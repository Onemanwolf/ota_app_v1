"""Kev delta fine-tune on the DGX Spark: kev_modal.py::run_train without Modal (same recipe, local paths).

Runs INSIDE a training container that has Kev installed at /kev (see evals/kev_finetune/README.md):

    python train_local.py --data /work/data/split --out /work/runs/kafka-triage-v1 [--epochs 2]

Steps (identical to the released recipe): kev.train --init_from (LoRA delta + pointer head, lr capped at 5e-5,
replaying 2000 public decision-v7 records against forgetting) -> temperature fit on calibration.jsonl -> score
development.jsonl -> score the baseline on the same records + paired bootstrap -> regression check on public
decision-v7 development groups. Writes result.json, errors.jsonl and train.log under --out.
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

KEV_ROOT = "/kev"
SUITE = f"{KEV_ROOT}/evals/v7/decision-v7"
MAX_DELTA_LR = 5e-5


def stage(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def score_checkpoint(run, development, out, calibration=None):  # type: ignore[no-untyped-def]
    import torch
    from kev.benchmark import evaluate_records
    from kev.checkpoint import LoadOptions
    from kev.metrics import fit_temperature
    from kev.model import SERVE_MAX_BRANCH, SERVE_MAX_STATE
    from kev.predictors import LocalPredictor
    from kev.suite import CONTEXT

    # score at the serving limits (kev.serve accepts 8192-token states), not the 384-token training default:
    # production sends the same untruncated states
    context = {
        **CONTEXT,
        "max_state": SERVE_MAX_STATE,
        "max_branch": SERVE_MAX_BRANCH,
        "max_packed": SERVE_MAX_STATE + SERVE_MAX_BRANCH,
    }
    predictor = LocalPredictor(run, "cuda", LoadOptions(temperature=1.0), context=context)
    try:
        t = 1.0
        if calibration:
            _, cal_rows = evaluate_records(calibration, predictor, Path(out) / "calibration")
            t = fit_temperature(cal_rows, aggregation="micro")
        report, rows = evaluate_records(development, predictor, Path(out) / "development", t)
        return report, rows, t
    finally:
        del predictor
        gc.collect()
        torch.cuda.empty_cache()


def tempered(rows, t):  # type: ignore[no-untyped-def]
    from kev.metrics import probabilities_at_temperature

    return [{**r, "p": probabilities_at_temperature(r, t).tolist()} for r in rows]


def summary(report, rows, t):  # type: ignore[no-untyped-def]
    from kev.metrics import grouped_metrics

    clean = [r for r in rows if r["variant"] == "clean"]
    return {
        "temperature": t,
        "raw": report["clean"],
        "calibrated": report["calibrated_clean"],
        "per_question": grouped_metrics(clean, "question", t),
        "n_questions": len(clean),
    }


def errors(rows, records, t, limit=60):  # type: ignore[no-untyped-def]
    from kev.metrics import probabilities_at_temperature

    by_id = {r["_meta"]["id"]: r for r in records}
    out = []
    for r in rows:
        p = probabilities_at_temperature(r, t)
        i = int(p.argmax())
        if i == r["label"]:
            continue
        out.append(
            {
                "id": r["id"],
                "confidence": round(float(p[i]), 3),
                "predicted": r["keys"][i],
                "label": r["keys"][r["label"]],
                "state": by_id[r["id"]]["state"],
            }
        )
    return sorted(out, key=lambda e: -e["confidence"])[:limit]


def train(cmd: list[str], out: Path) -> None:
    stage("training: " + " ".join(cmd[2:]))
    with (out / "train.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(  # noqa: S603  (fixed argv: this interpreter running kev.train)
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=KEV_ROOT
        )
        for line in proc.stdout or ():
            log.write(line)
            log.flush()
            if line.startswith(("ep", "saved", "device", "delta", "replay", "dropped")) or "Error" in line:
                print(line.rstrip(), flush=True)
        if proc.wait():
            raise SystemExit(f"kev.train failed ({proc.returncode}); see {out / 'train.log'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="directory with train/calibration/development.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--init_from", default="jaredpalmer/kev-4b")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument(
        "--lr", type=float, default=0.0, help="0 = the init checkpoint's delta lr, capped at 5e-5"
    )
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--accum", type=int, default=0)
    ap.add_argument("--replay", type=int, default=2000)
    ap.add_argument("--p_none_pair", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="checkpoint already trained: only score it")
    ap.add_argument(
        "--max_state",
        type=int,
        default=512,
        help="state tokens per --data record (Kev default 384; serving accepts 8192). Kafka logs run ~2.9 "
        "chars/token, so 1400-char production states reach ~490 tokens",
    )
    ap.add_argument(
        "--regression", type=int, default=300, help="public decision-v7 development groups; 0 = skip"
    )
    ap.add_argument("--extra_dev", default="", help="another labelled JSONL scored at the fitted temperature")
    a = ap.parse_args()

    import torch
    from kev.checkpoint import Checkpoint, read_meta, write_meta
    from kev.data import load_records
    from kev.metrics import metrics, paired_bootstrap
    from kev.suite import load_split, write_json, write_jsonl

    out, data = Path(a.out), Path(a.data)
    if (out / "checkpoint").exists() and not a.resume:
        raise SystemExit(f"{out} already holds a run; choose a new --out")
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    init = Checkpoint(a.init_from)
    meta, args = init.meta, init.meta.extra["args"]
    cfg = {
        "lr": a.lr or min(args["lr"], MAX_DELTA_LR),
        "batch": a.batch or args["batch"],
        "accum": a.accum or args["accum"],
        "checkpointing": args["checkpointing"],
        "epochs": a.epochs,
        "seed": a.seed,
        "replay": a.replay,
        "p_none_pair": a.p_none_pair,
        "max_state": a.max_state,
    }
    write_json(
        out / "config.json",
        {
            "init_from": a.init_from,
            "init_resolved": init.path,
            "base": meta.base,
            "config": cfg,
            "gpu": torch.cuda.get_device_name(0),
        },
    )
    stage(f"{meta.base} from {a.init_from}; config {json.dumps(cfg)}")

    run = str(out / "checkpoint")
    cmd = [
        sys.executable,
        "-m",
        "kev.train",
        "--data",
        str(data / "train.jsonl"),
        "--init_from",
        a.init_from,
        "--out",
        run,
        "--device",
        "cuda",
        "--dtype",
        "bf16",
        "--base",
        meta.base,
        "--lora",
        meta.lora,
        "--head_dim",
        meta.head_dim,
        "--lora_targets",
        args["lora_targets"],
        "--option_isolation",
        int(meta.option_isolation),
        "--special_embeddings",
        int(meta.special_embeddings),
        "--weights_dtype",
        meta.weights_dtype,
        "--epochs",
        cfg["epochs"],
        "--lr",
        cfg["lr"],
        "--batch",
        cfg["batch"],
        "--accum",
        cfg["accum"],
        "--checkpointing",
        cfg["checkpointing"],
        "--seed",
        cfg["seed"],
        "--p_none_pair",
        cfg["p_none_pair"],
        "--max_state",
        a.max_state,
    ]
    if meta.base_revision:
        cmd += ["--base_revision", meta.base_revision]
    if cfg["replay"]:
        cmd += ["--suite", SUITE, "--replay", cfg["replay"]]
    cmd = [str(c) for c in cmd]
    if not (a.resume and (out / "checkpoint/head.pt").exists()):
        train(cmd, out)
    else:
        stage("checkpoint exists, skipping training (--resume); clearing earlier scores")
        for d in ("calibration", "development", "baseline", "extra", "regression"):
            shutil.rmtree(out / d, ignore_errors=True)

    calibration = load_records(data / "calibration.jsonl")
    development = load_records(data / "development.jsonl")
    stage(f"scoring on {len(calibration)} calibration + {len(development)} development records")
    report, rows, t = score_checkpoint(run, development, out, calibration)
    m = read_meta(run)
    m.temperature = t
    m.extra["temperature_fit"] = {"rows": "calibration.jsonl", "n": len(calibration), "value": t}
    write_meta(run, m)  # the checkpoint now serves calibrated probabilities by default
    write_jsonl(out / "errors.jsonl", errors(rows, development, t))
    result = {
        "init_from": a.init_from,
        "config": cfg,
        "temperature": t,
        "development": summary(report, rows, t),
    }
    write_json(out / "result.json", result)

    stage(f"scoring the baseline {a.init_from}")
    b_report, b_rows, b_t = score_checkpoint(a.init_from, development, out / "baseline", calibration)
    result["baseline"] = {"development": summary(b_report, b_rows, b_t)}
    result["bootstrap"] = {
        k: paired_bootstrap(tempered(rows, t), tempered(b_rows, b_t), metric=k, aggregation="micro")
        for k in ("acc", "brier", "ece")
    }
    write_json(out / "result.json", result)

    if a.extra_dev:
        extra = load_records(Path(a.extra_dev))
        stage(f"scoring {len(extra)} extra development records ({a.extra_dev})")
        e_report, e_rows, _ = score_checkpoint(run, extra, out / "extra")
        result["extra_dev"] = {"file": a.extra_dev, "finetuned": summary(e_report, e_rows, 1.0)}
        write_jsonl(out / "extra_errors.jsonl", errors(e_rows, extra, 1.0))
        write_json(out / "result.json", result)

    if a.regression:
        groups: dict[str, list] = {}
        for r in load_split(SUITE, "development"):
            groups.setdefault(r["_meta"]["group_id"], []).append(r)
        chosen = random.Random(a.seed).sample(  # noqa: S311  (reproducible sample, not crypto)
            sorted(groups), min(a.regression, len(groups))
        )
        sample = [r for g in chosen for r in groups[g]]
        stage(f"regression check: {len(sample)} public decision-v7 development records")
        _, r_rows, _ = score_checkpoint(run, sample, out / "regression/finetuned")
        _, rb_rows, _ = score_checkpoint(a.init_from, sample, out / "regression/baseline")
        result["regression"] = {
            "n": len(sample),
            "finetuned": metrics([r for r in r_rows if r["variant"] == "clean"]),
            "baseline": metrics([r for r in rb_rows if r["variant"] == "clean"]),
        }
    result["wall_seconds"] = round(time.time() - started)
    write_json(out / "result.json", result)
    dev, base = result["development"]["calibrated"], result["baseline"]["development"]["calibrated"]
    stage(
        f"done in {result['wall_seconds']}s: development acc {base['acc']:.3f} -> {dev['acc']:.3f}, "
        f"brier {base['brier']:.3f} -> {dev['brier']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
