---
id: IDEA-0028
title: Fix agent-status.py cp1252 crash on Windows (Unicode arrow glyph)
status: parked
tags:
- template
- windows
- cross-platform
- tooling
- bug
size: S
created: '2026-07-28'
updated: '2026-07-28'
last_reviewed: '2026-07-28'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0028: Fix agent-status.py cp1252 crash on Windows (Unicode arrow glyph)

## Description

projects/ai-hub-poc scripts/agent-status.py crashes under Windows cp1252 when printing a right-arrow glyph. Architects worked around it with PYTHONIOENCODING=utf-8. Fix should force UTF-8 stdout in the script itself. Likely also present in the template copy under template/scripts/agent-status.py (on the do-not-edit list, so needs human approval to change).

## Originating context

Surfaced during CoS-dispatched architect runs for FR-0052/0053/0054 on 2026-07-28.

## Value hypothesis

Removes a per-invocation Windows workaround and prevents silent script crashes for any Windows-based agent run.

## Notes

(none)
