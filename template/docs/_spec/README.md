# Doc specs (`docs/_spec/`)

A **doc spec** defines one client-facing document as a projection of the spec
graph. It is the *only* hand-authored file in the docs pipeline. Everything under
`docs/client/` is generated from these specs by `scripts/render-docs.py` and must
never be edited by hand.

## The guard rule

**A doc spec holds section membership by ID only — never prose.** Section titles
are fixed and generated; the words come from the FRs and ADRs the spec points at.
If you find yourself wanting to write a sentence of client-facing explanation
here, it belongs in an FR (`## Why` / `## What`) or an ADR, not in this file.
This is what keeps the doc spec from becoming a second, drifting spec language.

## Schema

```yaml
doc: overview                 # doc id; must match the filename stem
title: "Project overview"     # the client-facing document title
frs:                          # FRs whose ACs / open questions this doc surfaces
  - FR-0001
  - FR-0006
adrs:                         # ADRs surfaced under "Decisions and why"
  - ADR-0001
```

One doc spec renders to `docs/client/<doc>.md`. Run the renderer with
`python scripts/render-docs.py` (all docs) or `python scripts/render-docs.py <doc>`.

## Generated sections (all read from the graph)

1. **What you've signed off** — acceptance criteria in the `ratified` state.
2. **Changes since your last review** — ACs in the `client-review` state.
3. **Open questions** — every Open Question and its default, per FR.
4. **Decisions and why** — the referenced ADRs.

Sections 1 and 2 depend on per-AC ratification state (`ac_state:` frontmatter,
added in a later phase). Until that lands they render an explanatory note rather
than failing, so the renderer is useful today.

## Outputs

- `docs/client/<doc>.md` — the generated document (committed; the git history is
  the proof it was never hand-edited).
- `docs/_render.json` — a render stamp: per doc, when it was rendered and a
  content hash of each source FR/ADR (used to detect staleness).
- `docs/_gaps.md` — a warn-only report: broken/`draft`/`deprecated` references,
  active FRs no doc covers, and sources that changed since the last render.
