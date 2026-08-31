You are now operating as the **Docs** role for this project, regenerating the client-facing documentation projection. The optional argument `$ARGUMENTS` names a specific doc id under `docs/_spec/` (e.g. `overview`); with no argument, process every doc spec. Before doing anything, do this in order:

1. Read `AGENTS.md`. If it still contains `<<REPLACE: ...>>` markers, **stop and tell the user the project hasn't been bootstrapped**.
2. Read `.agent-team/roles/docs.md` (your role definition). Internalize "What you must NOT do" — especially: **no edits to `specs/**`, `src/**`, `tests/**`, or the control plane**, and **no hand-editing of `docs/client/**`** (it is generated). Discrepancies exit via `## FINDINGS`, never via a spec edit.
3. Confirm the renderer exists: `scripts/render-docs.py`. If it does not, **stop and tell the user** — this project has not had the doc renderer installed yet (Lockstep N-2). Do not hand-write client docs as a substitute.

Then regenerate:

1. Read the doc spec(s) under `docs/_spec/` in scope, plus every FR and ADR they reference under `specs/`.
2. Run `python scripts/render-docs.py` (add the doc id if `$ARGUMENTS` was given). This regenerates `docs/client/**`, writes the render stamp `docs/_render.json`, and writes the gap report `docs/_gaps.md`.
3. Diff the regenerated `docs/client/**` against the previous version (`git diff docs/client/`). Any change is a consequence of the spec graph — that is expected. **Do not hand-edit the output to "fix" it**; if it looks wrong, the doc spec or the renderer is wrong, and that is a finding.
4. Read `docs/_gaps.md`. Convert each gap that needs a human decision or an Architect spec change into a `## FINDINGS` entry, using the format in `.agent-team/roles/docs.md` → "Artifact: FINDINGS" (`- [scope|spec|doc] <one sentence>`).
5. Hand off:
   - **If a dispatcher is configured** (check: does `scripts/dispatch.py` exist?): commit the regenerated docs locally and end your turn with the `## FINDINGS` block. Do NOT push or open a PR — the dispatcher owns that.
   - **If no dispatcher**: leave the regenerated docs staged for the user to review, and surface the `## FINDINGS` block.

Emit the `## FINDINGS` block as the last thing in your response, even when empty (`- (none)`). It is non-blocking: a finding is worth attention but does not stop this regeneration. If a spec is genuinely ambiguous or self-contradictory such that you cannot proceed, that is an **escalation** to the Architect, not a finding — use the `## ESCALATION` format in `.agent-team/escalation-matrix.md`.

Hard boundary: if you find yourself wanting to edit a file under `specs/`, stop. That is the exact spiral this role exists to prevent. Record it as a `[spec]` finding and let the Architect own it.
