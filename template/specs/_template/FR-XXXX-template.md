---
id: FR-XXXX
title: <one-line title>
status: draft  # draft | ready | in-progress | in-review | merged | deprecated | blocked
owner: architect
depends_on: []  # list of FR IDs this depends on
tags: []  # optional kebab-case themes; used for cross-project pattern detection
owns: []  # list of POSIX-style globs (relative to repo root) the Developer is permitted to write/edit/create. Empty list means "footprint not yet declared"; the indexer will warn. Matched against `git ls-files`. `**` matches any number of path segments. Absolute paths and `..` segments are rejected.
reads: []  # list of POSIX-style globs the Developer's implementation reads or consumes. Documentation only today; reserved for follow-on cross-commit-warning tooling. Same syntax rules as `owns`.
derived_from: null  # optional "IDEA-NNNN" if this FR was promoted from a parked idea
pattern: null  # optional "PATTERN-NNNN" if this FR is recognized as an instance of a hub-level pattern
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# FR-XXXX: <title>

## Why

One paragraph. What user/business problem does this solve? Who feels the pain today?

## What

One paragraph. What will exist after this FR is implemented that doesn't exist now? Stay at the behavior level — not implementation.

## Acceptance criteria

Each AC must be independently testable. Number them; the numbers are stable for the life of the FR.

- **AC-1:** <Specific, testable statement. E.g., "Given a malformed input X, the system returns error code Y with message Z.">
- **AC-2:** <...>
- **AC-3:** <...>

## Out of scope

Explicit list. Anything you can imagine a Developer accidentally implementing belongs here.

- <Item that is NOT covered by this FR>
- <Item that belongs to a different FR — link it: "see FR-YYYY">

## Open questions

If unresolved, propose a default. The default is what gets implemented if the FR ships without further clarification.

- **Q:** <question>
  - **Default:** <what to do absent further input>

## Dependencies

If this FR depends on others, explain the nature of the dependency:

- **FR-YYYY** — needed because <reason>

## Notes

Anything that helps a Developer understand context but isn't itself an acceptance criterion. Keep it short.

## Changelog

- YYYY-MM-DD: created
