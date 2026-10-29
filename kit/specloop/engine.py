#!/usr/bin/env python
"""specloop: a spec-driven development state machine for Claude Code, driven by hooks.

Hook entry points (read hook JSON from stdin):
    engine.py context <SessionStart|UserPromptSubmit>
    engine.py pretool
    engine.py stop

Manual commands (used by slash commands):
    engine.py init <spec-path>   load a specification, status -> ready
    engine.py go                 start (or resume) the workflow
    engine.py pause              stop driving the session, keep the phase
    engine.py advance            force-pass the current phase
    engine.py status             print the current state
    engine.py reset              delete state and run artifacts

Configuration is layered: defaults.json (shipped with the kit, replaced on update)
is deep-merged with config.json (owned by the project, never touched by updates).
Phase instructions are read from overrides/<phase>.md first, then phases/<phase>.md.
Set SPECLOOP_DISABLED=1 to turn every hook into a no-op.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.join(".claude", "specloop")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DONE = "done"
CHECKLIST_ITEM = re.compile(r"^\s*- \[[ x]\]", re.IGNORECASE | re.MULTILINE)


def project_dir(hook_input=None):
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        return os.environ["CLAUDE_PROJECT_DIR"]
    if hook_input and hook_input.get("cwd"):
        return hook_input["cwd"]
    return os.getcwd()


def _path(root, *parts):
    return os.path.join(root, BASE_DIR, *parts)


def _rel(*parts):
    return "/".join((BASE_DIR.replace("\\", "/"),) + parts)


def deep_merge(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def load_config(root):
    config = deep_merge(_read_json(_path(root, "defaults.json")), _read_json(_path(root, "config.json")))
    # Phases explicitly set to null in config.json are removed.
    config["phases"] = {k: v for k, v in config.get("phases", {}).items() if v}
    return config


def load_state(root):
    try:
        with open(_path(root, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_state(root, state):
    with open(_path(root, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def run_dir(root):
    return _path(root, "run")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_name(config, name):
    return "/" + config.get("commandPrefix", "") + name


def phase_instructions(root, config, state):
    phase = state["phase"]
    if phase == DONE:
        return "The workflow is complete."
    names = []
    custom = config["phases"].get(phase, {}).get("instructions")
    if custom:
        names.append(custom)
    names += [os.path.join("overrides", phase + ".md"), os.path.join("phases", phase + ".md")]
    text = "(no instructions file for phase '%s')" % phase
    for name in names:
        path = name if os.path.isabs(name) else _path(root, name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            break
    return text.replace("{spec}", state.get("spec", "")).replace("{run_dir}", _rel("run"))


def header(state):
    return "[specloop] phase=%s, iteration %d, spec=%s" % (
        state["phase"], state.get("iter", 0), state.get("spec", "?"))


def relative_to_project(root, path):
    if not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(root, path)
    try:
        rel = os.path.relpath(os.path.normcase(os.path.abspath(full)), os.path.normcase(os.path.abspath(root)))
    except ValueError:
        return None
    rel = rel.replace("\\", "/")
    return None if rel.startswith("../") or rel == ".." else rel


def resolve_shell(config):
    shell = config.get("shell", "bash")
    if os.name == "nt" and shell == "bash":
        # Prefer Git Bash over the WSL launcher in System32.
        git = shutil.which("git")
        if git:
            candidate = os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe")
            if os.path.exists(candidate):
                return candidate
    return shutil.which(shell) or shutil.which("sh") or shell


def tail(text, limit):
    return text if len(text) <= limit else "...(truncated)...\n" + text[-limit:]


# --- checks --------------------------------------------------------------------

def check_phase(root, config, phase):
    """Return (result, details) where result is 'pass', 'fail' or 'reject'."""
    check = config["phases"][phase].get("check")
    if not check:
        return "pass", "no check configured"
    kind = check["type"]
    limit = config.get("maxOutputChars", 4000)
    if kind == "file":
        path = os.path.join(root, check["path"])
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return "pass", "%s exists" % check["path"]
        return "fail", "Required file %s is missing or empty." % check["path"]
    if kind == "command":
        try:
            proc = subprocess.run(
                [resolve_shell(config), "-c", check["command"]],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=config.get("checkTimeoutSec", 900))
        except subprocess.TimeoutExpired:
            return "fail", "Check command timed out: %s" % check["command"]
        output = proc.stdout.decode("utf-8", errors="replace")
        if proc.returncode == 0:
            return "pass", "`%s` succeeded" % check["command"]
        return "fail", "`%s` exited with %d:\n%s" % (check["command"], proc.returncode, tail(output, limit))
    if kind == "verdict":
        path = os.path.join(root, check["path"])
        if not os.path.exists(path):
            return "fail", "Review file %s is missing." % check["path"]
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if check["approve"] in text:
            return "pass", "review approved"
        if check["reject"] in text:
            return "reject", tail(text, limit)
        return "fail", "%s must contain '%s' or '%s'." % (check["path"], check["approve"], check["reject"])
    return "fail", "Unknown check type '%s'." % kind


def size_limit(config, key):
    """Return a largeSpecWarning limit, or None when the block or the key is disabled or not an integer."""
    limits = config.get("largeSpecWarning")
    value = limits.get(key) if isinstance(limits, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def spec_size_warning(root, config, spec):
    """Return a warning when the spec is too big for single mode, else None."""
    max_lines, max_words = size_limit(config, "maxLines"), size_limit(config, "maxWords")
    if max_lines is None and max_words is None:
        return None
    try:
        with open(os.path.join(root, spec), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    lines, words = len(text.splitlines()), len(text.split())
    if not ((max_lines is not None and lines > max_lines) or (max_words is not None and words > max_words)):
        return None
    limits = " / ".join(s for s in (
        "%d lines" % max_lines if max_lines is not None else None,
        "%d words" % max_words if max_words is not None else None) if s)
    return ("Warning: the spec has %d lines and %d words, above the single-mode limit of %s. "
            "It may be too big for one plan -> implement -> review cycle; "
            "consider %s for multi-commit features." % (lines, words, limits, cmd_name(config, "feature")))


def count_plan_items(root):
    try:
        with open(os.path.join(run_dir(root), "plan.md"), encoding="utf-8", errors="replace") as f:
            return len(CHECKLIST_ITEM.findall(f.read()))
    except OSError:
        return 0


def archive_review(root, config, phase, state):
    src = os.path.join(root, config["phases"][phase]["check"]["path"])
    if os.path.exists(src):
        base, ext = os.path.splitext(src)
        os.replace(src, "%s-%d%s" % (base, state.get("iter", 0), ext))


# --- hook handlers ---------------------------------------------------------------

def handle_stop(root, hook_input):
    state = load_state(root)
    if not state or state.get("status") != "active":
        return None
    config = load_config(root)
    phase = state["phase"]
    result, details = check_phase(root, config, phase)
    state["iter"] = state.get("iter", 0) + 1
    state.setdefault("history", []).append(
        {"phase": phase, "result": result, "at": now(), "iter": state["iter"]})

    if result == "pass":
        next_phase = config["phases"][phase].get("next", DONE)
        state["phase"] = next_phase
        state["lastFailure"] = None
        if next_phase == DONE:
            state["status"] = "done"
            save_state(root, state)
            return {"systemMessage": "[specloop] all phases passed - done."}
        max_items = size_limit(config, "maxPlanItems")
        if phase == "plan" and max_items is not None and not state.get("largePlanAcknowledged"):
            items = count_plan_items(root)
            if items > max_items:
                state["status"] = "paused"
                state["largePlanItems"] = items
                save_state(root, state)
                return {"systemMessage": (
                    "[specloop] the plan has %d checklist items, above the single-mode limit of %d. "
                    "Paused before phase '%s'. Run %s and then %s to split the work into commits, "
                    "or %s to continue anyway." % (
                        items, max_items, next_phase, cmd_name(config, "reset"),
                        cmd_name(config, "feature"), cmd_name(config, "go")))}
        save_state(root, state)
        return {"decision": "block", "reason": "Phase '%s' passed (%s). Now entering phase '%s'.\n\n%s" % (
            phase, details, next_phase, phase_instructions(root, config, state))}

    if result == "reject":
        archive_review(root, config, phase, state)
        state["phase"] = config["phases"][phase]["check"].get("onReject", "implement")
        state["lastFailure"] = "review rejected"
        reason = "Review requested changes. Back to phase '%s'. Address every point below:\n\n%s\n\n%s" % (
            state["phase"], details, phase_instructions(root, config, state))
    else:
        state["lastFailure"] = tail(details, 500)
        reason = "Phase '%s' is not complete yet. %s\n\nKeep working on phase '%s'.\n\n%s" % (
            phase, details, phase, phase_instructions(root, config, state))

    if state["iter"] >= config.get("maxIterations", 15):
        state["status"] = "stalled"
        save_state(root, state)
        return {"systemMessage": "[specloop] stalled after %d iterations in phase '%s'. "
                                 "Inspect, then run %s to resume or %s to skip the phase." % (
                                     state["iter"], phase, cmd_name(config, "go"), cmd_name(config, "advance"))}
    save_state(root, state)
    return {"decision": "block", "reason": reason}


def deny(reason):
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "[specloop] " + reason}}


def handle_pretool(root, hook_input):
    state = load_state(root)
    if not state or state.get("status") != "active":
        return None
    config = load_config(root)
    tool = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input") or {}
    phase = state["phase"]

    if tool in EDIT_TOOLS:
        rel = relative_to_project(root, tool_input.get("file_path") or tool_input.get("notebook_path"))
        protected = [_rel(n) for n in ("state.json", "config.json", "defaults.json", "engine.py")]
        if rel in protected:
            return deny("%s is managed by specloop; use the %s* commands." % (rel, cmd_name(config, "")))
        allowed = config["phases"].get(phase, {}).get("allowEdits")
        if allowed is not None and (rel is None or not any(rel.startswith(p) for p in allowed)):
            return deny("phase '%s' only allows editing %s, not %s." % (
                phase, ", ".join(allowed), rel or "a path outside the project"))

    if tool == "Bash":
        command = tool_input.get("command", "")
        for blocked in config.get("blockedCommandsUntilDone", []):
            if blocked in command:
                return deny("'%s' is not allowed until the workflow is done (phase '%s')." % (blocked, phase))
    return None


def handle_context(root, event):
    state = load_state(root)
    if not state:
        return None
    config = load_config(root)
    status = state.get("status")
    if status == "active":
        text = header(state)
        if event == "SessionStart" or state.get("iter", 0) == 0:
            text += "\n\n" + phase_instructions(root, config, state)
        elif state.get("lastFailure"):
            text += "\nLast check failure: " + state["lastFailure"]
    elif status == "ready":
        text = "[specloop] spec %s is loaded; development starts when the user runs %s." % (
            state.get("spec"), cmd_name(config, "go"))
    else:
        return None
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


# --- manual commands ---------------------------------------------------------------

def cmd_init(root, spec):
    config = load_config(root)
    if not spec or not os.path.exists(os.path.join(root, spec)):
        return "Spec file not found: %r" % spec
    # Stale plan.md / review.md from an earlier run would pass the new run's gates.
    if os.path.isdir(run_dir(root)):
        shutil.rmtree(run_dir(root))
    os.makedirs(run_dir(root))
    save_state(root, {"status": "ready", "phase": config.get("initialPhase", "plan"),
                      "spec": spec.replace("\\", "/"), "iter": 0, "history": [], "createdAt": now()})
    out = "Spec loaded: %s. Run %s to start development." % (spec, cmd_name(config, "go"))
    warning = spec_size_warning(root, config, spec)
    return out + "\n\n" + warning if warning else out


def cmd_go(root):
    config = load_config(root)
    state = load_state(root)
    if not state:
        return "No spec loaded. Run %s <path> first." % cmd_name(config, "spec")
    if state["status"] == "done":
        return "Workflow already done. Run %s and %s to start a new one." % (
            cmd_name(config, "reset"), cmd_name(config, "spec"))
    if state["status"] == "ready" and not state.get("largeSpecAcknowledged"):
        warning = spec_size_warning(root, config, state.get("spec", ""))
        if warning and not state.get("largeSpecWarned"):
            state["largeSpecWarned"] = True
            save_state(root, state)
            return ("%s\n\nThe workflow has not started. Do not plan or implement; show this warning to the user "
                    "and wait. Running %s again proceeds in single mode anyway." % (warning, cmd_name(config, "go")))
        if warning:
            state["largeSpecAcknowledged"] = True
    if state["status"] == "stalled":
        state["iter"] = 0
    if state["status"] == "paused" and state.get("largePlanItems") and not state.get("largePlanAcknowledged"):
        state["largePlanAcknowledged"] = True
    state["status"] = "active"
    save_state(root, state)
    return header(state) + "\n\n" + phase_instructions(root, config, state)


def cmd_pause(root):
    state = load_state(root)
    if not state:
        return "Nothing to pause."
    state["status"] = "paused"
    save_state(root, state)
    return "Workflow paused at phase '%s'. Run %s to resume." % (state["phase"], cmd_name(load_config(root), "go"))


def cmd_advance(root):
    state = load_state(root)
    if not state or state["phase"] == DONE:
        return "Nothing to advance."
    config = load_config(root)
    state.setdefault("history", []).append({"phase": state["phase"], "result": "skipped", "at": now()})
    state["phase"] = config["phases"][state["phase"]].get("next", DONE)
    if state["phase"] == DONE:
        state["status"] = "done"
    save_state(root, state)
    return "Advanced to phase '%s'." % state["phase"]


def cmd_status(root):
    state = load_state(root)
    return json.dumps(state, indent=2) if state else "No workflow state."


def cmd_reset(root):
    for target in (_path(root, "state.json"), run_dir(root)):
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.exists(target):
            os.remove(target)
    return "Workflow state reset."


def read_hook_input():
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


def run_hook(command, args):
    if os.environ.get("SPECLOOP_DISABLED") == "1":
        return None
    hook_input = read_hook_input()
    root = project_dir(hook_input)
    if not os.path.exists(_path(root, "defaults.json")):
        return None
    if command == "stop":
        return handle_stop(root, hook_input)
    if command == "pretool":
        return handle_pretool(root, hook_input)
    return handle_context(root, args[0] if args else "UserPromptSubmit")


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    command, args = argv[0], argv[1:]
    if command in ("stop", "pretool", "context"):
        try:
            out = run_hook(command, args)
        except Exception as e:  # A broken hook must never break the user's session.
            print("[specloop] hook error: %s" % e, file=sys.stderr)
            return 0
        if out:
            print(json.dumps(out))
        return 0
    root = project_dir()
    handlers = {
        "init": lambda: cmd_init(root, " ".join(args).strip().strip('"')),
        "go": lambda: cmd_go(root),
        "pause": lambda: cmd_pause(root),
        "advance": lambda: cmd_advance(root),
        "status": lambda: cmd_status(root),
        "reset": lambda: cmd_reset(root),
    }
    if command not in handlers:
        print("Unknown command: " + command)
        return 1
    print(handlers[command]())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
