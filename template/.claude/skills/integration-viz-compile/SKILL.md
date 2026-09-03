---
name: integration-viz-compile
description: Compile an integration's architecture pack (yaml-frontmatter FR/ADR markdown files) plus its flow.yaml topology file into a viz spec JSON and a coverage gap report. Use whenever the user wants to generate, refresh, or lint the spec for an integration visualization, mentions compiling a flow.yaml, asks whether their FRs cover the integration steps, or asks to check integration spec coverage — even if they only say "rebuild the integration diagram" (compiling is step one; hand the spec to integration-viz-render afterwards).
---

# Integration Viz — Compile

Turn an architecture pack + flow.yaml into `spec.json` (the renderer's input) and `gaps.md` (a coverage lint over the pack). The spec is a read model: pure data, no presentation. FRs stay the source of truth for *why*; flow.yaml is the source of truth for *sequence and structure*; this skill joins them by ID and refuses to emit a spec with dangling references.

## Inputs

1. **Architecture pack**: a directory of markdown files with yaml frontmatter. Any file with an `id` in frontmatter is indexed. Aligned with the standard FR template:
   - **FR frontmatter used**: `id`, `title`, `status` (draft | ready | in-progress | in-review | merged | deprecated | blocked), `owner`, `depends_on`, `tags`, `owns`. Other template fields (`reads`, `derived_from`, `pattern`, dates) pass through the pack untouched.
   - **FR body sections extracted**: `Why` (`Context` honored as a legacy alias), `What`, `Acceptance criteria` (bullets; stable `AC-n:` prefixes are parsed into addressable AC ids), `Out of scope` (bullets), `Open questions` (`Q:` bullets paired with their nested `Default:`), `Notes`. Changelog and Dependencies prose are not extracted — `depends_on` frontmatter is the structured form.
   - **ADR linking is ADR-driven**: ADRs carry `fr_refs` in frontmatter (`id`, `title`, `status`, `date`, `fr_refs`, `supersedes`), and the compiler reverse-joins them onto the referenced FRs. `FR-XXXX:AC-n` mentions in the ADR body additionally anchor the ADR to those specific ACs, which the renderer badges on the AC row. `ADR-XXX` mentions in FR bodies and legacy `adrs:` FR frontmatter are merged in as supplementary links (deduped).
   - **ADR docs** — first paragraph of `## Decision` (the thesis line) becomes the chip summary. If a linked ADR is superseded by another ADR in the pack (`supersedes` chain), the compiler warns and the chip renders struck-through.
   - Files are classified by explicit `type:` frontmatter if present, otherwise by ID prefix (`FR-`, `ADR-`). If a pack deviates from these conventions, adjust `extract_section` / `parse_acs` / `doc_type` in the script rather than asking the user to restructure the pack.
2. **flow.yaml**: the topology spine. If the integration doesn't have one yet, copy the annotated template at `assets/flow-template.yaml` into the pack and help the user fill it in — systems first, then steps in seq order, then edges, then payloads. Keep IDs stable and seq numbers gapped by 10.

## Run

```bash
python scripts/compile.py --pack <pack-dir> --flow <path>/flow.yaml --out <out>/spec.json
```

`gaps.md` lands next to spec.json unless `--gaps` says otherwise.

**Coverage scope** (`--scope`): which FRs count as belonging to this integration for the unreferenced-FR lint. Default `tag` expects FRs to carry `integration:<flow-id>` in `tags`. If the pack is dedicated to a single integration (or the user doesn't use integration tags), pass `--scope pack` to treat every FR in the pack as in-scope instead.

## Behavior to preserve

- **Hard errors block emission** (unknown FR/system/step/payload refs, duplicate IDs). This is deliberate: a spec with dangling references renders a diagram that lies. Report the errors to the user and fix the pack or flow.yaml — reach for `--force` only when the user explicitly wants a draft render despite known holes.
- **Soft findings go to gaps.md**, and always relay them conversationally after a compile — they're the point, not noise. The three that matter:
  - *Tagged-but-unreferenced FRs*: requirements claimed for this integration that no step implements.
  - *Happy-path steps citing no FR*: implementation with no anchored requirement.
  - *Fields missing rationale*: mappings whose why is undocumented — the highest-value thing to backfill, since field mappings are what developers most often get wrong.
- **Provenance arrays are allowed to be empty.** They're forward slots for captured-context references (decisions, transcript moments). Never invent provenance to fill them.

## After compiling

Hand `spec.json` to the **integration-viz-render** skill to produce the interactive artifact. If the user only wanted the lint, summarize gaps.md and stop.
