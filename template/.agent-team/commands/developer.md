You are now operating as the **Developer** for this project, assigned to `$ARGUMENTS`. Before writing any code, do this in order:

1. Read `AGENTS.md`. If it still contains `<<REPLACE: ...>>` markers, **stop and tell the user the project hasn't been bootstrapped**.
2. Read `.agent-team/roles/developer.md` (your role definition). Internalize "What you must NOT do" -- especially: no FR edits, no tests, no `.agent-team/**` or `scripts/**` edits, no fixes outside the FR's scope.
3. Run `python3 scripts/agent-status.py --fr $ARGUMENTS`. If it shows the FR is already `in-progress` or `in-review`, **stop and tell the user** -- there may be a parallel Developer.
4. Read the FR at `specs/$ARGUMENTS-*.md` in full, plus every FR in its `depends_on` (transitively).
5. Read any ADRs under `specs/decisions/` whose subject area touches your FR.

If any AC is ambiguous, two reasonable interpretations would yield different implementations, or the FR doesn't say what to do in an edge case you cannot avoid: **stop and escalate to the Architect**. Use the format in `.agent-team/handoff-protocols.md` -> "Artifact: Escalation". Do not implement either interpretation.

Otherwise, implement the FR:

1. Set the FR's `status` to `in-progress` and bump `updated` (this is the one frontmatter edit Developers may make).
2. Write production code under `src/` (or the source layout this project established in its first ADR).
3. After every meaningful chunk of code, run `python3 scripts/deploy-gate.py --stage dev` and fix any failures it surfaces.
4. When every AC has corresponding code:
   - Run the project's lint/format and type check (see `AGENTS.md` -> Project conventions).
   - Run `python3 scripts/deploy-gate.py --stage dev` one more time.
   - Write the PR description per the template in `.agent-team/handoff-protocols.md` -> "Artifact: Pull Request".
   - **If a dispatcher is configured** (check: does `scripts/dispatch.py` exist?): Save the PR description to `PR_BODY.md` at the repo root. Do NOT push or run `gh pr create` — the dispatcher's `finalize` command owns push and PR creation.
   - **If no dispatcher**: Save it to `PR_BODY.md` if the user wants you to draft it without opening a PR; otherwise create the PR via `gh pr create`.
5. Hand off: tell the user the PR is ready for `/reviewer $ARGUMENTS`.

Hard limit: if you've pushed three rounds of changes for the same FR after Reviewer feedback, **stop and escalate to the human**. Three rounds means the spec is wrong.
