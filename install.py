#!/usr/bin/env python
"""Install, update or remove specloop in a project.

    python install.py [TARGET] [options]

TARGET defaults to the current directory. Re-running the installer updates the kit:
engine, launcher, defaults and shipped phase prompts are replaced, while the project's
config.json and overrides/ are kept. Hooks are merged into the existing Claude Code
settings file without touching any other settings or hooks.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kit")
BASE_DIR = os.path.join(".claude", "specloop")
HOOK_MARKER = ".claude/specloop/run.sh"
RUN = 'sh "$CLAUDE_PROJECT_DIR/.claude/specloop/run.sh"'
SHIPPED = ["engine.py", "run.sh", "defaults.json", "phases"]
GITIGNORE_LINES = [".claude/specloop/state.json", ".claude/specloop/run/"]
GITIGNORE_HEADER = "# specloop runtime state"
NO_TESTS_HINT = "No test command detected; set phases.implement.check in .claude/specloop/config.json."


def hook_entries(stop_timeout):
    def command(args, timeout=None):
        hook = {"type": "command", "command": "%s %s" % (RUN, args)}
        if timeout:
            hook["timeout"] = timeout
        return hook

    return {
        "SessionStart": [{"hooks": [command("context SessionStart")]}],
        "UserPromptSubmit": [{"hooks": [command("context UserPromptSubmit")]}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash", "hooks": [command("pretool")]}],
        "Stop": [{"hooks": [command("stop", stop_timeout)]}],
    }


def detect_test_command(target):
    def exists(*names):
        return any(glob.glob(os.path.join(target, n)) for n in names)

    if exists("gradlew"):
        return "./gradlew test -q"
    if exists("build.gradle", "build.gradle.kts"):
        return "gradle test -q"
    if exists("mvnw"):
        return "./mvnw -q test"
    if exists("pom.xml"):
        return "mvn -q test"
    if exists("package.json"):
        try:
            with open(os.path.join(target, "package.json"), encoding="utf-8") as f:
                if json.load(f).get("scripts", {}).get("test"):
                    return "npm test --silent"
        except ValueError:
            pass
    if exists("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        return "python -m pytest -q"
    if exists("go.mod"):
        return "go test ./..."
    if exists("Cargo.toml"):
        return "cargo test -q"
    if exists("*.sln", "*.csproj"):
        return "dotnet test"
    return None


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def is_specloop_group(group):
    return any(HOOK_MARKER in h.get("command", "") for h in group.get("hooks", []))


def strip_hooks(settings):
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_specloop_group(g)]
        if not hooks[event]:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    return settings


def merge_hooks(settings, stop_timeout):
    strip_hooks(settings)
    hooks = settings.setdefault("hooks", {})
    for event, groups in hook_entries(stop_timeout).items():
        hooks.setdefault(event, []).extend(groups)
    return settings


def settings_files(target):
    return [os.path.join(target, ".claude", n) for n in ("settings.json", "settings.local.json")]


def copy_kit(target):
    dest = os.path.join(target, BASE_DIR)
    os.makedirs(dest, exist_ok=True)
    for name in SHIPPED:
        src, dst = os.path.join(KIT, "specloop", name), os.path.join(dest, name)
        if os.path.isdir(src):
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(dest, "overrides"), exist_ok=True)
    shutil.copy2(os.path.join(os.path.dirname(KIT), "VERSION"), os.path.join(dest, "VERSION"))


def write_config(target, test_command, prefix, force):
    path = os.path.join(target, BASE_DIR, "config.json")
    config = {} if force else read_json(path)
    if prefix is not None or "commandPrefix" not in config:
        config["commandPrefix"] = prefix if prefix is not None else "sl-"
    if test_command is not None or force or not os.path.exists(path):
        command = test_command if test_command is not None else detect_test_command(target)
        implement = config.setdefault("phases", {}).setdefault("implement", {})
        implement["check"] = {"type": "command", "command": command} if command else None
        if not command:
            print("! " + NO_TESTS_HINT)
    write_json(path, config)
    return config


def manifest_path(target):
    return os.path.join(target, BASE_DIR, "manifest.json")


def remove_commands(target):
    for rel in read_json(manifest_path(target)).get("commands", []):
        path = os.path.join(target, rel)
        if os.path.exists(path):
            os.remove(path)


def install_commands(target, prefix):
    remove_commands(target)
    dest = os.path.join(target, ".claude", "commands")
    os.makedirs(dest, exist_ok=True)
    installed = []
    for src in sorted(glob.glob(os.path.join(KIT, "commands", "*.md"))):
        name = prefix + os.path.basename(src)
        with open(src, encoding="utf-8") as f:
            text = f.read().replace("{{prefix}}", prefix)
        with open(os.path.join(dest, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        installed.append(".claude/commands/" + name)
    return installed


def git_ignored(target, rel):
    try:
        return subprocess.run(["git", "check-ignore", "-q", rel], cwd=target,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def update_gitignore(target):
    missing = [line for line in GITIGNORE_LINES if not git_ignored(target, line)]
    path = os.path.join(target, ".gitignore")
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    missing = [line for line in missing if line not in existing.splitlines()]
    if not missing:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n%s\n%s\n" % (GITIGNORE_HEADER, "\n".join(missing)))


def install(target, scope="project", prefix=None, test_command=None, force=False, gitignore=True):
    copy_kit(target)
    config = write_config(target, test_command, prefix, force)
    prefix = config["commandPrefix"]
    commands = install_commands(target, prefix)
    defaults = read_json(os.path.join(target, BASE_DIR, "defaults.json"))
    stop_timeout = int(config.get("checkTimeoutSec", defaults.get("checkTimeoutSec", 900))) + 60
    chosen = settings_files(target)[0 if scope == "project" else 1]
    for path in settings_files(target):
        if path != chosen and os.path.exists(path):
            write_json(path, strip_hooks(read_json(path)))
    write_json(chosen, merge_hooks(read_json(chosen), stop_timeout))
    write_json(manifest_path(target), {"commands": commands, "settings": os.path.relpath(chosen, target)})
    if gitignore:
        update_gitignore(target)
    return prefix


def uninstall(target, keep_config=False):
    remove_commands(target)
    for path in settings_files(target):
        if os.path.exists(path):
            write_json(path, strip_hooks(read_json(path)))
    dest = os.path.join(target, BASE_DIR)
    if not os.path.isdir(dest):
        return
    if not keep_config:
        shutil.rmtree(dest)
        return
    for name in os.listdir(dest):
        if name not in ("config.json", "overrides"):
            path = os.path.join(dest, name)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)


def main(argv):
    parser = argparse.ArgumentParser(description="Install, update or remove specloop in a project.")
    parser.add_argument("target", nargs="?", default=".", help="project directory (default: current)")
    parser.add_argument("--scope", choices=["project", "local"], default="project",
                        help="write hooks to .claude/settings.json (project, shared via git) "
                             "or .claude/settings.local.json (local, personal)")
    parser.add_argument("--prefix", help="slash command prefix (default: sl-, use '' for none)")
    parser.add_argument("--test-command", help="command that must pass to leave the implement phase")
    parser.add_argument("--force", action="store_true", help="recreate config.json from scratch")
    parser.add_argument("--no-gitignore", action="store_true", help="do not touch .gitignore")
    parser.add_argument("--uninstall", action="store_true", help="remove specloop from the project")
    parser.add_argument("--keep-config", action="store_true", help="with --uninstall: keep config.json and overrides/")
    args = parser.parse_args(argv)

    target = os.path.abspath(args.target)
    if not os.path.isdir(target):
        print("Target directory does not exist: " + target)
        return 1
    if args.uninstall:
        uninstall(target, args.keep_config)
        print("specloop removed from " + target)
        return 0
    if not os.path.isdir(os.path.join(target, ".git")):
        print("! %s is not a git repository root; the review phase relies on git diff." % target)
    prefix = install(target, args.scope, args.prefix, args.test_command, args.force, not args.no_gitignore)
    with open(os.path.join(os.path.dirname(KIT), "VERSION")) as f:
        version = f.read().strip()
    print("specloop %s installed into %s" % (version, target))
    print("Start a new Claude Code session there, then: /%sspec <file> and /%sgo" % (prefix, prefix))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
