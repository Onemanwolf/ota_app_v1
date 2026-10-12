"""`opstriage-contracts export-schemas` writes JSON Schema files for every top-level contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from opstriage_contracts import problems, tools, triage

EXPORTED: tuple[type[BaseModel], ...] = (
    problems.ProblemEvent,
    problems.Evidence,
    triage.TriageDecision,
    tools.NotificationRequest,
    tools.NotificationResult,
    tools.TicketRequest,
    tools.TicketResult,
    tools.RestartRequest,
    tools.RemediationResult,
    tools.TargetSummary,
    tools.ResolveTargetRequest,
    tools.ResolveTargetResult,
    tools.ContainerStatusResult,
)

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "schemas"


def render() -> dict[str, str]:
    """File name -> JSON text, deterministic so CI can diff it."""
    return {
        f"{model.__name__}.schema.json": json.dumps(model.model_json_schema(), indent=2, sort_keys=True)
        + "\n"
        for model in EXPORTED
    }


def export(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in render().items():
        path = out_dir / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check(out_dir: Path) -> list[str]:
    """Names of schema files that are missing or stale."""
    return [
        name
        for name, text in render().items()
        if not (out_dir / name).exists() or (out_dir / name).read_text(encoding="utf-8") != text
    ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="opstriage-contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export-schemas", "check-schemas"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.command == "export-schemas":
        for path in export(args.out):
            print(path)
        return
    stale = check(args.out)
    if stale:
        raise SystemExit(f"stale JSON schemas (run export-schemas): {', '.join(stale)}")
