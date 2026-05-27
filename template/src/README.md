# Source

Production code lives here. Owned by the Developer role.

The directory layout below this is project-specific — establish it via your first ADR or in `CLAUDE.md` once you've picked a stack. Until then, this README and the `.gitkeep` exist only so the directory exists and the role files can reference it without lying.

## What goes here

Production code that satisfies acceptance criteria from FRs in `specs/`.

## What does NOT go here

- Tests (those live in `tests/` and are owned by Reviewer)
- Spec content (that lives in `specs/` and is owned by Architect)
- Build/CI scripts (those live in `scripts/` and are not modifiable by Developer)
