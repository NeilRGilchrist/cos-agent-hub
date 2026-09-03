---
id: IDEA-0022
title: Live extraction integration through LiteLLM gateway (representative tests on
  prod-ready infra)
status: promoted
tags:
- extraction
- litellm
- gateway
- integration-test
- live-eval
- layer-3
size: M
created: '2026-06-16'
updated: '2026-06-16'
last_reviewed: '2026-06-16'
promoted_to: FR-0037
pattern: null
archive_reason: null
---

# IDEA-0022: Live extraction integration through LiteLLM gateway (representative tests on prod-ready infra)

## Description

FR-0020 wired GatewayLLMClient but its unit tests mock the Protocol boundary with no real API calls. With Bedrock access provisioned, add an opt-in/CI-excluded live integration run that pushes a synthetic PHI-free transcript through the real gateway->Bedrock path, asserts a valid ExtractionEvent round-trips, and captures a baseline candidate JSON consumable by the FR-0028 rubric harness. depends_on FR-0009/FR-0020/FR-0028. Must decide whether to commit or dev-gate scripts/run_fathom_live.py.

## Originating context

CoS triage 2026-06-16. Bedrock now unblocked via LiteLLM gateway. Builds on merged FR-0020 (GatewayLLMClient, mocks boundary in tests), FR-0009 (agent), FR-0028 (offline rubric eval). Untracked local scaffolding to reconcile: scripts/run_fathom_live.py, config/extraction/ns-ltc-poc/, data/.

## Value hypothesis

Exercising the already-built gateway client against live Bedrock proves the extraction path works on prod-ready infra and yields baseline candidates that feed the FR-0028 rubric eval, closing the gap between mocked unit tests and real-model behaviour.

## Notes

(none)
