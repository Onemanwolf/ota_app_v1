# Kev fine-tune: Dynatrace + Kafka triage decision

Delta fine-tune of `jaredpalmer/kev-4b` on the production `action` question, trained locally on the DGX Spark
with Kev's own trainer (`kev.train --init_from`, the recipe of `skills/kev-finetune/scripts/kev_modal.py`
without Modal). The context and decision are in ADR 0009 and `evals/reports/strategy-comparison-*.md`.

Labels are **inferred** from the research runbook, not reviewed by people (open questions 7 and 12). The
training and evaluation states come from one scenario generator, so all scores here are in-distribution.

## 1. Build the records (Mac)

```bash
uv run python evals/kev_finetune/build_records.py
```

This writes two files, both git-ignored and deterministic:

- `data/kafka_triage.jsonl`: about 1,200 records, about 190 per label.
  - Built from `fixtures/kafka` scenarios with seed 424242; the eval set uses 20261012.
  - Includes minimal pairs (one fact changed, label flips): offline partitions, a second broker down, a full disk, many SSL clients, maintenance.
  - States are the production `build_state_text` output at 1,400 characters.
- `data/kafka_v2_dev.jsonl`: the 150-case eval set in Kev format.

Kafka log lines tokenize at about 2.9 characters per token. A 1,400-character state can therefore reach about
490 tokens, over the trainer's default 384-token state limit, so training passes `--max_state 512`. Serving
accepts 8,192 state tokens, so production states are not cut.

## 2. Split and train (Spark)

```bash
scp evals/kev_finetune/data/*.jsonl evals/kev_finetune/*.py timo@spark-337a.local:kev-ft/   # data -> kev-ft/data/
# split 70/15/15 by state with Kev's checker
docker exec kev python3 /kev/skills/kev-finetune/scripts/split_data.py /tmp/kevft-data/kafka_triage.jsonl --out ...
# training container: same image as the serving `kev` container, plus the training deps
docker stop qwen38-flash          # frees ~70 GB of unified memory; restart it afterwards
docker run -d --name kev-train --gpus all --ipc=host \
  -v ~/.cache/huggingface:/root/.cache/huggingface -v ~/kev-src:/src:ro -v ~/kev-ft:/work \
  --entrypoint bash vllm/vllm-openai:v0.29.0-aarch64 -c "sleep infinity"
docker exec kev-train bash -c 'cp -r /src /kev && cd /kev && pip install --only-binary=:all: -q \
  "transformers>=5.17" "peft>=0.21" "datasets>=3.0" "scikit-learn>=1.5" && pip install -q --no-deps --no-build-isolation -e .'
docker exec -d kev-train bash -c "cd /work && python3 train_local.py --data /work/data/split \
  --out /work/runs/kafka-triage-v1 --extra_dev /work/data/kafka_v2_dev.jsonl > /work/runs/kafka-triage-v1.out 2>&1"
```

`train_local.py` runs these steps:

1. Train with Kev's recipe:
   - learning rate 2e-5, batch 2 × accumulation 4, 1 epoch, `p_none_pair` 0.25
   - 2,000 public decision-v7 records replayed so the model does not forget its general skills
2. Fit the temperature on `calibration.jsonl`.
3. Score `development.jsonl` and the baseline on the same records, with a paired bootstrap.
4. Score the 150-case eval set (`--extra_dev`).
5. Run the forgetting check on 300 public decision-v7 development groups.

On the GB10 this runs at about 1.1 s per record with the reference DeltaNet kernels, which is about 55 minutes of training.

## 3. Serve and compare

```bash
docker run -d --name kev-kafka --gpus all -p 8005:8000 -e KEV_RUN=/work/runs/kafka-triage-v1/checkpoint \
  -v ~/.cache/huggingface:/root/.cache/huggingface -v ~/kev-src:/src:ro -v ~/kev-ft:/work \
  --entrypoint bash vllm/vllm-openai:v0.29.0-aarch64 -c 'cp -r /src /kev && cd /kev && \
  pip install --only-binary=:all: -q "transformers>=5.17" "peft>=0.21" "fastapi>=0.115" "uvicorn>=0.30" "typesafe-sdk>=0.6.0" && \
  pip install -q --no-deps --no-build-isolation -e . && python3 /work/serve_launch.py'
EXTRA_FORWARDS="18001:8001 18005:8005" scripts/kev-tunnel.sh install   # rewrites the LaunchAgent
uv run python evals/kev_finetune/compare_finetune.py --kev http://127.0.0.1:18005/v1/systemone
```

The base model keeps serving on 8003 (`kev`, shared with other projects). The fine-tune serves on 8005. The
stack switches to it through the Kev overlay alone:
`KEV_BASE_URL=http://host.docker.internal:18005 docker compose -f compose.yaml -f compose.kev.yaml up -d`.

## Results: run `kafka-triage-v1` (2026-10-12)

The run used 836 training records plus 2,000 replay records, 1 epoch and `--max_state 512`. Training took 50
minutes on the GB10, and scoring took another 10 minutes.

| Measure | Base kev-4b | kafka-triage-v1 |
|---|---|---|
| Development accuracy (179 records, calibrated) | 0.492 | **0.989** |
| Development Brier score (lower is better) | 0.794 | **0.022** |
| Development ECE (lower is better) | 0.207 | **0.011** |
| Accuracy on the Kafka v2 eval set (150) | 0.520 (strategy A) | **1.000** (G) / 0.987 with signatures (H) |
| Public decision-v7 regression check (417 records) | 0.860 acc / 0.225 Brier | 0.860 / 0.231 (no forgetting) |
| Latency p50 / p95 through the tunnel | 0.57 / 0.88 s | 0.81 / 1.51 s |

- **Accuracy gain:** +0.497, 95% CI [+0.42, +0.56] (paired bootstrap).
- **Fitted temperature:** 0.25 for the fine-tune, 2.73 for the base model.
- **Full comparison:** `evals/reports/kev-finetune-comparison-2026-10-12.md`.

**Stress test** (`stress.py`, `evals/reports/kev-finetune-stress-2026-10-12.md`). The 134 cases the model
decides were each changed in ways the generator never produces but that keep the label:

- entities and hosts renamed
- different tags
- evidence reordered
- two harmless INFO log lines added
- ISO-8601 timestamps instead of the Kafka log format

| Model | Accuracy, all changes combined | Wrong restarts |
|---|---|---|
| Fine-tune | 0.918 | 0 |
| Base | 0.358 | 0 |

The weak spot is the INFO noise. It turns 7 `cannot_tell` cases (an alert with no context) into `suppress`,
which silences an unknown problem. The fix is in the data: add training records where harmless INFO lines sit
next to an alert that lacks context.

**Caveats:**

- The labels are inferred, not reviewed by people.
- The training and evaluation data come from one generator.
- None of these numbers is evidence about real tenant data (open questions 7, 12 and 13).

**Memory on the Spark:** Qwen runs at `--gpu-memory-utilization 0.70` (about 85 GB), so it cannot run alongside
`kev-kafka` (about 12 GB). After this run, Qwen was restarted and `kev-kafka` was stopped. Its checkpoint stays at
`~/kev-ft/runs/kafka-triage-v1/checkpoint`, and `docker start kev-kafka` brings it back once Qwen is stopped.
