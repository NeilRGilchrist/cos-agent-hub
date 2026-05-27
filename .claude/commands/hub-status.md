---
description: Show the workspace hub overview — projects, parking lot, patterns, recent escalations.
argument-hint: ""
---

You are reporting hub-level status. Read but don't modify.

## Step 1 — Read (no refresh by default)

By default, read the existing indexes without rerunning the indexers. If indexes seem stale or the user requests a refresh, run these manually first:

```
python3 scripts/hub-index.py
python3 scripts/parking.py reindex
python3 scripts/patterns.py reindex
```

Read:

- `hub/projects.yaml`
- `hub/FR-INDEX.json`
- `parking-lot/INDEX.md`
- `patterns/INDEX.md`
- For each registered project, read its `.agent-team/escalation-log.md` if present (catch human-bound escalations across projects)

## Step 2 — Render

Output a compact dashboard:

```
## Workspace overview — <date>

### Projects (<N>)
- <name> [stack] — N FRs (in-progress: X, in-review: Y, blocked: Z)
- ...

### Parking lot (<N> parked, <M> archived)
- N parked, oldest: IDEA-NNNN (created <date>, last reviewed <date>)
- Top tags by count: #tag1 (X), #tag2 (Y), #tag3 (Z)
- Stale (>90d not reviewed): <count>

### Patterns
- <N> proposed, <M> accepted, <K> built, <R> rejected
- (list accepted + proposed inline if ≤5 total)

### Recent human-bound escalations (last 7 days)
- <date> — <project> — <FR> — <trigger>
- ...
- (or "none")

### Suggested next action
<one line: most useful thing to do right now — e.g., "review stale parked ideas", "clear blocker on project-foo/FR-0014", "consider pattern PATTERN-0001 — 4 instances now exist">
```

Keep it terse. This is a status command, not a triage command. If the user wants to act on something, they can use `/cos`, `/promote`, `/patterns`, or jump into a project.

## What you must NOT do

- ❌ Modify any artifact (this is read-only)
- ❌ Recommend specific commits, code changes, or PRs (those are project-level concerns)
- ❌ Run pattern synthesis here — that's `/patterns`
