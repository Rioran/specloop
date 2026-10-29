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

    # --- steps check type ----------------------------------------------------------

    def use_steps_check(self, require_all_pending=True, **extra):
        check = {"type": "steps", "path": ".claude/specloop/run/steps.json"}
        if require_all_pending is not None:
            check["requireAllPending"] = require_all_pending
        config = {"phases": {"plan": {"check": check}, "implement": {"check": None}}}
        config.update(extra)
        self.set_config(config)
        self.start()

    def test_steps_check_passes_and_enters_next_phase(self):
        self.use_steps_check()
        self.write_run("steps.json", json.dumps(valid_steps()))
        out = workflow.handle_stop(self.root, {})
        self.assertEqual("block", out["decision"])
        self.assertIn("entering phase 'implement'", out["reason"])

    def test_steps_check_fails_when_missing(self):
        self.use_steps_check()
        out = workflow.handle_stop(self.root, {})
        self.assertIn("steps.json", out["reason"])
        self.assertIn("missing", out["reason"])
        self.assertEqual("plan", self.state()["phase"])

    def test_steps_check_fails_on_invalid_json(self):
        self.use_steps_check()
        self.write_run("steps.json", "{not json")
        out = workflow.handle_stop(self.root, {})
        self.assertIn("not valid JSON", out["reason"])
        self.assertEqual("plan", self.state()["phase"])

    def test_steps_check_fails_on_schema_errors_and_lists_all(self):
        self.use_steps_check()
        data = valid_steps()
        data["feature"] = ""
        data["steps"][1]["title"] = ""
        self.write_run("steps.json", json.dumps(data))
        out = workflow.handle_stop(self.root, {})
        self.assertIn("feature", out["reason"])
        self.assertIn("steps[1] (id 2): title", out["reason"])
        self.assertEqual("plan", self.state()["phase"])

    def test_steps_check_require_all_pending(self):
        data = valid_steps()
        data["steps"][0]["status"] = "done"
        self.use_steps_check()
        self.write_run("steps.json", json.dumps(data))
        out = workflow.handle_stop(self.root, {})
        self.assertIn("steps[0] (id 1): status", out["reason"])
        self.assertIn("pending", out["reason"])
        self.assertEqual("plan", self.state()["phase"])

        self.use_steps_check(require_all_pending=False)
        self.write_run("steps.json", json.dumps(data))
        self.assertIn("entering phase 'implement'", workflow.handle_stop(self.root, {})["reason"])

    def test_steps_check_require_all_pending_defaults_to_false(self):
        data = valid_steps()
        data["steps"][0]["status"] = "done"
        self.use_steps_check(require_all_pending=None)
        self.write_run("steps.json", json.dumps(data))
        self.assertIn("entering phase 'implement'", workflow.handle_stop(self.root, {})["reason"])

    def test_steps_check_details_truncated(self):
        self.use_steps_check(maxOutputChars=100)
        data = {"feature": "F", "steps": [{"id": i + 1} for i in range(20)]}
        self.write_run("steps.json", json.dumps(data))
        out = workflow.handle_stop(self.root, {})
        self.assertIn("...(truncated)", out["reason"])
        self.assertIn("steps[0] (id 1)", out["reason"])
        self.assertNotIn("steps[19]", out["reason"])

    # --- delegation and the failure file ------------------------------------------

    def delegate(self, phase, command="exit 0", **extra):
        phases = {"implement": {"check": {"type": "command", "command": command}}}
        phases.setdefault(phase, {})["delegate"] = True
        config = {"phases": phases}
        config.update(extra)
        self.set_config(config)

    def last_failure(self):
        path = os.path.join(self.base, "run", "last-failure.md")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_delegate_absent_output_is_unchanged(self):
        for config in (None, {"phases": {"implement": {"delegate": False,
                                                       "check": {"type": "command", "command": "exit 1"}}}}):
            if config is None:
                self.set_test_command("exit 1")
            else:
                self.set_config(config)
            workflow.cmd_init(self.root, "spec.md")
            state = self.state()
            state["status"] = "active"
            out = workflow.cmd_go(self.root)
            cfg = workflow.load_config(self.root)
            self.assertEqual(workflow.header(state) + "\n\n" + workflow.phase_instructions(self.root, cfg, state), out)
            self.assertEqual(
                workflow.header(state) + "\n\n" + workflow.phase_instructions(self.root, cfg, state),
                workflow.handle_context(self.root, "SessionStart")["hookSpecificOutput"]["additionalContext"])

            self.write_run("plan.md", "plan")
            reason = workflow.handle_stop(self.root, {})["reason"]
            state = self.state()
            self.assertEqual("Phase 'plan' passed (.claude/specloop/run/plan.md exists). Now entering phase "
                             "'implement'.\n\n" + workflow.phase_instructions(self.root, cfg, state), reason)

            reason = workflow.handle_stop(self.root, {})["reason"]
            self.assertEqual("Phase 'implement' is not complete yet. `exit 1` exited with 1:\n\n\n"
                             "Keep working on phase 'implement'.\n\n"
                             + workflow.phase_instructions(self.root, cfg, state), reason)
            self.assertNotIn("delegated", workflow.handle_context(self.root, "UserPromptSubmit")
                             ["hookSpecificOutput"]["additionalContext"])

    def test_delegated_go_contains_preamble_instructions_and_files(self):
        self.delegate("plan")
        workflow.cmd_init(self.root, "spec.md")
        out = workflow.cmd_go(self.root)
        self.assertIn("phase=plan", out)
        self.assertIn("exactly one subagent", out)
        self.assertIn("## Phase: plan", out)
        self.assertIn("## Files to read", out)
        self.assertIn("- `spec.md`", out)
        self.assertNotIn("{brief}", out)
        self.assertNotIn("plan.md`", out.split("## Files to read")[1])
        text = workflow.handle_context(self.root, "SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("exactly one subagent", text)
        self.assertIn("## Files to read", text)

    def test_delegated_stop_reason_on_pass_uses_preamble(self):
        self.delegate("implement")
        self.start()
        self.write_run("plan.md", "plan")
        reason = workflow.handle_stop(self.root, {})["reason"]
        self.assertIn("entering phase 'implement'", reason)
        self.assertIn("exactly one subagent", reason)
        self.assertIn("## Phase: implement", reason)
        self.assertIn("- `.claude/specloop/run/plan.md`", reason)

    def test_delegated_fail_reason_is_short_and_points_to_failure_file(self):
        self.delegate("implement", command="echo line-$((0+1)); echo line-$((1+1)); exit 1")
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        reason = workflow.handle_stop(self.root, {})["reason"]
        self.assertIn("Phase 'implement' is not complete yet", reason)
        self.assertIn("exited with 1", reason)
        self.assertIn(".claude/specloop/run/last-failure.md", reason)
        self.assertIn("exactly one subagent", reason)
        self.assertNotIn("line-1", reason)
        self.assertNotIn("line-2", reason)
        self.assertIn("line-2", self.last_failure())
        self.assertIn("- `.claude/specloop/run/last-failure.md`", reason)

    def test_delegated_reject_reason_is_short(self):
        self.delegate("implement")
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        workflow.handle_stop(self.root, {})
        self.write_run("review.md", "SECRET-DETAIL fix the null check.\nVERDICT: CHANGES_REQUESTED")
        reason = workflow.handle_stop(self.root, {})["reason"]
        self.assertIn("Back to phase 'implement'", reason)
        self.assertIn(".claude/specloop/run/last-failure.md", reason)
        self.assertNotIn("SECRET-DETAIL", reason)
        self.assertIn("- `.claude/specloop/run/review-3.md`", reason)
        self.assertIn("exactly one subagent", reason)

    def test_brief_lists_review_files_and_last_failure(self):
        self.start()
        state, config = self.state(), workflow.load_config(self.root)
        brief = workflow.phase_brief(self.root, config, state)
        self.assertNotIn("review", brief.split("## Files to read")[1])
        self.assertNotIn("last-failure", brief)
        self.write_run("plan.md", "plan")
        self.write_run("review-2.md", "old")
        self.write_run("review.md", "new")
        self.write_run("last-failure.md", "boom")
        files = workflow.phase_brief(self.root, config, state).split("## Files to read")[1]
        expected = ["spec.md", ".claude/specloop/run/plan.md", ".claude/specloop/run/review-2.md",
                    ".claude/specloop/run/review.md", ".claude/specloop/run/last-failure.md"]
        self.assertEqual(["- `%s`" % p for p in expected], [l for l in files.splitlines() if l.startswith("- ")])

    def test_delegate_override_preamble_wins(self):
        self.write_override("delegate", "CUSTOM ORCHESTRATOR for {spec}\n{brief}")
        self.delegate("plan")
        workflow.cmd_init(self.root, "spec.md")
        out = workflow.cmd_go(self.root)
        self.assertIn("CUSTOM ORCHESTRATOR for spec.md", out)
        self.assertNotIn("exactly one subagent", out)
        self.assertIn("## Phase: plan", out)

    def test_last_failure_written_on_failed_command_with_full_output(self):
        self.set_config({"maxOutputChars": 50, "phases": {"implement": {"check": {
            "type": "command", "command": "echo FIRST-$((2*3)); printf 'x%.0s' $(seq 1 200); echo; exit 2"}}}})
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        reason = workflow.handle_stop(self.root, {})["reason"]
        self.assertNotIn("FIRST-6", reason)
        text = self.last_failure()
        self.assertIn("exited with 2", text)
        self.assertIn("FIRST-6", text)
        self.assertIn("x" * 200, text)

    def test_last_failure_written_on_rejected_review(self):
        self.start()
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        workflow.handle_stop(self.root, {})
        review = "Fix it.\n" + "detail " * 1000 + "\nVERDICT: CHANGES_REQUESTED"
        self.write_run("review.md", review)
        workflow.handle_stop(self.root, {})
        self.assertEqual(review, self.last_failure())

    def test_last_failure_removed_when_phase_passes(self):
        self.start()
        workflow.handle_stop(self.root, {})
        self.assertIn("plan.md", self.last_failure())
        self.write_run("plan.md", "plan")
        workflow.handle_stop(self.root, {})
        self.assertIsNone(self.last_failure())

    def test_last_failure_removed_on_advance(self):
        self.start()
        workflow.handle_stop(self.root, {})
        self.assertIsNotNone(self.last_failure())
        workflow.cmd_advance(self.root)
        self.assertIsNone(self.last_failure())

    def test_delegated_context_states_orchestrator_role(self):
        self.delegate("plan")
        self.start()
        workflow.handle_stop(self.root, {})
        text = workflow.handle_context(self.root, "UserPromptSubmit")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Phase 'plan' is delegated", text)
        self.assertNotIn("## Files to read", text)
        text = workflow.handle_context(self.root, "SessionStart")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Phase 'plan' is delegated", text)


def valid_steps():
    return {
        "feature": "Short feature title",
        "steps": [
            {
                "id": 1,
                "title": "Add steps.json schema validation",
                "type": "feat",
                "description": "What to change and why.",
                "acceptance": ["Observable criteria this step must satisfy"],
                "files": ["kit/specloop/engine.py"],
                "status": "pending",
                "commit": None,
            },
            {
                "id": 2,
                "title": "Document it",
                "description": "Explain the format.",
                "acceptance": ["README mentions steps.json"],
                "status": "pending",
            },
        ],
    }


class StepsValidationTest(unittest.TestCase):

    def errors_for(self, index, field, value, delete=False):
        data = valid_steps()
        if delete:
            del data["steps"][index][field]
        else:
            data["steps"][index][field] = value
        return workflow.validate_steps(data)

    def assertStepError(self, errors, location, field):
        self.assertTrue(errors, "expected errors")
        self.assertTrue(any(e.startswith(location) and field in e for e in errors),
                        "no error for %s %s in %r" % (location, field, errors))

    def test_valid_steps_pass(self):
        self.assertEqual([], workflow.validate_steps(valid_steps()))

    def test_top_level_must_be_object(self):
        self.assertTrue(workflow.validate_steps([]))
        self.assertTrue(workflow.validate_steps(None))

    def test_feature_required_non_empty_string(self):
        for value in (None, "", "  ", 5):
            data = valid_steps()
            data["feature"] = value
            self.assertStepError(workflow.validate_steps(data), "feature", "feature")
        data = valid_steps()
        del data["feature"]
        self.assertStepError(workflow.validate_steps(data), "feature", "feature")

    def test_steps_required_non_empty_list(self):
        for value in (None, [], {}, "x"):
            data = valid_steps()
            data["steps"] = value
            self.assertStepError(workflow.validate_steps(data), "steps", "steps")

    def test_step_must_be_object(self):
        data = valid_steps()
        data["steps"].append("not a step")
        self.assertStepError(workflow.validate_steps(data), "steps[2]", "object")

    def test_id_must_be_positive_integer(self):
        for value in (0, -1, "1", True, 1.5, None):
            self.assertStepError(self.errors_for(0, "id", value), "steps[0]", "id")
        self.assertStepError(self.errors_for(0, "id", None, delete=True), "steps[0]", "id")

    def test_id_must_be_unique(self):
        errors = self.errors_for(1, "id", 1)
        self.assertEqual(1, len(errors))
        self.assertStepError(errors, "steps[1] (id 1)", "id")

    def test_title_required_and_max_72(self):
        for value in ("", "   ", None, 7, "x" * 73):
            self.assertStepError(self.errors_for(0, "title", value), "steps[0] (id 1)", "title")
        self.assertStepError(self.errors_for(0, "title", None, delete=True), "steps[0] (id 1)", "title")
        self.assertEqual([], self.errors_for(0, "title", "x" * 72))

    def test_description_required(self):
        for value in ("", None, ["x"]):
            self.assertStepError(self.errors_for(1, "description", value), "steps[1] (id 2)", "description")
        self.assertStepError(self.errors_for(1, "description", None, delete=True), "steps[1] (id 2)", "description")

    def test_acceptance_non_empty_list_of_non_empty_strings(self):
        for value in ([], "criterion", [""], ["ok", " "], [1], None):
            self.assertStepError(self.errors_for(0, "acceptance", value), "steps[0] (id 1)", "acceptance")
        self.assertStepError(self.errors_for(0, "acceptance", None, delete=True), "steps[0] (id 1)", "acceptance")

    def test_status_must_be_known(self):
        errors = self.errors_for(0, "status", "finished")
        self.assertStepError(errors, "steps[0] (id 1)", "status")
        self.assertIn("pending", errors[0])
        self.assertStepError(self.errors_for(0, "status", None, delete=True), "steps[0] (id 1)", "status")
        for value in ("pending", "active", "done", "empty"):
            self.assertEqual([], self.errors_for(0, "status", value))

    def test_type_must_be_conventional_commit_type(self):
        self.assertStepError(self.errors_for(0, "type", "feature"), "steps[0] (id 1)", "type")
        for value in ("feat", "fix", "refactor", "test", "docs", "chore", "perf", "build", "ci", "style"):
            self.assertEqual([], self.errors_for(0, "type", value))

    def test_files_must_be_list_of_strings(self):
        for value in ("a.py", [1], None):
            self.assertStepError(self.errors_for(0, "files", value), "steps[0] (id 1)", "files")
        self.assertEqual([], self.errors_for(0, "files", []))

    def test_commit_must_be_string_or_null(self):
        self.assertStepError(self.errors_for(0, "commit", 123), "steps[0] (id 1)", "commit")
        self.assertEqual([], self.errors_for(0, "commit", "abc123"))
        self.assertEqual([], self.errors_for(0, "commit", None))

    def test_reports_all_errors_at_once(self):
        data = valid_steps()
        data["steps"][0]["title"] = ""
        data["steps"][1]["status"] = "bogus"
        errors = workflow.validate_steps(data)
        self.assertStepError(errors, "steps[0] (id 1)", "title")
        self.assertStepError(errors, "steps[1] (id 2)", "status")

    # --- immutability ------------------------------------------------------------

    def history(self):
        """Three steps: 1 done, 2 empty, 3 pending, 4 active."""
        data = valid_steps()
        data["steps"][0].update(status="done", commit="abc123")
        data["steps"][1].update(status="empty")
        data["steps"].append({"id": 3, "title": "Third", "description": "d", "acceptance": ["a"],
                              "status": "pending"})
        data["steps"].append({"id": 4, "title": "Fourth", "description": "d", "acceptance": ["a"],
                              "status": "active"})
        return data

    def test_immutable_accepts_pending_edits(self):
        previous = self.history()
        current = self.history()
        current["steps"][2].update(title="Renamed", description="New", acceptance=["x", "y"])
        current["steps"][3]["description"] = "Changed active step"
        current["steps"].append({"id": 9, "title": "New", "description": "d", "acceptance": ["a"],
                                 "status": "pending", "commit": None})
        current["steps"].insert(2, current["steps"].pop())  # move the new step before step 3
        self.assertEqual([], workflow.check_steps_immutable(previous, current))
        del current["steps"][3]  # remove pending step 3
        self.assertEqual([], workflow.check_steps_immutable(previous, current))
        current["steps"][2], current["steps"][3] = current["steps"][3], current["steps"][2]
        self.assertEqual([], workflow.check_steps_immutable(previous, current))

    def test_immutable_rejects_done_step_changed(self):
        current = self.history()
        current["steps"][0]["title"] = "Rewritten history"
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[0] (id 1)", "done")

    def test_immutable_rejects_empty_step_changed(self):
        current = self.history()
        current["steps"][1]["acceptance"] = ["other"]
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[1] (id 2)", "empty")

    def test_immutable_rejects_done_step_removed(self):
        current = self.history()
        del current["steps"][0]
        errors = workflow.check_steps_immutable(self.history(), current)
        self.assertTrue(any("id 1" in e and "removed" in e for e in errors), errors)

    def test_immutable_rejects_done_steps_reordered(self):
        current = self.history()
        current["steps"][0], current["steps"][1] = current["steps"][1], current["steps"][0]
        errors = workflow.check_steps_immutable(self.history(), current)
        self.assertTrue(any("reordered" in e for e in errors), errors)

    def test_immutable_rejects_status_change(self):
        current = self.history()
        current["steps"][2]["status"] = "done"
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[2] (id 3)", "status")

    def test_immutable_rejects_commit_change(self):
        current = self.history()
        current["steps"][2]["commit"] = "def456"
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[2] (id 3)", "commit")

    def test_immutable_rejects_new_step_not_pending(self):
        new = {"id": 9, "title": "New", "description": "d", "acceptance": ["a"], "status": "active"}
        current = self.history()
        current["steps"].append(new)
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[4] (id 9)", "status")
        new.update(status="pending", commit="abc999")
        self.assertStepError(workflow.check_steps_immutable(self.history(), current), "steps[4] (id 9)", "commit")


if __name__ == "__main__":
    unittest.main()
