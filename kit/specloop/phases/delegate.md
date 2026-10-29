## Orchestrator: this phase is delegated to a subagent

You are the orchestrator for this phase. The work is done by a subagent that starts with a fresh context and sees nothing of this conversation.

1. Start exactly one subagent with the Agent tool. Pass it the phase brief below word for word as its prompt, from the `--- BEGIN PHASE BRIEF ---` line to the `--- END PHASE BRIEF ---` line.
2. Do not read source files, edit files or run checks yourself. The subagent does the work, and specloop runs the checks when you finish your turn.
3. When the subagent returns, write a summary of at most 5 lines for the user and finish your turn.

--- BEGIN PHASE BRIEF ---
{brief}
--- END PHASE BRIEF ---
