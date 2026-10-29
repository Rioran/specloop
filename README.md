# specloop

A spec-driven development loop for [Claude Code](https://claude.com/claude-code), built only from hooks, slash commands and a small Python state machine. You drop in a specification, start development by hand, and Claude is walked through **plan → implement → review** phases. A phase only completes when its check passes: the plan file exists, the tests are green, or the reviewer approves.

It runs inside your normal Claude Code session (CLI, desktop app or IDE), so it uses your Claude subscription. You don't need API keys or a separate orchestrator.

```
/sl-spec specs/login.md      load the spec (Claude summarizes it, nothing else)
/sl-go                       start: plan -> implement -> review -> done
```

## How it works

| Phase       | Claude does                                        | Gate (checked by the Stop hook)                 | Edits allowed            |
|-------------|----------------------------------------------------|--------------------------------------------------|--------------------------|
| `plan`      | Reads the spec, writes `.claude/specloop/run/plan.md` | plan file exists and is non-empty              | only `.claude/specloop/run/` |
| `implement` | Test-first implementation of the plan              | your test command exits 0                        | anything                 |
| `review`    | Reviews the diff (ideally via a subagent), writes `review.md` | `VERDICT: APPROVE` → done; `VERDICT: CHANGES_REQUESTED` → back to implement | only `.claude/specloop/run/` |

The engine plugs into four hooks:

- **Stop**: each time Claude tries to end its turn, it runs the current phase's check. If the check fails, the stop is blocked and Claude gets the failure (for example the tail of the test output) and keeps working. If it passes, the workflow moves to the next phase and Claude gets that phase's instructions.
- **PreToolUse**: enforces per-phase edit restrictions. It blocks `git push` and `gh pr create`/`merge` until the workflow is done, and stops Claude from editing specloop's own state.
- **SessionStart / UserPromptSubmit**: put the current phase and its instructions into context, so a new or resumed session picks up where the last one stopped.

Safety valves:
- After `maxIterations` blocked stops (default 15) the workflow goes to `stalled` and Claude is allowed to stop. `/sl-go` resumes it.
- `/sl-pause` turns the hooks off when you want to talk to Claude in between.
- A spec or plan that looks too big for one plan → implement → review cycle triggers a warning (see [Large specs and plans](#large-specs-and-plans)).
- Setting `SPECLOOP_DISABLED=1` makes every hook a no-op.
- If the engine itself crashes, the hook logs to stderr and exits 0, so a crash never breaks your session.

## Requirements

- Claude Code
- Python 3.8+ (stdlib only)
- `sh`, which on Windows means Git Bash. Claude Code on Windows needs it anyway.
- git (the review phase reads `git diff`)

## Install into a project

Clone once, then install into as many projects as you like:

```bash
git clone https://github.com/ba3a/specloop.git ~/tools/specloop
~/tools/specloop/install.sh ~/code/my-project
```

On Windows (cmd or PowerShell):

```bat
install.cmd C:\code\my-project
```

Then start a **new** Claude Code session in that project. Hooks are loaded at session start.

The installer:
- copies the kit into `<project>/.claude/specloop/`;
- creates `config.json` and tries to detect the test command (Gradle, Maven, npm, pytest, Go, Cargo, .NET);
- installs the slash commands into `.claude/commands/`;
- merges the hooks into `.claude/settings.json` and leaves your other settings and hooks alone;
- if the project is in a git work tree and `.gitignore` is writable, keeps a marked `# >>> specloop` block in `.gitignore`. The block ignores the installed kit, the runtime state and the installed slash commands. `config.json` and `overrides/` stay tracked so the team can share them. Re-running the installer rewrites the block, and `--uninstall` removes it.

### Installer options

| Option | Meaning |
|--------|---------|
| `--test-command "<cmd>"` | Command that must pass to leave `implement` (overrides detection). |
| `--scope project\|local` | `project` (default) writes hooks to `.claude/settings.json`, which you commit and share with your team. `local` writes them to `.claude/settings.local.json`, which stays personal. |
| `--prefix <p>` | Slash command prefix, default `sl-`. Use `--prefix ""` for plain `/spec`, `/go`, … |
| `--force` | Recreate `config.json` from scratch. |
| `--no-gitignore` | Don't touch `.gitignore`. |
| `--uninstall [--keep-config]` | Remove specloop: kit, commands, hooks. Other settings are kept. |

### Updating

```bash
cd ~/tools/specloop && git pull
./install.sh ~/code/my-project
```

An update replaces the engine, launcher, `defaults.json` and the shipped `phases/`. Your `config.json` and `overrides/` are never touched.

## Commands

| Command | What it does |
|---------|--------------|
| `/sl-spec <file>` | Load a spec; status `ready`. Clears the previous run's `run/` artifacts (plan, reviews). Claude summarizes the spec and waits. |
| `/sl-feature <file>` | Load a spec in [feature mode](#feature-mode) (one commit per planned step). Also clears `run/`. |
| `/sl-go` | Start, or resume after pause/stall. |
| `/sl-pause` | Stop driving the session; keep the phase. |
| `/sl-status` | Show the state (phase, iteration, history). |
| `/sl-advance` | Force-pass the current phase. |
| `/sl-reset` | Delete the state and the `run/` artifacts. |

## Configuration

Files in `<project>/.claude/specloop/`:

| File | Owner | Purpose |
|------|-------|---------|
| `defaults.json` | kit (replaced on update) | Default settings and phase graph. |
| `config.json` | **project** | Your overrides, deep-merged over `defaults.json`. |
| `phases/<phase>.md` | kit (replaced on update) | Shipped phase instructions. |
| `overrides/<phase>.md` | **project** | Your phase instructions; take precedence over `phases/`. |
| `state.json`, `run/` | runtime | Current state, plan and review files. |

Everything in this directory except `config.json` and `overrides/` is git-ignored.

`config.json` example:

```json
{
  "commandPrefix": "sl-",
  "maxIterations": 20,
  "checkTimeoutSec": 1200,
  "blockedCommandsUntilDone": ["git push", "gh pr create", "gh pr merge", "npm publish"],
  "phases": {
    "implement": {
      "check": { "type": "command", "command": "./gradlew test -q && ./gradlew lint -q" }
    }
  }
}
```

### Phase graph

Each phase has `next` (a phase name or `done`), an optional `allowEdits` list of path prefixes (omit it to allow all edits), an optional `instructions` file, and an optional `check`:

| `check.type` | Fields | Passes when |
|--------------|--------|-------------|
| `file` | `path` | the file exists and is non-empty |
| `command` | `command` | the command exits 0 (run with `shell`, default `bash`, from the project root) |
| `verdict` | `path`, `approve`, `reject`, `onReject` | the file contains `approve`. If it contains `reject`, the file is archived and the flow jumps to `onReject`. |

A phase with no check passes immediately. To remove a phase, set it to `null` and re-point the previous phase's `next`. For example, to skip review:

```json
{ "phases": { "implement": { "next": "done" }, "review": null } }
```

To add a phase, for example `docs` after `implement`, define it in `config.json`, point `implement.next` at it, and put the instructions in `overrides/docs.md`.

In instruction files, `{spec}` becomes the spec path and `{run_dir}` becomes `.claude/specloop/run`.

Whenever a check fails or a review requests changes, the full details (the complete check output or review text, not truncated) are written to `.claude/specloop/run/last-failure.md`. The file is removed when a phase passes and on `/sl-advance`.

### Delegating a phase to a subagent

Set `"delegate": true` on any phase (default `false`; no shipped phase enables it) to run that phase in a fresh context:

```json
{ "phases": { "implement": { "delegate": true } } }
```

In a delegated phase the main session acts as an orchestrator. Instead of the bare phase instructions, `/sl-go`, the Stop hook and the session context give it an orchestration preamble (`phases/delegate.md`, overridable with `overrides/delegate.md`; `{brief}` marks where the brief goes). The preamble tells it to start exactly one subagent with a self-contained **phase brief**, not to read or edit files itself, and to finish its turn with a summary of at most 5 lines. The brief holds the rendered phase instructions and the files the subagent should read: the spec, `run/plan.md`, any `run/review*.md`, and `run/last-failure.md`. When a check fails, the Stop hook's message stays short and points at `run/last-failure.md` instead of including the full output.

Gates still run only when the main session stops; specloop registers no `SubagentStop` hook. Note that a phase named `delegate` would share its instructions file with the preamble.

### Large specs and plans

A single plan → implement → review cycle works best for changes that fit in one commit. specloop warns when a spec or plan looks bigger than that, and recommends `/sl-feature` (feature mode, which splits work into multiple commits) instead.

| Key | Default | Check |
|-----|---------|-------|
| `largeSpecWarning.maxLines` | `200` | spec has more lines than this |
| `largeSpecWarning.maxWords` | `2000` | spec has more words than this |
| `largeSpecWarning.maxPlanItems` | `12` | `run/plan.md` has more checklist items (`- [ ]` / `- [x]`) than this |

- **Spec size.** `/sl-spec` prints a warning with the spec's line and word counts and the limits. The first `/sl-go` of that run does not start; it repeats the warning. Running `/sl-go` again starts in single mode anyway (state records `largeSpecAcknowledged`). Resuming a run that has already started never warns.
- **Plan size.** When the `plan` phase passes with too many checklist items, the workflow pauses before `implement` instead of continuing. Either run `/sl-reset` and then `/sl-feature`, or run `/sl-go` to continue in `implement` (state records `largePlanAcknowledged`).

Set a key to `null` to disable that check, or the whole block to disable all of them:

```json
{ "largeSpecWarning": { "maxPlanItems": null } }
```

### Environment variables

| Variable | Effect |
|----------|--------|
| `SPECLOOP_DISABLED=1` | All hooks become no-ops. |
| `SPECLOOP_PYTHON=/path/to/python` | Interpreter for the launcher and installer (skips auto-detection). |

## Feature mode

For features too big for one plan → implement → review cycle, load the spec with `/sl-feature` instead of `/sl-spec`. A planning subagent splits the spec into small ordered steps, and each step is meant to end in one commit on a dedicated branch.

> **Status:** branch creation, planning and plan approval are available. Running and committing the steps is **coming soon**: approving a plan currently stores it and reports that step execution is not available yet.

```
/sl-feature specs/big-feature.md   load the spec in feature mode (Claude summarizes it)
/sl-go                             create the feature branch, run the planning phase, pause for approval
/sl-go                             approve the plan (re-validates run/steps.json)
```

**Branch.** The first `/sl-go` of a feature run creates and checks out `feature.branchPrefix` + a slug of the spec's first `# ` heading (or its file name): lowercase ASCII, `-` separators, at most 50 characters. If that branch exists, `-2`, `-3`, … is appended. It refuses, without changing anything, when the directory is not a git repository, the repository has no commits, `HEAD` is detached, or tracked files have uncommitted changes (staged or not; changes under `.claude/specloop/` are ignored). State records `branch`, `baseRef` (the starting commit) and `preexistingUntracked` (untracked files that existed at start). Later `/sl-go` calls refuse if the current branch is not the run's branch.

**Planning.** The `feature-plan` phase is delegated to a subagent (see [Delegating a phase](#delegating-a-phase-to-a-subagent)). It writes `run/plan.md` (goal, acceptance criteria, assumptions, step-order rationale) and `run/steps.json`:

```json
{
  "feature": "Short feature title",
  "steps": [
    { "id": 1, "title": "At most 72 characters", "type": "feat", "description": "What and why.",
      "acceptance": ["Observable criterion"], "files": ["src/x.py"], "status": "pending", "commit": null }
  ]
}
```

Edits are restricted to `.claude/specloop/run/`. The gate passes when `steps.json` parses, matches the schema and every step is `pending`; otherwise the errors are written to `run/last-failure.md` and the subagent keeps working.

**Approval.** When the plan passes its gate, the run pauses. Review or edit `run/plan.md` and `run/steps.json`, then run `/sl-go`: it validates `steps.json` again (refusing with the errors if it is invalid) and stores the approved copy in state as `approvedSteps`.

The single-mode size warnings do not apply in feature mode.

| Key | Default | Meaning |
|-----|---------|---------|
| `feature.branchPrefix` | `"specloop/"` | Prefix of the feature branch name. |
| `feature.planPhase` | `"feature-plan"` | First phase of a feature run. |
| `feature.requirePlanApproval` | `true` | Pause for approval after planning. When `false`, the plan is approved as soon as its gate passes. |
| `feature.gitTimeoutSec` | `60` | Timeout for every git call the engine makes. |
| `feature.planPhases` | `feature-plan` phase | Phase graph for planning, with the same keys as `phases` (`delegate`, `allowEdits`, `check`, `instructions`). |

## Tips

- Write specs with explicit acceptance criteria. The plan, the tests and the review all key off them.
- If you commit `.claude/settings.json` with `--scope project`, every teammate who opens the repo gets the hooks. Without a loaded spec the hooks do nothing, so that's harmless.
- A long test suite runs on every Stop during `implement`. Point `check.command` at a fast subset if needed.
- Claude can't stop to ask you questions while a phase is active. Use `/sl-pause`, or let it record assumptions in `plan.md`. The plan instructions already ask for that.

## Hacks and limitations

- **Delegation is instruction-only and not guaranteed.** Hooks cannot start subagents, so `delegate: true` only *tells* the main session to hand the phase to a subagent. It may do the work itself anyway, for example after context compaction or when it judges the phase to be small. The gates still protect the result either way; only the fresh-context benefit is lost.
  - *How to notice:* in a delegated phase, the main session reads source files, edits files or runs the tests itself instead of starting one subagent.
  - *How to recover:* run `/sl-pause`, then `/clear`, then `/sl-go`. `/sl-go` re-sends the orchestrator instructions with the phase brief to a clean context.

## Uninstall

```bash
~/tools/specloop/install.sh ~/code/my-project --uninstall
```

## License

[0BSD](LICENSE): do anything with it, no attribution required, no warranty.
