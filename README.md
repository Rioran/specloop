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
| `/sl-spec <file>` | Load a spec; status `ready`. Claude summarizes it and waits. |
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

### Environment variables

| Variable | Effect |
|----------|--------|
| `SPECLOOP_DISABLED=1` | All hooks become no-ops. |
| `SPECLOOP_PYTHON=/path/to/python` | Interpreter for the launcher and installer (skips auto-detection). |

## Tips

- Write specs with explicit acceptance criteria. The plan, the tests and the review all key off them.
- If you commit `.claude/settings.json` with `--scope project`, every teammate who opens the repo gets the hooks. Without a loaded spec the hooks do nothing, so that's harmless.
- A long test suite runs on every Stop during `implement`. Point `check.command` at a fast subset if needed.
- Claude can't stop to ask you questions while a phase is active. Use `/sl-pause`, or let it record assumptions in `plan.md`. The plan instructions already ask for that.

## Uninstall

```bash
~/tools/specloop/install.sh ~/code/my-project --uninstall
```

## License

[0BSD](LICENSE): do anything with it, no attribution required, no warranty.
