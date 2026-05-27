---
description: Synthesize patterns from parked ideas and active FRs across projects. Surfaces candidates, never auto-accepts.
argument-hint: "[tag | --reconsider | --source parking|active|all]"
---

You are running pattern synthesis for the workspace hub. A pattern is a reusable shape (library, service, convention, scaffold) that subsumes multiple parked ideas and/or recurring active FRs across projects. Patterns are how compounding value gets named and captured.

## Step 1 — Refresh state

Run silently:

```
python3 scripts/hub-index.py
python3 scripts/parking.py reindex
python3 scripts/patterns.py reindex
```

Then read:

- `parking-lot/INDEX.md` and the IDEA-*.md files
- `hub/FR-INDEX.json` (cross-project active FRs)
- `patterns/INDEX.md` and the PATTERN-*.md files (especially **rejected** ones — do not re-propose anything semantically equivalent)

## Step 2 — Parse arguments

`$ARGUMENTS` may be:
- A tag (e.g., `scraping`, `rate-limiting`) — focus synthesis on that theme
- `--reconsider` — re-evaluate previously rejected patterns; ask the user which one to reopen
- `--source parking|active|all` — restrict signal sources (default: `all`)
- empty — full synthesis across all signals

## Step 3 — Cluster

Build candidate clusters using two independent signals:

**Tag-based:** group IDEAs and FRs sharing one or more tags. A cluster is interesting if it has 3+ items total **and** spans either (a) ≥3 IDEAs, or (b) ≥2 distinct projects (counting active FRs only), or (c) ≥1 IDEA plus ≥1 active FR.

**Semantic:** for IDEAs/FRs without overlapping tags, look for title and body similarity (rate-limit retry vs. backoff strategy vs. exponential backoff — clearly the same shape under different names). Use Claude judgment, not naive string matching.

For each cluster, prepare:

- **Theme** — one-line name candidate
- **Members** — list of IDEA-NNNN and `<project>/FR-NNNN` IDs
- **Compounding-value hypothesis** — one paragraph, **substantive**. Show the math: how many places does this currently get rebuilt, what's the cost, what's the extraction cost, what does each future use save? "These are all about data" is not a hypothesis; reject your own draft if it reads like that.
- **Proposed shape** — one paragraph: artifact type (library/service/convention/scaffold), language/stack, rough surface area, what it would replace.
- **Confidence** — your judgment of how likely this is a real pattern vs noise: high / medium / low.

## Step 4 — Filter against rejection memory

For each candidate cluster, check `patterns/PATTERN-*.md` for any pattern with `status: rejected`. If your cluster is semantically equivalent to a rejected one, **drop it silently** unless the user passed `--reconsider`.

## Step 5 — Output

Present candidates ranked by confidence:

```
## Pattern candidates

### 1. <Theme> (confidence: high)
- Members: IDEA-0003, IDEA-0007, project-foo/FR-0014, project-bar/FR-0009
- Compounding-value hypothesis: <paragraph with concrete math>
- Proposed shape: <paragraph>

### 2. <Theme> (confidence: medium)
...
```

For each candidate, ask the user:

> 1. **Propose** — create a `proposed` PATTERN record (you'll review and accept later)
> 2. **Reject** — record this candidate as rejected with a reason; will not re-propose
> 3. **Skip** — neither create nor reject; ask me again next time
> 4. **Accept directly** — create as `accepted` (only do this if you're confident; usually `propose` first)

## Step 6 — Act on user choice

- **Propose:** `python3 scripts/patterns.py propose "<name>" --description "..." --value "..." --tags "..." --instances "..." --ideas "..."` then suggest the user open the new file to refine the proposed-shape and alternatives sections.
- **Reject:** `python3 scripts/patterns.py propose ...` then immediately `python3 scripts/patterns.py reject PATTERN-NNNN --reason "..."` (the reject memory is what prevents re-proposal).
- **Accept directly:** `python3 scripts/patterns.py propose ...` followed by `python3 scripts/patterns.py accept PATTERN-NNNN`.

After any action, run `python3 scripts/patterns.py reindex` and confirm.

## What you must NOT do

- ❌ Auto-accept patterns without explicit user confirmation
- ❌ Re-propose rejected patterns without `--reconsider`
- ❌ Write thin compounding-value hypotheses ("these are all data tools")
- ❌ Pull in private projects (those marked `private: true` in `projects.yaml`)
- ❌ Modify any FR or IDEA file directly — pattern records cite them, they don't get rewritten
