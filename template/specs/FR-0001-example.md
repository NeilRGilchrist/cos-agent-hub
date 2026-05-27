---
id: FR-0001
title: Example FR for template validation
status: ready
owner: architect
depends_on: []
tags: [example]
derived_from: null
pattern: null
owns: []
reads: []
created: 2026-04-28
updated: 2026-04-28
---

# FR-0001: Example FR for template validation

## Why

This FR exists only to demonstrate the spec format and validate the indexer/gate scripts. Delete it before using this template for real work.

## What

A trivial example showing the structure agents should expect.

## Acceptance criteria

- **AC-1:** The indexer parses this file successfully and includes it in INDEX.md
- **AC-2:** The deploy gate, when this FR is in `in-review` status, requires `@covers FR-0001:AC-1` and `@covers FR-0001:AC-2` somewhere in tests/

## Out of scope

- Anything real
- Demonstrating CI configuration

## Open questions

- **Q:** Should this file be auto-deleted on first real use?
  - **Default:** No; leave it as docs.

## Notes

Delete this file once you have at least one real FR.

## Changelog

- 2026-04-28: created
