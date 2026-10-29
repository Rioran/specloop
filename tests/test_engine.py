import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(os.path.dirname(HERE), "kit", "specloop")
sys.path.insert(0, KIT)

import engine as workflow  # noqa: E402


class EngineTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.base = os.path.join(self.root, ".claude", "specloop")
        shutil.copytree(KIT, self.base)
        self.set_test_command("exit 0")
        with open(os.path.join(self.root, "spec.md"), "w") as f:
            f.write("# Spec\nDo the thing.")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def set_config(self, config):
        with open(os.path.join(self.base, "config.json"), "w") as f:
            json.dump(config, f)

    def set_test_command(self, command, max_iterations=15):
        self.set_config({"maxIterations": max_iterations,
                         "phases": {"implement": {"check": {"type": "command", "command": command}}}})

    def write_run(self, name, text):
        with open(os.path.join(self.base, "run", name), "w") as f:
            f.write(text)

    def write_override(self, phase, text):
        os.makedirs(os.path.join(self.base, "overrides"), exist_ok=True)
        with open(os.path.join(self.base, "overrides", phase + ".md"), "w") as f:
            f.write(text)

    def start(self):
        workflow.cmd_init(self.root, "spec.md")
        workflow.cmd_go(self.root)

    def state(self):
        return workflow.load_state(self.root)

    def test_hooks_are_inert_without_state(self):
        self.assertIsNone(workflow.handle_stop(self.root, {}))
        self.assertIsNone(workflow.handle_pretool(self.root, {"tool_name": "Edit", "tool_input": {"file_path": "x"}}))
        self.assertIsNone(workflow.handle_context(self.root, "SessionStart"))

    def test_ready_state_does_not_block_stop(self):
        workflow.cmd_init(self.root, "spec.md")
        self.assertIsNone(workflow.handle_stop(self.root, {}))
        self.assertIn("/sl-go", workflow.handle_context(self.root, "UserPromptSubmit")
                      ["hookSpecificOutput"]["additionalContext"])

    def test_init_rejects_missing_spec(self):
        self.assertIn("not found", workflow.cmd_init(self.root, "missing.md"))
        self.assertIsNone(self.state())

    def test_plan_blocks_until_plan_file_written(self):
        self.start()
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertIn("plan.md", out["reason"])
        self.assertEqual("plan", self.state()["phase"])

    def test_full_happy_path(self):
        self.start()
        self.write_run("plan.md", "- [ ] step")
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertIn("entering phase 'implement'", out["reason"])

        out = workflow.handle_stop(self.root, {})
        self.assertIn("entering phase 'review'", out["reason"])

        self.write_run("review.md", "Looks good.\nVERDICT: APPROVE")
        out = workflow.handle_stop(self.root, {})
        self.assertNotIn("decision", out)
        self.assertEqual("done", self.state()["status"])
        self.assertIsNone(workflow.handle_stop(self.root, {}))

    def test_failing_tests_keep_implement_phase_with_output(self):
        self.set_test_command("echo boom-output; exit 3")
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertIn("boom-output", out["reason"])
        self.assertIn("exited with 3", out["reason"])
        self.assertEqual("implement", self.state()["phase"])

    def test_rejected_review_returns_to_implement_and_archives(self):
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        workflow.handle_stop(self.root, {})
        self.write_run("review.md", "Fix null check.\nVERDICT: CHANGES_REQUESTED")
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertIn("Fix null check", out["reason"])
        self.assertEqual("implement", self.state()["phase"])
        run = os.listdir(os.path.join(self.root, ".claude", "specloop", "run"))
        self.assertNotIn("review.md", run)
        self.assertTrue(any(n.startswith("review-") for n in run))

    def test_review_without_verdict_blocks(self):
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        workflow.handle_stop(self.root, {})
        self.write_run("review.md", "Hmm.")
        out = workflow.handle_stop(self.root, {})
        self.assertIn("must contain", out["reason"])
        self.assertEqual("review", self.state()["phase"])

    def test_stalls_after_max_iterations_and_go_resumes(self):
        self.set_test_command("exit 0", max_iterations=2)
        self.start()
        self.assertEqual("block", workflow.handle_stop(self.root, {})["decision"])
        out = workflow.handle_stop(self.root, {})
        self.assertIn("stalled", out["systemMessage"])
        self.assertEqual("stalled", self.state()["status"])
        self.assertIsNone(workflow.handle_stop(self.root, {}))
        workflow.cmd_go(self.root)
        self.assertEqual("active", self.state()["status"])
        self.assertEqual(0, self.state()["iter"])

    def test_pause_disables_hooks(self):
        self.start()
        workflow.cmd_pause(self.root)
        self.assertIsNone(workflow.handle_stop(self.root, {}))
        self.assertIsNone(workflow.handle_pretool(self.root, {"tool_name": "Bash", "tool_input": {"command": "git push"}}))

    def test_plan_phase_denies_source_edits_but_allows_run_dir(self):
        self.start()
        src = os.path.join(self.root, "src", "Main.java")
        out = workflow.handle_pretool(self.root, {"tool_name": "Edit", "tool_input": {"file_path": src}})
        self.assertEqual("deny", out["hookSpecificOutput"]["permissionDecision"])
        plan = os.path.join(self.root, ".claude", "specloop", "run", "plan.md")
        self.assertIsNone(workflow.handle_pretool(self.root, {"tool_name": "Write", "tool_input": {"file_path": plan}}))

    def test_implement_phase_allows_source_edits_but_not_state(self):
        self.start()
        workflow.cmd_advance(self.root)
        src = os.path.join(self.root, "src", "Main.java")
        self.assertIsNone(workflow.handle_pretool(self.root, {"tool_name": "Edit", "tool_input": {"file_path": src}}))
        state = os.path.join(self.root, ".claude", "specloop", "state.json")
        out = workflow.handle_pretool(self.root, {"tool_name": "Write", "tool_input": {"file_path": state}})
        self.assertEqual("deny", out["hookSpecificOutput"]["permissionDecision"])

    def test_push_blocked_while_active(self):
        self.start()
        out = workflow.handle_pretool(self.root, {"tool_name": "Bash", "tool_input": {"command": "git add . && git push"}})
        self.assertEqual("deny", out["hookSpecificOutput"]["permissionDecision"])
        self.assertIsNone(workflow.handle_pretool(self.root, {"tool_name": "Bash", "tool_input": {"command": "git status"}}))

    def test_session_start_context_includes_phase_instructions(self):
        self.start()
        text = workflow.handle_context(self.root, "SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("phase=plan", text)
        self.assertIn("spec.md", text)
        self.assertIn(".claude/specloop/run/plan.md", text)

    def test_config_overrides_are_deep_merged_with_defaults(self):
        config = workflow.load_config(self.root)
        self.assertEqual("exit 0", config["phases"]["implement"]["check"]["command"])
        self.assertEqual("review", config["phases"]["implement"]["next"])
        self.assertEqual("verdict", config["phases"]["review"]["check"]["type"])

    def test_phase_set_to_null_is_removed_and_flow_skips_it(self):
        self.set_config({"phases": {"implement": {"next": "done"}, "review": None}})
        config = workflow.load_config(self.root)
        self.assertNotIn("review", config["phases"])
        self.start()
        workflow.cmd_advance(self.root)
        self.assertEqual("implement", self.state()["phase"])
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("done", self.state()["status"])
        self.assertIn("done", out["systemMessage"])

    def test_override_prompt_wins_over_shipped_phase(self):
        self.write_override("plan", "Custom plan for {spec}")
        self.start()
        text = workflow.handle_context(self.root, "SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Custom plan for spec.md", text)
        self.assertNotIn("## Phase: plan", text)

    def test_command_prefix_appears_in_messages(self):
        self.set_config({"commandPrefix": "x-"})
        self.assertIn("/x-go", workflow.cmd_init(self.root, "spec.md"))

    def test_disabled_env_makes_hooks_noop(self):
        self.start()
        os.environ["SPECLOOP_DISABLED"] = "1"
        try:
            self.assertIsNone(workflow.run_hook("stop", []))
        finally:
            del os.environ["SPECLOOP_DISABLED"]

    def test_hook_errors_never_fail_the_session(self):
        self.start()
        with open(os.path.join(self.base, "config.json"), "w") as f:
            f.write("{broken")
        os.environ["CLAUDE_PROJECT_DIR"] = self.root
        stdin = sys.stdin
        try:
            sys.stdin = open(os.devnull)
            self.assertEqual(0, workflow.main(["stop"]))
        finally:
            sys.stdin.close()
            sys.stdin = stdin
            del os.environ["CLAUDE_PROJECT_DIR"]

    def test_reset_removes_state(self):
        self.start()
        workflow.cmd_reset(self.root)
        self.assertIsNone(self.state())

    # --- "too big for single mode" safety net ---------------------------------

    def write_spec(self, text):
        with open(os.path.join(self.root, "spec.md"), "w") as f:
            f.write(text)

    def plan_items(self, count):
        marks = ["[ ]", "[x]", "[X]"]
        return "# Plan\n" + "".join("- %s step %d\n" % (marks[i % 3], i) for i in range(count))

    def test_large_spec_by_lines_warns_on_init_refuses_first_go_starts_on_second(self):
        self.write_spec("line\n" * 201)
        out = workflow.cmd_init(self.root, "spec.md")
        self.assertIn("201 lines", out)
        self.assertIn("200", out)
        self.assertIn("/sl-feature", out)

        out = workflow.cmd_go(self.root)
        self.assertIn("201 lines", out)
        self.assertIn("/sl-go", out)
        self.assertIn("again", out)
        self.assertIn("has not started", out)
        self.assertIn("Do not plan or implement", out)
        self.assertEqual("ready", self.state()["status"])
        self.assertIsNone(workflow.handle_stop(self.root, {}))

        out = workflow.cmd_go(self.root)
        self.assertEqual("active", self.state()["status"])
        self.assertTrue(self.state()["largeSpecAcknowledged"])
        self.assertIn("phase=plan", out)
        self.assertNotIn("201 lines", out)

    def test_large_spec_by_words_warns(self):
        self.write_spec("word " * 2001)
        out = workflow.cmd_init(self.root, "spec.md")
        self.assertIn("2001 words", out)
        self.assertIn("2000", out)
        workflow.cmd_go(self.root)
        self.assertEqual("ready", self.state()["status"])

    def test_spec_within_limits_starts_on_first_go(self):
        out = workflow.cmd_init(self.root, "spec.md")
        self.assertNotIn("/sl-feature", out)
        workflow.cmd_go(self.root)
        self.assertEqual("active", self.state()["status"])
        self.assertFalse(any(k.startswith("largeSpec") for k in self.state()))

    def test_large_spec_warning_block_null_disables_checks(self):
        self.set_config({"largeSpecWarning": None})
        self.write_spec("word\n" * 3000)
        self.assertNotIn("/sl-feature", workflow.cmd_init(self.root, "spec.md"))
        workflow.cmd_go(self.root)
        self.assertEqual("active", self.state()["status"])
        self.write_run("plan.md", self.plan_items(50))
        self.assertEqual("block", workflow.handle_stop(self.root, {})["decision"])
        self.assertEqual("implement", self.state()["phase"])

    def test_large_spec_single_key_null_disables_that_check(self):
        self.set_config({"largeSpecWarning": {"maxLines": None}})
        self.write_spec("word\n" * 300)
        self.assertNotIn("/sl-feature", workflow.cmd_init(self.root, "spec.md"))
        self.write_spec("word " * 2001)
        self.assertIn("2001 words", workflow.cmd_init(self.root, "spec.md"))

    def test_non_integer_limits_disable_the_check(self):
        self.set_config({"largeSpecWarning": {"maxLines": False, "maxWords": "many", "maxPlanItems": True}})
        self.write_spec("word\n" * 3000)
        self.assertNotIn("/sl-feature", workflow.cmd_init(self.root, "spec.md"))
        workflow.cmd_go(self.root)
        self.write_run("plan.md", self.plan_items(13))
        self.assertEqual("block", workflow.handle_stop(self.root, {})["decision"])
        self.assertEqual("active", self.state()["status"])

    def test_resumed_run_never_warns_about_spec_size(self):
        self.write_spec("line\n" * 201)
        workflow.cmd_init(self.root, "spec.md")
        workflow.cmd_go(self.root)
        workflow.cmd_go(self.root)
        workflow.cmd_pause(self.root)
        out = workflow.cmd_go(self.root)
        self.assertNotIn("201 lines", out)
        self.assertEqual("active", self.state()["status"])

    def test_large_spec_warning_uses_command_prefix(self):
        self.set_config({"commandPrefix": "x-"})
        self.write_spec("line\n" * 201)
        self.assertIn("/x-feature", workflow.cmd_init(self.root, "spec.md"))
        self.assertIn("/x-go", workflow.cmd_go(self.root))

    def test_plan_over_limit_pauses_then_go_continues_in_implement(self):
        self.start()
        self.write_run("plan.md", self.plan_items(13))
        out = workflow.handle_stop(self.root, {})
        self.assertNotIn("decision", out)
        self.assertIn("13", out["systemMessage"])
        for name in ("/sl-reset", "/sl-feature", "/sl-go"):
            self.assertIn(name, out["systemMessage"])
        self.assertEqual("paused", self.state()["status"])
        self.assertEqual("implement", self.state()["phase"])
        self.assertIsNone(workflow.handle_stop(self.root, {}))

        out = workflow.cmd_go(self.root)
        self.assertIn("phase=implement", out)
        self.assertTrue(self.state()["largePlanAcknowledged"])
        self.assertEqual("active", self.state()["status"])
        self.assertIn("entering phase 'review'", workflow.handle_stop(self.root, {})["reason"])

    def test_plan_at_limit_does_not_pause(self):
        self.start()
        self.write_run("plan.md", self.plan_items(12))
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertEqual("active", self.state()["status"])
        self.assertEqual("implement", self.state()["phase"])

    def test_plan_items_single_key_null_disables_pause(self):
        self.set_config({"largeSpecWarning": {"maxPlanItems": None}})
        self.start()
        self.write_run("plan.md", self.plan_items(13))
        self.assertEqual("block", workflow.handle_stop(self.root, {})["decision"])
        self.assertEqual("active", self.state()["status"])

    def test_init_removes_stale_run_directory(self):
        self.start()
        self.write_run("plan.md", "old plan")
        self.write_run("review.md", "VERDICT: APPROVE")
        workflow.cmd_init(self.root, "spec.md")
        self.assertEqual([], os.listdir(os.path.join(self.base, "run")))
        workflow.cmd_go(self.root)
        self.assertEqual("block", workflow.handle_stop(self.root, {})["decision"])
        self.assertEqual("plan", self.state()["phase"])

    def test_init_with_missing_spec_keeps_run_directory(self):
        self.start()
        self.write_run("plan.md", "plan")
        workflow.cmd_init(self.root, "missing.md")
        self.assertTrue(os.path.exists(os.path.join(self.base, "run", "plan.md")))


if __name__ == "__main__":
    unittest.main()
