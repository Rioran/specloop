## Phase: feature-plan

Plan the feature described in the specification at `{spec}` as an ordered sequence of small steps. Each step will later be implemented, reviewed and committed on its own. Do not modify source code in this phase; only write files in `{run_dir}/`.

1. Read the specification and the code it touches.
2. Write `{run_dir}/plan.md` with:
   - the goal and the acceptance criteria, taken from the spec;
   - open questions and the assumptions you made (make a reasonable assumption rather than stopping to ask);
   - a short rationale for the order of the steps.
3. Write `{run_dir}/steps.json`:

```json
{
  "feature": "Short feature title",
  "steps": [
    {
      "id": 1,
      "title": "Imperative summary, at most 72 characters",
      "type": "feat",
      "description": "What to change and why, precise enough to implement without this conversation.",
      "acceptance": ["Observable criterion this step must satisfy"],
      "files": ["path/to/likely/file"],
      "status": "pending",
      "commit": null
    }
  ]
}
```

   - `id`: unique positive integer. `type`: one of `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`, `build`, `ci`, `style`.
   - Every step starts with `"status": "pending"` and `"commit": null`. The engine owns these two fields.

Planning rules:

- Each step is one coherent, reviewable commit.
- After every step the project builds and the test command passes.
- Each step depends only on earlier steps; steps run in array order.
- Prefer many small steps over a few large ones. As a guideline, keep each step's diff to about 300 changed lines or fewer.
- Every acceptance criterion in the spec is covered by at least one step.

When both files are written, finish your turn. specloop validates `steps.json`; if it is invalid you get the errors and must fix them. When it is valid, the run pauses so the user can review the plan.
