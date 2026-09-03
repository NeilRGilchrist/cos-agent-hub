---
id: IDEA-0032
title: The .venv-dispatch environment cannot collect tests, so dispatched agents cannot
  self-verify
status: parked
tags:
- dispatch
- tooling
- environment
- ai-hub-poc
- dx
size: S
created: '2026-08-12'
updated: '2026-08-12'
last_reviewed: '2026-08-12'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0032: The .venv-dispatch environment cannot collect tests, so dispatched agents cannot self-verify

## Description

(no description provided)

## Originating context

Surfaced by the FR-0058 maintainer run on 2026-08-12, which reported pytest collection failing with No module named pydantic in .venv-dispatch. Confirmed the same test files pass 100/100 under the ambient interpreter, so the failure is isolated to the dispatch virtualenv.

## Value hypothesis

Dispatched agents are told to run the gate before declaring work complete, but cannot run the test suite at all, so every agent reports an unverifiable result and the verification burden shifts to the human on every single FR. The ambient interpreter runs the same suite green, so this is a provisioning gap rather than a code defect and should be cheap to close.

## Notes

(none)
