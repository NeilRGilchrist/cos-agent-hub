---
id: IDEA-0003
title: reconcile-merged should fetch before flipping
status: parked
tags:
- dispatcher
- workflow
- reconcile
- robustness
size: S
created: '2026-05-06'
updated: '2026-05-06'
last_reviewed: '2026-05-06'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0003: reconcile-merged should fetch before flipping

## Description

(no description provided)

## Originating context

Discovered 2026-05-06 during the FR-0006/FR-0008 reconcile: local main was at cfecd66 while origin/main had moved to 96ed87e (squash-merges from PRs #10/#11). reconcile-merged committed the status flips locally, then git push rejected because local was behind. Required a manual git rebase with three conflicts (FR-0006, FR-0008, INDEX.md). Fix: add git fetch origin main + behind-check at the top of reconcile_merged_prs(); refuse to flip (with a clear message) if local main is behind origin/main.

## Value hypothesis

Prevent rebases and push conflicts when reconcile-merged runs on a local main that is behind origin/main. One fetch check, one guard — saves a manual rebase per merge cycle.

## Notes

(none)
