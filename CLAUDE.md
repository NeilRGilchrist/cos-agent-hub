# Workspace Hub — Working Agreement

This file is read by every agent at the start of every session in the hub directory. The hub is the cross-project layer: it owns the parking lot, the pattern catalog, and the project registry. Individual projects (under `projects/<name>/` or registered out-of-tree in `hub/projects.yaml`) have their own `CLAUDE.md` / `AGENTS.md` files that govern their internal work.

## What this hub is for

The hub is where unstructured ideas, half-formed plans, and pattern signals get triaged. Three things live here that don't belong inside any single project:

1. **The parking lot** (`parking-lot/`) — ideas you want to keep but aren't ready to act on
2. **The pattern catalog** (`patterns/`) — reusable shapes extracted from clusters of parked ideas or recurring active FRs across projects
3. **The project registry** (`hub/projects.yaml` + `hub/FR-INDEX.json`) — denormalized cross-project data that lets the Chief of Staff and the pattern detector see the whole system at once

When you're working *inside a project* (`projects/<name>/` or any path registered in `hub/projects.yaml`), the role-based work happens there. When you're working *at the hub level* (here), you're triaging, reflecting, or surfacing patterns.

Hub operations are Claude Code / Cowork-first by design. Bootstrapped projects are the portability layer: they use `AGENTS.md` as canonical content and add harness-specific wrappers under `.claude/` and `.cursor/`.

## AlayaCare engagement context

This hub is provisioned for AlayaCare FDE work. The default project shape (under `template/`) assumes PHIPA-regulated PHI may be in scope. The PHI hygiene block in `template/AGENTS.md`, the hooks in `template/.agent-team/hooks/`, and the wired `template/.claude/settings.json` are the defensive defaults. Projects that genuinely do not touch PHI can opt out by editing `.claude/settings.json` after bootstrap — but the default is opt-in, on purpose.

Real PHI never enters this hub directory, this chat, or any commit. Use ticket IDs (FDE-XXXX), FR IDs (FR-XXXX:AC-Y), and synthetic identifiers ("Patient A", "Provider 1"). See `hub/docs/PORTING-NOTES.md` for the full hygiene rationale and the original Pattern 2 Hybrid port history.

## Cross-platform support

All hub-level scripts are Python and work natively on Windows, macOS, and Linux:

- `scripts/bootstrap.py` — project creation and upgrade (cross-platform; `bootstrap.sh` is a thin bash wrapper for backward compatibility)
- `scripts/hub-index.py`, `scripts/parking.py`, `scripts/patterns.py` — all Python, no platform restrictions

Prerequisites: Python 3.11+ and `pyyaml` (`pip install pyyaml`). See `GETTING-STARTED.md` for full setup instructions.

## Hub-level slash commands

- `/cos <unstructured input>` — Chief of Staff. Triages unstructured input into chat / park / promote / pattern-signal. Always proposes-and-confirms before dispatching.
- `/park <one-line idea>` — Fast-capture into the parking lot without going through full triage. Captures title, tags, size, and a value hypothesis.
- `/promote IDEA-NNNN` — Promote a parked idea to an active FR (in an existing project) or bootstrap a new project. Updates bidirectional links.
- `/patterns [tag]` — Synthesize patterns from parked ideas and active FRs across projects. Surfaces candidates; never auto-accepts.
- `/hub-status` — Compact dashboard: projects, parking lot, patterns, recent escalations.

## Hub-level scripts

```
python scripts/bootstrap.py <name> "<description>" --stack python|node|none   # new project
python scripts/bootstrap.py --upgrade <project-path>                          # upgrade existing project
python scripts/hub-index.py                                                   # rebuild cross-project FR index
python scripts/parking.py <add|list|show|promote|merge|archive|reflect|reindex>
python scripts/patterns.py <propose|list|show|accept|reject|mark-built|reindex>
```

## Universal rules (apply at hub and inside projects)

1. **Spec graph is the source of truth.** Every change traces to a Functional Requirement (FR) and one or more Acceptance Criteria (AC). Even at the hub level, anything that becomes work eventually traces back to an FR somewhere.
2. **Stable IDs only.** FRs are `FR-XXXX` (project-scoped). IDEAs are `IDEA-NNNN` (hub-scoped). PATTERNs are `PATTERN-NNNN` (hub-scoped). IDs are never reused.
3. **Bidirectional links.** When an IDEA is promoted to an FR, both records reference each other (`derived_from`, `promoted_to`). When an FR is recognized as a pattern instance, both records reference each other (`pattern`, `instances`). The slash commands and CRUD scripts maintain these — don't edit by hand.
4. **Propose-and-confirm at the hub.** `/cos`, `/promote`, and `/patterns` always propose actions and wait for explicit confirmation before invoking anything. There is no auto-mode.
5. **Compounding-value discipline.** Patterns require a substantive compounding-value hypothesis. "These are all about data" is not a hypothesis. Concrete claims with rough math.
6. **Serial execution.** Hub scripts (`hub-index.py`, `parking.py`, `patterns.py`) assume serial execution — they read, mutate, and rewrite shared files without locking. Do not run them in parallel; concurrent invocations can corrupt indexes or lose writes.
7. **PHI never enters chat or commits.** See engagement context above and `template/AGENTS.md` for project-level enforcement.

## Files you should never edit without explicit human approval

- `template/.agent-team/**` — role definitions, escalation rules, and hooks (project-level defaults)
- `template/scripts/{deploy-gate.py,index-specs.py,agent-status.py}` — the merge gate and indexer (project-level)
- `scripts/{bootstrap.py,bootstrap.sh,hub-index.py,parking.py,patterns.py}` — hub-level scripts
- `.claude/commands/**` and `template/.claude/commands/**` — the slash commands encoding role contracts
- `template/AGENTS.md` and `template/CLAUDE.md` — project-level working agreement and Claude pointer
- This file (`CLAUDE.md`)

If a task seems to require editing these, escalate first.
