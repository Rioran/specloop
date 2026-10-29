import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import install  # noqa: E402


class InstallTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def read_json(self, *parts):
        with open(self.path(*parts), encoding="utf-8") as f:
            return json.load(f)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(self.path(rel)) or self.root, exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            f.write(text)

    def specloop_groups(self, settings, event):
        return [g for g in settings.get("hooks", {}).get(event, []) if install.is_specloop_group(g)]

    def test_fresh_install_lays_out_kit_commands_and_hooks(self):
        install.install(self.root, test_command="make test")
        for name in ("engine.py", "run.sh", "defaults.json", "VERSION", "phases/plan.md", "config.json"):
            self.assertTrue(os.path.exists(self.path(".claude", "specloop", name)), name)
        self.assertTrue(os.path.isdir(self.path(".claude", "specloop", "overrides")))
        self.assertTrue(os.path.exists(self.path(".claude", "commands", "sl-go.md")))
        with open(self.path(".claude", "commands", "sl-spec.md"), encoding="utf-8") as f:
            self.assertIn("/sl-go", f.read())
        settings = self.read_json(".claude", "settings.json")
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"):
            self.assertEqual(1, len(self.specloop_groups(settings, event)), event)
        self.assertEqual(960, settings["hooks"]["Stop"][0]["hooks"][0]["timeout"])
        config = self.read_json(".claude", "specloop", "config.json")
        self.assertEqual("make test", config["phases"]["implement"]["check"]["command"])

    def test_install_preserves_foreign_settings_and_is_idempotent(self):
        foreign = {"permissions": {"allow": ["Bash(ls)"]},
                   "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
        self.write(".claude/settings.json", json.dumps(foreign))
        install.install(self.root, test_command="true")
        install.install(self.root, test_command="true")
        settings = self.read_json(".claude", "settings.json")
        self.assertEqual(["Bash(ls)"], settings["permissions"]["allow"])
        self.assertEqual(2, len(settings["hooks"]["Stop"]))
        self.assertEqual(1, len(self.specloop_groups(settings, "Stop")))

    def test_update_keeps_project_config_and_overrides(self):
        install.install(self.root, test_command="first")
        self.write(".claude/specloop/overrides/plan.md", "mine")
        config = self.read_json(".claude", "specloop", "config.json")
        config["maxIterations"] = 3
        self.write(".claude/specloop/config.json", json.dumps(config))
        install.install(self.root)
        config = self.read_json(".claude", "specloop", "config.json")
        self.assertEqual(3, config["maxIterations"])
        self.assertEqual("first", config["phases"]["implement"]["check"]["command"])
        with open(self.path(".claude", "specloop", "overrides", "plan.md")) as f:
            self.assertEqual("mine", f.read())

    def test_explicit_test_command_updates_existing_config(self):
        install.install(self.root, test_command="first")
        install.install(self.root, test_command="second")
        config = self.read_json(".claude", "specloop", "config.json")
        self.assertEqual("second", config["phases"]["implement"]["check"]["command"])

    def test_prefix_change_replaces_previous_commands(self):
        install.install(self.root, test_command="true")
        install.install(self.root, prefix="")
        commands = sorted(os.listdir(self.path(".claude", "commands")))
        self.assertIn("go.md", commands)
        self.assertNotIn("sl-go.md", commands)
        self.assertEqual("", self.read_json(".claude", "specloop", "config.json")["commandPrefix"])

    def test_local_scope_moves_hooks_out_of_project_settings(self):
        install.install(self.root, test_command="true")
        install.install(self.root, scope="local")
        self.assertEqual([], self.specloop_groups(self.read_json(".claude", "settings.json"), "Stop"))
        local = self.read_json(".claude", "settings.local.json")
        self.assertEqual(1, len(self.specloop_groups(local, "Stop")))

    def test_gitignore_entries_added_once(self):
        self.write(".gitignore", "node_modules/")
        install.install(self.root, test_command="true")
        install.install(self.root, test_command="true")
        with open(self.path(".gitignore")) as f:
            text = f.read()
        self.assertTrue(text.startswith("node_modules/\n"))
        self.assertEqual(1, text.count(".claude/specloop/state.json"))

    def test_uninstall_removes_everything_but_foreign_settings(self):
        self.write(".claude/settings.json", json.dumps({"model": "opus"}))
        self.write(".claude/commands/mine.md", "x")
        install.install(self.root, test_command="true")
        install.uninstall(self.root)
        self.assertFalse(os.path.exists(self.path(".claude", "specloop")))
        self.assertEqual(["mine.md"], os.listdir(self.path(".claude", "commands")))
        self.assertEqual({"model": "opus"}, self.read_json(".claude", "settings.json"))

    def test_uninstall_keep_config(self):
        install.install(self.root, test_command="true")
        install.uninstall(self.root, keep_config=True)
        self.assertEqual(["config.json", "overrides"], sorted(os.listdir(self.path(".claude", "specloop"))))

    def test_detects_test_commands(self):
        cases = [("gradlew", "", "./gradlew test -q"),
                 ("pom.xml", "", "mvn -q test"),
                 ("package.json", '{"scripts": {"test": "jest"}}', "npm test --silent"),
                 ("pyproject.toml", "", "python -m pytest -q"),
                 ("go.mod", "", "go test ./..."),
                 ("Cargo.toml", "", "cargo test -q"),
                 ("App.csproj", "", "dotnet test")]
        for name, content, expected in cases:
            root = tempfile.mkdtemp()
            try:
                with open(os.path.join(root, name), "w") as f:
                    f.write(content)
                self.assertEqual(expected, install.detect_test_command(root), name)
            finally:
                shutil.rmtree(root)

    def test_no_detected_tests_leaves_implement_unchecked(self):
        install.install(self.root)
        config = self.read_json(".claude", "specloop", "config.json")
        self.assertIsNone(config["phases"]["implement"]["check"])

    def test_cli_rejects_missing_target(self):
        self.assertEqual(1, install.main([self.path("missing")]))


if __name__ == "__main__":
    unittest.main()
