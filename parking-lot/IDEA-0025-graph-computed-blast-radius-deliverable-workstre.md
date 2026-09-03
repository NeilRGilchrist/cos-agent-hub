---
id: IDEA-0025
title: 'Graph-computed blast radius: Deliverable/Workstream schema + cross-customer
  risk propagation'
status: parked
tags:
- knowledge-graph
- schema
- risk-propagation
- layer-4
- impact-analysis
- cross-customer
size: L
created: '2026-07-27'
updated: '2026-07-27'
last_reviewed: '2026-07-27'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0025: Graph-computed blast radius: Deliverable/Workstream schema + cross-customer risk propagation

## Description

(no description provided)

## Originating context

Surfaced while drafting FR-0046 (KB Query MCP Server) in ai-hub-poc. FR-0046 ships the read surface but deliberately defers graph-computed impact: needs new Deliverable/Workstream nodes, typed THREATENS/DEPENDS_ON/PROPAGATES_TO edges, and a propagation-inference layer. Requires an ADR.

## Value hypothesis

Enable the graph itself to answer 'if Risk A slips, which workstreams across customers are implicated?' and proactively infer that a Risk in Customer A>Workstream B implies corresponding Risks in A>C or B>A.

## Notes

(none)
