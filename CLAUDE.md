# CLAUDE.md

Guidance for developing specloop itself. User-facing documentation lives in `README.md`.

## What this repository is

specloop is a kit that gets installed into other projects. It adds a spec-driven state machine (plan → implement → review) to Claude Code, built from hooks and slash commands. Nothing in this repository runs inside this repository. `install.py` copies `kit/` into a target project's `.claude/` directory.

## Layout

```
install.py, install.sh, install.cmd   installer (Python) and its shell / cmd wrappers
VERSION                               kit version, copied into targets
kit/specloop/engine.py                state machine: hook handlers + manual commands
kit/specloop/run.sh                   portable launcher used by hooks and commands (finds Python)
kit/specloop/defaults.json            default config and phase graph (replaced on update)
kit/specloop/phases/*.md              shipped phase instructions (replaced on update)
kit/commands/*.md                     slash command templates; {{prefix}} is substituted on install
tests/                                unittest suites for the engine and the installer
```

Installed layout in a target: `.claude/specloop/{engine.py,run.sh,defaults.json,phases/,VERSION,manifest.json}` belong to the kit. `config.json` and `overrides/` belong to the project. `state.json` and `run/` are runtime state.

## Commands

```bash
python -m unittest discover -s tests          # run all tests
python -m unittest tests.test_engine -v       # one suite
./install.sh /path/to/sandbox-project         # manual end-to-end check
```

## Rules

- **Python standard library only, compatible with Python 3.8.** Friends install this on arbitrary machines.
- **Hooks must never break a session.** Hook entry points catch every exception, log to stderr and exit 0. `run.sh` exits 0 when no Python is found.
- **Never overwrite project-owned files on update.** That means `config.json`, `overrides/`, and any settings or hooks that specloop did not create. specloop hook groups are identified by the `.claude/specloop/run.sh` marker in their command.
- **Backward compatibility of `config.json`.** Add new keys with defaults in `defaults.json`. Do not rename or repurpose existing keys without a migration in `install.py`.
- **Cross-platform.** Paths written into settings and commands use forward slashes and `$CLAUDE_PROJECT_DIR`. Hooks run through `sh`, which is Git Bash on Windows. Keep `.sh` files LF and `.cmd` files CRLF (see `.gitattributes`). On Windows, stage scripts with `git add --chmod=+x`.
- **User-visible command names go through the prefix.** In the engine use `cmd_name(config, "go")`; in command templates use `/{{prefix}}go`. Never hard-code `/sl-go`.
- Every behavior change gets a unit test in `tests/`. Tests build throwaway projects in temp directories and must not depend on the developer's machine. Use `exit 0` / `exit 1` as check commands.
- Update `README.md` whenever options, config keys, commands or phases change. Bump `VERSION` (semver) for every release.

## Hook protocol reference

- Stop: print `{"decision": "block", "reason": "..."}` to keep Claude working, or `{"systemMessage": "..."}` / nothing to let it stop.
- PreToolUse: print `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}` to block a tool call.
- SessionStart / UserPromptSubmit: print `{"hookSpecificOutput": {"hookEventName": "<event>", "additionalContext": "..."}}`.
