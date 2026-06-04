---
id: IDEA-0012
title: Web-hosted human review console (FR-0017/FR-0018 client)
status: parked
tags:
- human-review
- web-ui
- hitl
- slack-follow-on
size: M
created: '2026-05-29'
updated: '2026-05-29'
last_reviewed: '2026-05-29'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0012: Web-hosted human review console (FR-0017/FR-0018 client)

## Description

When the Slack client (FR-0019) strains under bulk triage, rich attribute editing, or graph-context review, add a **web-hosted console** that calls the same FR-0017 list/detail APIs and FR-0018 `apply_review_event` path. System of record stays the graph + `ReviewEvent` audit; the web app is an alternate client only.

## Originating context

Deferred from FR-0019 planning 2026-05-29. Originating FRs: ai-hub-poc FR-0017, FR-0018, FR-0019.

## Value hypothesis

When Slack modals strain, a thin web UI reuses the same read/write APIs without re-specifying graph semantics.

## Notes

(none)
