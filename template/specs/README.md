# Specs

This directory contains the spec graph: every Functional Requirement (FR) the project tracks, plus architectural decision records.

## Layout

```
specs/
├── _template/
│   └── FR-XXXX-template.md      # Copy this to start a new FR
├── _escalations/                 # Optional: escalation files when PR comments aren't enough
├── decisions/                    # ADRs for cross-FR architectural choices
├── FR-0001-<slug>.md
├── FR-0002-<slug>.md
└── ...
```

## ID allocation

- FR IDs are allocated sequentially: `FR-0001`, `FR-0002`, ...
- IDs are **never reused**, even when an FR is deprecated
- ACs within an FR are numbered `AC-1`, `AC-2`, ... and are also never reused (deprecated ACs stay in the file marked `[DEPRECATED]`)

This stability is what makes `@covers FR-XXXX:AC-Y` annotations meaningful across time.

## Index

Run `python3 scripts/index-specs.py` to regenerate `specs/INDEX.md` — a flat listing of all FRs with status, owner, and dependencies. This is the cheapest way for an agent to orient before picking up work.

## Writing a new FR

1. Copy `_template/FR-XXXX-template.md` to `FR-NNNN-<short-slug>.md` (next available number)
2. Fill in frontmatter
3. Write the four required sections: Why, What, Acceptance criteria, Out of scope
4. Set `status: ready` only when all four are complete and acceptance criteria are individually testable
5. Run `python3 scripts/index-specs.py`
