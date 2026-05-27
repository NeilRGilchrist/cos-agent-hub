---
description: Fast-capture an idea into the parking lot without going through full /cos triage.
argument-hint: "<one-line idea>"
---

You are doing a fast-capture into the parking lot. The user is in the middle of something else and doesn't want to context-switch into full triage. Be brief.

User's input: `$ARGUMENTS`

If `$ARGUMENTS` is empty, ask: "What's the idea?"

## Step 1 — Quick overlap check

Read `parking-lot/INDEX.md`. If any existing IDEA's title is a near-match (>60% semantic similarity), say:

> Similar to **IDEA-NNNN** — `<title>`. Add anyway, or merge into that one?

Wait for an answer before proceeding.

## Step 2 — Extract metadata

From the input:

- **Title** — one line, ≤80 chars. If the input is already short, use it. Otherwise compress.
- **Description** — one paragraph. Use the user's input as the seed; expand only what's clearly implied.
- **Tags** — 2–4 kebab-case nouns/themes. Choose from existing tags in `parking-lot/INDEX.md` where applicable to keep the tag space small. Don't invent fancy new tags for things adjacent to existing ones.
- **Size** — XS (afternoon), S (a day or two), M (a week), L (multi-week). Default M if unclear.
- **Value hypothesis** — one sentence. If the input doesn't contain one, **ask the user for one** rather than fabricating. The hypothesis is the one piece of friction that distinguishes this from a Notion brain-dump; don't skip it.

## Step 3 — Create the IDEA

Run:

```
python3 scripts/parking.py add "<title>" \
  --description "<paragraph>" \
  --tags "<tag1,tag2>" \
  --size <size> \
  --value "<one-sentence hypothesis>" \
  --context "captured via /park during <brief context: what we were doing>"
```

Confirm the IDEA-NNNN that was assigned.

## Step 4 — Tag-cluster trigger

After creation, count how many parked IDEAs share each tag. If any tag now has **3+ parked ideas**, mention it once:

> Tag `#<tag>` now has N parked ideas — consider running `/patterns <tag>` when you have a moment.

Do not run synthesis automatically.

## What you must NOT do

- ❌ Skip the value hypothesis (ask the user if missing — don't fabricate)
- ❌ Mint new tags when an existing tag fits
- ❌ Run pattern synthesis from this command — that's `/patterns`
- ❌ Promote the idea to an FR or project — that's `/promote`
