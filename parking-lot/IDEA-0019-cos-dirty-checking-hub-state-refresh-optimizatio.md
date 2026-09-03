---
id: IDEA-0019
title: CoS dirty-checking hub-state refresh optimization
status: promoted
tags:
- template
- cos
- performance
- hub-scripts
- overhead-reduction
size: S
created: '2026-06-01'
updated: '2026-06-01'
last_reviewed: '2026-06-01'
promoted_to: ai-hub-poc/FR-0023
pattern: null
archive_reason: null
---
# IDEA-0019: CoS dirty-checking hub-state refresh optimization

## Description

### Problem

Every `/cos` invocation runs a full hub-state refresh: 3 reindex scripts (`hub-index.py`, `parking.py reindex`, `patterns.py reindex`) plus reads of 5 hub-state files (`FR-INDEX.json`, `parking-lot/INDEX.md`, `patterns/INDEX.md`, `hub/projects.yaml`, and the CoS command file itself). This costs ~8 tool calls per invocation.

Across the 5 CoS sessions observed in IDEA-0016's analysis, this totaled ~40 tool calls for hub-state refresh. In most cases, nothing had changed between CoS invocations — the indexes were already up to date. The reindex scripts are serial and blocking (per CLAUDE.md), making this a synchronous bottleneck at the start of every triage interaction.

### Proposed solution

Add timestamp-based dirty-checking to the CoS command and the reindex scripts:

1. **Marker file approach:** After each successful reindex, write a `.last-reindex` marker file (or per-script markers like `.last-reindex-parking`, `.last-reindex-patterns`, `.last-reindex-hub`) containing the timestamp of the last run.

2. **Dirty check logic:** Before running a reindex script, compare the modification time of the relevant source files against the marker:
   - `parking.py reindex`: check mtime of `parking-lot/*.md` vs `.last-reindex-parking`
   - `patterns.py reindex`: check mtime of `patterns/*.md` vs `.last-reindex-patterns`
   - `hub-index.py`: check mtime of `hub/projects.yaml` and project-level `FR-INDEX.json` files vs `.last-reindex-hub`

3. **Skip if clean:** If no source files are newer than the marker, skip the reindex and report "index is current."

4. **CoS command update:** Modify the CoS slash command (`.claude/commands/cos.md` and `.cursor/commands/cos.md`) to use the dirty-checking flow instead of unconditionally running all 3 scripts.

### Where it belongs

Hub-level script and command improvements:
- `scripts/hub-index.py` — add dirty-checking logic
- `scripts/parking.py` — add dirty-checking to `reindex` subcommand
- `scripts/patterns.py` — add dirty-checking to `reindex` subcommand
- `.claude/commands/cos.md` — update CoS startup sequence
- `.cursor/commands/cos.md` — update CoS startup sequence
- New: `.last-reindex-*` marker files (add to `.gitignore`)

## Originating context

Derived from IDEA-0016 ("Agent effectiveness analysis tooling") transcript analysis of 7 parent sessions and 13 subagent transcripts (2026-05-28 through 2026-06-01).

- `derived_from: IDEA-0016`
- Key finding: "Index reindexing at CoS startup: `hub-index.py`, `parking.py reindex`, `patterns.py reindex` are serial and blocking. If nothing changed since last run, this is pure waste. No incremental/dirty-checking mechanism exists."
- Quantified: ~40 redundant tool calls across 5 CoS sessions, ~8 tool calls per invocation

## Value hypothesis

Cuts **~60% of CoS startup overhead** by skipping reindexing when source files haven't changed. Across the observed 5 CoS sessions per week, this saves ~24 tool calls/week (~5 tool calls per skipped reindex × ~5 sessions × ~60% skip rate). The savings compound: as the parking lot and pattern catalog grow, reindex time increases, making the dirty-check optimization more valuable over time.

**ROI estimate:** ~1-2 hours of script modification produces ongoing savings of ~24 tool calls/week. Payback within 1 week of normal CoS usage.

## Notes

- All 3 reindex scripts and both CoS command files are protected (per CLAUDE.md) — implementation requires explicit human approval
- The dirty-check logic should be simple: Python `os.path.getmtime()` comparisons, no external dependencies
- Alternative approach: git-hash-based checking (compare `git log -1 --format=%H -- parking-lot/` against stored hash) — more robust but adds git dependency to the check itself
- The marker files should be `.gitignore`d since they're local state
- Per CLAUDE.md serial execution rule, dirty-checking should be done before deciding to run each script, not in parallel
