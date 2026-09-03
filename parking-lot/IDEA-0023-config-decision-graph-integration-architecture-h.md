---
id: IDEA-0023
title: Config Decision Graph integration architecture (hybrid model)
status: parked
tags:
- extraction
- config-graph
- architecture
- multi-pass
- review-surface
- work-streams
size: L
created: '2026-06-19'
updated: '2026-06-19'
last_reviewed: '2026-06-19'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0023: Config Decision Graph integration architecture (hybrid model)

## Description

Design a hybrid integration architecture (option C from CoS triage) for bringing the Config Decision Graph into ai-hub-poc. Three layers identified:

- **Layer 1** (existing): General-purpose extraction (Decision, Risk, Commitment, etc.) from conversations — stays in ai-hub-poc as-is
- **Layer 2** (existing externally): Config-specific extraction (`DecisionRecord[]` across 150+ decisions, 16 phases) via 2-pass LLM pipeline — lives as a separate service on the bus
- **Layer 3** (new, `model.json`): Deterministic rule engine (facts → rules → gated actions) — lives alongside Layer 2 as post-extraction enrichment, no LLM needed

**ai-hub-poc changes required:**
1. Multi-pass extraction orchestration (parallel extraction capability, addressing context-limit concerns)
2. KG node types for config decisions, facts, rules, and gated actions (FR-0010 extension)
3. Review surface must show the full provenance chain: extracted conversational decision → config decision ID → derived facts → gated actions
4. Work-stream / owner dimension: a single call may surface decisions belonging to multiple work streams with different owners and review queues

**Key design question for architect session:** How does the review surface present the relationship between "the LLM thinks this decision was settled on the call" and the specific configuration task it maps to? The reviewer needs to see the chain, not just approve extracted text in isolation.

**Scaling concern:** model.json currently covers 3 corners (~30 facts, 12 rules, 7 actions). Full AlayaCare scale is 16+ phases, 150+ decisions. The deterministic engine scales fine, but authoring/maintaining the full graph and extracting against it are the real costs.

## Originating context

CoS triage session 2026-06-19; references Confluence ConfigurationDecision KG Shape page, config_extraction_output_schema.md, model.json (funder/VV/core corners), and claude_desktop 2-pass pipeline README

## Value hypothesis

Hybrid architecture where ai-hub-poc gains multi-pass orchestration and KG decision-graph types while config extraction prompts and the deterministic rule engine are a separate bus service enables the review surface to show full provenance chain from extracted decision to gated config action, scaling independently across 150+ decision IDs and work-stream ownership.

## Notes

### Reference materials
- **Confluence:** [ConfigurationDecision Knowledge Graph Shape](https://alayacare.atlassian.net/wiki/spaces/FDE/pages/6851821592) — worked example (tenant structure SO/GA/MO/MOGA) showing full-depth decision modeling
- **config_extraction_output_schema.md:** `OneDrive/AI & Automation Projects/Onboarding Redesign/usa_enablement_config/config_extraction_output_schema.md` — 150+ decision IDs, DecisionRecord schema, polymorphic DecisionValue, Mechanism taxonomy
- **2-pass pipeline:** `OneDrive/.../usa_enablement_config/prompts/claude_desktop/README.md` — Context Extractor (Pass 1) → Decision Extractor (Pass 2), rationale for multi-pass
- **model.json:** Attached to CoS session — deterministic config decision graph with corners (funder, visit_verification_agent, core_product), scope tiers, facts, rules, gated actions
- **ai-hub-poc FRs touched:** FR-0007 (extraction event contract), FR-0008/FR-0034 (extraction config), FR-0009 (extraction agent), FR-0010 (KG projection), FR-0028 (eval harness), FR-0037 (live extraction)

### Architect session entry point
When promoted, run `/architect` in ai-hub-poc with focus on:
1. How the review surface presents the extraction→decision→fact→action chain
2. Multi-pass orchestration architecture (parallel extraction agents)
3. Work-stream ownership dimension on config decisions
4. KG schema extensions for decision-graph node types
5. Boundary between ai-hub-poc platform capabilities and the config-specific service
