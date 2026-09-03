---
name: integration-viz-render
description: Render a compiled integration spec (spec.json) into an interactive single-file HTML visualization — swimlane flow diagram, per-step Why/Contract/Behavior/Provenance facets, field-mapping tables with rationale, failure overlay, and trace-a-record mode. Use whenever the user asks to render, visualize, diagram, or "make the artifact for" an integration, or mentions the integration viz, swimlane spec, or trace mode — even if they phrase it as "show me the flow". If only a pack + flow.yaml exist (no spec.json yet), run integration-viz-compile first, then this.
---

# Integration Viz — Render

Inject a compiled `spec.json` into the canonical renderer template and deliver the result as a standalone HTML artifact. The renderer is a fixed asset, not something to regenerate: improvements to it propagate to every future integration, and specs stay pure data.

## Workflow

1. **Get a spec.** If `spec.json` doesn't exist yet, use the `integration-viz-compile` skill (pack + flow.yaml → spec.json). Never hand-write a spec when a pack exists — the compile step is where coverage linting happens.
2. **Inject.** The template contains `<script id="integration-spec" type="application/json">__SPEC_JSON__</script>`. Replace the placeholder with the spec JSON. Escape `</` as `<\/` so JSON strings can never terminate the script tag:

```python
import json, pathlib
tpl = pathlib.Path("assets/renderer.html").read_text()
spec = json.dumps(json.load(open("spec.json")), ensure_ascii=False).replace("</", "<\\/")
out = tpl.replace("__SPEC_JSON__", spec)
pathlib.Path(f"{json.load(open('spec.json'))['meta']['id']}.html").write_text(out)
```

3. **Deliver.** Name the output `<integration-id>.html`, place it where the user can open it, and present it. If the spec's `gaps` object is non-empty, mention the findings briefly — the rendered banner shows them, but say it out loud too.

## Do not

- **Do not edit the template per-integration.** All variation belongs in the spec. If the spec schema can't express something the user needs, that's a template improvement: edit `assets/renderer.html` once, deliberately, so every future render benefits — and keep the change backward-compatible with existing specs (new spec fields must be optional).
- **Do not inline-modify the injected spec** to "fix" gaps — fix the pack or flow.yaml and recompile, otherwise the artifact and the pack disagree.

## What the artifact does (for orienting the user)

- **Title block**: purpose, trigger, success criterion — the 10-second orientation layer, styled as an engineering drawing title block.
- **Swimlanes**: systems as lanes, steps in seq order; dashed border = async, PHI badge = compliance-sensitive; edges carry clickable payload chips that open the field-mapping inspector.
- **Step panel**: Why (FR context + acceptance-criteria checklist + ADR chips), Contract (endpoint/auth + mapping table with per-field rationale on click), Behavior (retries/idempotency/ordering/timeouts), Provenance (empty-by-design slot for captured-context references).
- **Failure overlay**: toggle in the title block reveals failure-path steps and failure/compensation edges; the happy path stays clean by default.
- **Trace mode**: steps a sample record through the flow hop by hop, diffing the payload at each hop (added/changed/dropped fields highlighted).

AC checkboxes and panel state are session-only by design — the artifact is a reader over the spec, not a store.

## Spec schema (reference)

Top level: `meta` (id, title, purpose, trigger{type,detail}, success_criterion, version, compiled_at), `systems[]` (id, name, kind), `steps[]`, `edges[]` (from, to, payload_ref, kind: normal|failure|compensation, label), `payloads[]` (id, name, format, fields[]: name, maps_from, transform, rationale, fr_ref, phi), `traces[]` (label, hops[]: step, note, payload), `gaps` (unreferenced_frs, steps_without_frs, fields_missing_rationale, warnings).

Step: `id, seq, system, title, mode: sync|async, cadence: event|batch|schedule|manual, phi, path: happy|failure, why{summary, frs[]}, contract{protocol,endpoint,method,auth,payload_ref,notes}, behavior{retries,idempotency,ordering,timeout,notes}, provenance[]` (kind: decision|moment, ref, label, source — may be empty).

Embedded FR: `id, title, status, owner, depends_on[], owns[], why, what, acceptance_criteria[]` ({id: "AC-n"|null, text} — stable AC ids render as chips and key the checklist state), `out_of_scope[], open_questions[]` ({q, default} — defaults render as "ships by default"), `notes, adrs[]` ({id, title, status, date, summary, supersedes, superseded_by, ac_refs[]} — `ac_refs` badges the specific ACs an ADR constrains; `superseded_by` renders the chip struck-through). The renderer also tolerates the legacy shape (`context` string, plain-string ACs).

Every field is optional except ids and structural refs; the renderer degrades gracefully (empty states, not errors).
