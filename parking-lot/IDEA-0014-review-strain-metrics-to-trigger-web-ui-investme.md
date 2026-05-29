---
id: IDEA-0014
title: Review strain metrics to trigger web UI investment
status: parked
tags:
- human-review
- observability
- metrics
- slack-follow-on
size: S
created: '2026-05-29'
updated: '2026-05-29'
last_reviewed: '2026-05-29'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0014: Review strain metrics to trigger web UI investment

## Description

Instrument the review pipeline so we know **when** to invest in IDEA-0012: queue depth and age, `edit_attributes` rate vs confirm-only, Slack modal open/submit/abandon ratios, and reviews per tenant per day. Feed dashboards or simple thresholds (e.g. sustained pending > N) to trigger a promote-to-FR decision.

## Originating context

Deferred from FR-0019. Originating FRs: ai-hub-poc FR-0017 (queue), FR-0019 (Slack interactions).

## Value hypothesis

Evidence-driven decision when Slack UX is insufficient.

## Notes

(none)
