---
id: IDEA-0015
title: Template drift detection and back-propagation lifecycle
status: archived
tags:
- template
- settings
- drift
- back-propagation
- developer-experience
size: M
created: '2026-05-29'
updated: '2026-09-03'
last_reviewed: '2026-09-03'
promoted_to: null
pattern: null
archive_reason: Superseded by IDEA-0034 (reverse-leg harvest); same drift/back-propagation
  theme, more developed. Unique content (stack-aware starter settings.local.json)
  folded into IDEA-0034.
---
# IDEA-0015: Template drift detection and back-propagation lifecycle

## Description

(no description provided)

## Originating context

Observed during ai-hub-poc FR-0014: dev-FR-0014 worktree required an 88-line settings.local.json built through trial-and-error. Same class of problem as archived IDEA-0001 (dispatch.py port), whose open question 1 (canonical vs drift) was deferred.

## Value hypothesis

Ship stack-aware starter settings.local.json in the template and add a diff/sync mechanism to bootstrap.py --upgrade so project-level improvements to agent infrastructure (permissions, hooks, role tweaks) can be reviewed and absorbed into the template, reducing per-project setup friction and compounding quality across all bootstrapped projects.

## Notes

- **Motivating instance (2026-06-03):** FR-0027 (ai-hub-poc) performs a manual sync-forward of template hooks, settings, and handoff-protocol strengthening to the POC. This is exactly the class of drift/sync operation that IDEA-0015 would automate — manual identification of template improvements, manual copy to the project, manual verification of compatibility.
