Run the agent gate locally. Use this before declaring any work complete.

Steps:

1. Run `python3 scripts/index-specs.py`. If it fails, fix the spec graph errors it reports -- usually missing required frontmatter or unknown `depends_on` references.
2. If `specs/INDEX.md` changed, that means the index was stale. Tell the user it needs to be committed.
3. Run `python3 scripts/deploy-gate.py $ARGUMENTS` (defaults to `--stage full` if no argument given).
4. If the gate fails, read each failure line and explain what would need to change to satisfy it. Do not modify anything yourself unless the user asks -- your job here is diagnosis.

Common failures:
- "no @covers test for AC-N" -> Reviewer hasn't written a test for that AC. Switch to `/reviewer FR-XXXX`.
- "@covers references unknown FR" -> A test annotation points at a deleted FR. Either restore the FR or update the annotation.
- "@covers references FR-XXXX:AC-N but that AC is not in the FR" -> The FR was edited; talk to the Architect about whether the AC was renumbered.

When green, report: number of FRs validated, number of `@covers` annotations, and which stage you ran.
