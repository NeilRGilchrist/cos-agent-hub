# Agent Team Workspace Hub

A workspace for running spec-driven agent crews across multiple projects, with a parking lot for cross-project ideas and a pattern catalog for compounding-value extraction.

## Layout

```
.
├── hub/                    cross-project state (projects.yaml, FR-INDEX.json, PATTERN-INDEX.md)
├── parking-lot/            IDEA-NNNN files — ideas not ready to be projects yet
├── patterns/               PATTERN-NNNN files — reusable shapes extracted from clusters
├── projects/               bootstrapped projects (default location for new ones)
├── template/               bootstrap source — used by scripts/bootstrap.sh
├── scripts/                hub-level scripts
│   ├── bootstrap.sh        stand up a new project
│   ├── hub-index.py        rebuild cross-project FR index
│   ├── parking.py          IDEA CRUD
│   └── patterns.py         PATTERN CRUD
├── .claude/commands/       hub-level slash commands (/cos, /park, /promote, /patterns, /hub-status)
├── .gitignore              local override ignores (AGENTS.override.md, CLAUDE.local.md)
└── CLAUDE.md               hub-level working agreement
```

## Three-layer architecture

```
              ┌──────────────────────┐
              │   Workspace Hub      │  ← unstructured input, parking lot, pattern catalog
              │   (this directory)   │
              └──────────┬───────────┘
                         │ /promote, /bootstrap
                         ▼
              ┌──────────────────────┐
              │   Projects           │  ← spec-driven, role-based work
              │   projects/<name>/   │
              └──────────┬───────────┘
                         │ /architect, /developer, /reviewer
                         ▼
              ┌──────────────────────┐
              │   Code + Tests       │  ← adversarial separation, gated by CI
              └──────────────────────┘
```

The hub is where you start. Drop unstructured thoughts into `/cos`. The Chief of Staff triages them — chat answer, park, promote to a new FR or project, or surface as a pattern signal. Patterns surface from clusters of parked ideas *and* recurring active FRs across projects, so even ideas that bypass the parking lot still feed pattern detection.

## Quickstart

Park an idea:

```
/park I keep wishing every project had a typed config loader with env-var fallback
```

Stand up a new project:

```
scripts/bootstrap.sh helena-emr-mapper "Bidirectional EMR transformation layer." --stack python
```

Triage unstructured input:

```
/cos I want to start tracking which integrations expire and notify me before they break
```

See the workspace at a glance:

```
/hub-status
```

## Scripts (hub-level)

- `scripts/bootstrap.sh <name> "<description>" [--stack python|node|none] [--private]` — bootstrap a project from `template/` and register it in `hub/projects.yaml`. Bare names land under `projects/<name>`; explicit paths can go anywhere.
- `scripts/hub-index.py` — walk every registered project and rebuild `hub/FR-INDEX.json`.
- `scripts/parking.py <add|list|show|promote|merge|archive|reflect|reindex>` — IDEA CRUD.
- `scripts/patterns.py <propose|list|show|accept|reject|mark-built|reindex>` — PATTERN CRUD.

The slash commands wrap these so you rarely call them directly.

## Slash commands (hub-level)

- `/cos <input>` — Chief of Staff. Triages unstructured input.
- `/park <idea>` — Fast-capture into the parking lot.
- `/promote IDEA-NNNN` — Promote a parked idea to an FR or new project.
- `/patterns [tag]` — Synthesize patterns from parked ideas + active FRs.
- `/hub-status` — Workspace overview.

Project-level slash commands (`/architect`, `/developer`, `/reviewer`, `/status`, `/gate`, `/escalate`) are bootstrapped for both harnesses under `template/.claude/commands/` and `template/.cursor/commands/`, with canonical command bodies in `template/.agent-team/commands/`.

## Portability model

- Hub workflows remain Claude Code-first (`CLAUDE.md` plus `.claude/commands/`).
- Bootstrapped projects are dual-harness: `AGENTS.md` is canonical, and `CLAUDE.md` is a thin pointer to it.
- Harness-specific metadata stays separate: `.claude/` for Claude Code and `.cursor/rules/` for Cursor glob-scoped activation.
- Per-developer overrides belong in `AGENTS.override.md` (gitignored) so local preferences never affect shared project behavior.

## Why this shape

Span-of-control over agent crews collapses if every input goes through the user's full attention. The hub layer absorbs unstructured input and only escalates back to the user as structured triage decisions. The parking lot prevents "lost time" framing — parked ideas are inputs to a synthesis machine, not deferred work. The pattern catalog captures compounding value the moment it becomes visible across projects, before you reinvent it a fourth time.
