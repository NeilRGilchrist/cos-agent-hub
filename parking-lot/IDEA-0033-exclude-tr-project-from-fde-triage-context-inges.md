---
id: IDEA-0033
title: Exclude TR project from FDE triage context ingestion
status: parked
tags:
- fde-triage
- context-efficiency
- skill-tuning
- jira
size: S
created: '2026-08-13'
updated: '2026-08-13'
last_reviewed: '2026-08-13'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0033: Exclude TR project from FDE triage context ingestion

## Description

(no description provided)

## Originating context

FDE triage session 2026-08-13. All 30 backlog tickets showed a 'Defect: TR-xxxx [Done]' link whose only content was the Jira Service Desk landing record and an automated 'moved to FDE for review' comment. Confirmed by the human as expected behaviour: TR is purely a JSD landing page, not a routing signal. The skill should specify that TR-project issuelinks and their automation comments are ignored during intake reading, gap analysis, and OOB screening.

## Value hypothesis

Every FDE ticket carries a mirrored TR link plus a boilerplate automation comment; skipping TR entirely cuts roughly one wasted link and comment per ticket (30/session at present) with zero information loss, and removes a recurring false signal that the ticket was 'already handled'.

## Notes

(none)
