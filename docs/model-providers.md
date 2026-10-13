# Switching models

The app has no model or vendor built into its code. Two kinds of model plug in through settings:

| Role | Contract | Where it is used | Providers |
|---|---|---|---|
| **Classifier** (decision model) | TypeSafe **System One**: `POST …/systemone` with `state` and typed `questions` (`choice`, `score`, `noul`) | triage action, severity, auto-remediable, noise (`TRIAGE__DECISION__PROVIDER=systemone`) | Kev (local, fine-tuned or base), **Jev** (TypeSafe), **Microsoft-Decision-1** (Foundry) |
| **LLM** (generative) | OpenAI-compatible **chat completions** with JSON output, validated in code | triage `llm` provider (`TRIAGE__DECISION__PROVIDER=llm`); health-review analyst (`INSIGHTS__ANALYST__*`) | vLLM (Qwen today), Ollama, LM Studio, llama.cpp, OpenAI, Azure OpenAI, LiteLLM-style gateways |

Both kinds are reached through one adapter each:
- the classifier through `services/agent-runtime/.../adapters/systemone.py`;
- the LLMs through the shared `packages/llm` (`opstriage_llm.ChatJsonClient`).

To switch a model, change environment variables, or use one of the compose overlays below. No code changes are needed.

## Classifier: Kev, Jev or Microsoft-Decision-1

All three take the same request (`model`, `state`, `questions`) and return `answers` keyed by question name. Only three things differ:

| | Kev (DGX Spark) | Jev (TypeSafe) | Microsoft-Decision-1 (Foundry) |
|---|---|---|---|
| `TRIAGE__DECISION__BASE_URL` | `http://host.docker.internal:18003` (tunnel) | `https://api.typesafe.ai` | your Foundry resource endpoint |
| `TRIAGE__DECISION__PATH` | `/v1/systemone` | `/v1/systemone` | `/providers/microsoft/v1/systemone` |
| `TRIAGE__DECISION__MODEL` | `kev-latest` | `jev-latest` | **your deployment name** (not the model name) |
| `TRIAGE__DECISION__AUTH_MODE` | `none` | `bearer` (key in `TRIAGE__DECISION__API_KEY`) | `api_key` (header `api-key`) or `entra` (managed identity, scope `https://cognitiveservices.azure.com/.default`) |
| Compose overlay | `compose.kev.yaml` | `compose.jev.yaml` (needs `JEV_API_KEY` in `.env`) | `compose.decision1.yaml` (needs `DECISION1_ENDPOINT`, `DECISION1_DEPLOYMENT`, `DECISION1_API_KEY` in `.env`) |
| Confidence | `confidence` field (chance-normalised) | `confidence` field | not documented; the adapter uses the chosen option's probability |

```bash
docker compose -f compose.yaml -f compose.jev.yaml up -d agent-runtime          # Jev
docker compose -f compose.yaml -f compose.decision1.yaml up -d agent-runtime    # Microsoft-Decision-1
docker compose -f compose.yaml -f compose.kev.yaml up -d agent-runtime          # Kev
```

`services/agent-runtime/tests/test_systemone.py::test_switching_classifiers_is_configuration_only` pins each provider's URL, auth header and body.

**Calibrate thresholds per model.** The models report confidence on different scales:
- **Kev:** chance-normalised confidence. That's why `compose.kev.yaml` uses `config/policy.local-kev.yaml` (demo only, `min_action_confidence` 0.10).
- **Jev and Decision-1:** these overlays use `config/policy.example.yaml`, with the production thresholds.

Before any model triggers automated restarts, measure it on `evals/datasets/triage_kafka_v2.jsonl` with `opstriage-evals run`, and set its thresholds from that run.

Sources:
- System One API: <https://docs.typesafe.ai/api>
- Microsoft-Decision-1: <https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision>
- Kev: <https://github.com/jaredpalmer/kev> ("The API matches TypeSafe's System One")

## LLMs: any OpenAI-compatible endpoint

Each LLM role reads one endpoint from settings (`opstriage_llm.LlmEndpoint`):

| Setting (`TRIAGE__DECISION__…` or `INSIGHTS__ANALYST__…`) | Meaning |
|---|---|
| `BASE_URL`, `PATH` | Endpoint. `PATH` is usually `/v1/chat/completions`. |
| `MODEL` | Model, or the deployment name on Azure. |
| `AUTH_MODE`, `API_KEY`, `API_KEY_HEADER` | `none`, `bearer`, `api_key` (Azure OpenAI uses the header `api-key`) or `entra`. |
| `RESPONSE_FORMAT` | `json_schema` (strict structured output: vLLM, OpenAI, Azure OpenAI), `json_object` (JSON mode) or `none` (plain text; the schema goes into the prompt). |
| `EXTRA_BODY` | Model-specific request fields, as JSON. Example for Qwen on vLLM: `{"chat_template_kwargs":{"enable_thinking":false}}`. Use `{}` for other models. |
| `TEMPERATURE`, `MAX_TOKENS`, `TIMEOUT_SECONDS`, `ATTEMPTS`, `REPAIR_ATTEMPTS` | Generation and resilience settings. The health-review analyst repairs invalid output once by default; triage does not repair, to keep latency low. |

Model output is untrusted in every mode:
- it is validated with pydantic;
- health-review citations are checked against the digest;
- an unavailable or invalid model falls back to a safe result: the rules decision in triage, a rules-only report in health-review.

Examples, using health-review:

```bash
# Ollama on the Mac
INSIGHTS__ANALYST__BASE_URL=http://host.docker.internal:11434 INSIGHTS__ANALYST__MODEL=qwen3:8b \
INSIGHTS__ANALYST__RESPONSE_FORMAT=json_object INSIGHTS__ANALYST__EXTRA_BODY={}

# Azure OpenAI (v1 API)
INSIGHTS__ANALYST__BASE_URL=https://<resource>.openai.azure.com/openai INSIGHTS__ANALYST__PATH=/v1/chat/completions \
INSIGHTS__ANALYST__MODEL=<deployment> INSIGHTS__ANALYST__AUTH_MODE=api_key INSIGHTS__ANALYST__API_KEY=<key> \
INSIGHTS__ANALYST__EXTRA_BODY={}

# No model: reports contain only the code-checked findings
INSIGHTS__ANALYST__PROVIDER=none
```

Before switching the analyst, compare models on the planted-issue set. `evals/health_review_eval.py` takes the same endpoint options on the command line:

```bash
uv run python evals/health_review_eval.py --base-url http://127.0.0.1:11434 --model qwen3:8b \
  --response-format json_object --extra-body '{}'
```
