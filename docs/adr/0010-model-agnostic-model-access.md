# ADR 0010: Model-agnostic model access (classifiers and LLMs switch by configuration)

- Status: accepted (2026-10-13), requested by the product owner. The app must be able to switch models, and to switch the classifier between Kev, Jev and Microsoft-Decision-1.
- Related: ADR 0006 (Kev decision model), ADR 0009 (signatures plus an LLM for decisions), OTA-APP-002 (health review).

## Context

Two kinds of model are used:

1. **Classifiers**, which return typed decisions with probabilities. Kev, TypeSafe's Jev and Microsoft-Decision-1 all speak the System One contract (`state` plus typed `questions`). Only the endpoint, path, model name or deployment, and auth differ:
   - TypeSafe API docs (docs.typesafe.ai/api);
   - Microsoft Learn: "Deploy and use Microsoft-Decision-1", path `/providers/microsoft/v1/systemone`, `model` = deployment name, Entra ID or `api-key` header;
   - the Kev README: "The API matches TypeSafe's System One".
2. **LLMs**, which return generated JSON: the triage `llm` provider and the health-review analyst.

Before this ADR, the triage LLM adapter hard-coded Qwen's `chat_template_kwargs` and strict JSON-schema mode. A plain OpenAI or Azure OpenAI endpoint would reject that request body. The api-key auth mode used by Azure was also missing.

## Decision

1. **Classifiers stay behind the one System One adapter**, `adapters/systemone.py`.
   - It sends only the standard fields and tolerates a missing `confidence`.
   - Auth modes: `none`, `bearer`, `api_key` (header name configurable, `api-key` by default) and `entra`.
   - Overlays: `compose.kev.yaml`, `compose.jev.yaml` and `compose.decision1.yaml`. Keys are read from the git-ignored `.env`.
2. **New shared package `packages/llm` (`opstriage_llm`):** `LlmEndpoint` settings and `ChatJsonClient`, for any OpenAI-compatible chat completions endpoint. It provides:
   - **response formats:** `json_schema`, `json_object` or `none`;
   - **`extra_body`** for model-specific fields;
   - **auth modes:** none, bearer, api-key or Entra;
   - **retries** on transport errors, 429 and 5xx;
   - **output validation** with an optional repair round. Error messages never contain prompt or output text.
3. **Triage and health-review both use it.** Qwen's `enable_thinking: false` moves into configuration:
   - triage: `TRIAGE__DECISION__EXTRA_BODY` in `compose.qwen.yaml`;
   - health-review: `INSIGHTS__ANALYST__EXTRA_BODY`.
4. **Model output is untrusted in every mode.** Invalid or unavailable models degrade to safe results:
   - triage falls back to the rules decision;
   - health-review falls back to a rules-only report.
5. **Each model is measured before it is used:**
   - classifiers with `opstriage-evals run` on `evals/datasets/triage_kafka_v2.jsonl`; restart thresholds are calibrated per model;
   - analysts with `evals/health_review_eval.py`, which takes the same endpoint options.

## Consequences

- Switching models is configuration only; `docs/model-providers.md` has the settings for each provider.
- Tests pin the request shape per provider:
  - `test_switching_classifiers_is_configuration_only`;
  - the `packages/llm` tests cover each response mode, auth mode and repair;
  - the analyst tests cover `json_object` and `none` mode.
- **Confidence scales differ between classifiers** (Kev's is chance-normalised). Policy thresholds are not portable. Jev and Decision-1 use `policy.example.yaml` until they have been measured.
- **Entra auth** needs the `opstriage-llm[entra]` extra (azure-identity) in Azure images. Local images do not include it.
- **Native non-OpenAI APIs** (for example Anthropic's Messages API) are not adapters yet. They can be reached through an OpenAI-compatible gateway, or added later as another `Analyst` or `DecisionModel` adapter without touching the domain.
