"""Summarize step-cost telemetry and run lean fixture benchmarks.

This is a small operator tool: turn step-costs.jsonl into something legible,
then optionally generate deterministic fixture data for regression checks.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import metrics

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_METRICS_PATH = ROOT / "memory" / "step-costs.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "benchmarks"
DEFAULT_FIXTURES_PATH = ROOT / "benchmarks" / "fixture-workloads.json"


@dataclass
class SummaryRow:
    key: str
    samples: int
    ok: int
    errors: int
    median_ms: int
    p95_ms: int
    median_tokens: int
    p95_tokens: int
    total_cost_usd: float


GROUP_FIELDS = {
    "task": "task_class",
    "step_type": "step_type",
    "model": "model",
    "status": "status",
}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def percentile_nearest_rank(values: Sequence[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil((pct / 100.0) * len(ordered)))
    return int(ordered[min(rank - 1, len(ordered) - 1)])


def infer_task_class(event: Dict[str, Any]) -> str:
    preview = str(event.get("step_text_preview") or "").lower()
    goal = str(event.get("goal_preview") or "").lower()
    step_type = str(event.get("step_type") or "general")
    combo = f"{preview} {goal}"

    if "x.com/" in combo or "twitter.com/" in combo or "tweet" in combo or "thread" in combo:
        return "x-link-research"
    if any(word in combo for word in ["summarize", "summarise", "summary", "distill", "brief"]):
        return "document-summary"
    if any(word in combo for word in ["analyze", "analyse", "pattern", "compare", "structured", "json"]):
        return "structured-analysis"
    if step_type == "research":
        return "research"
    return step_type or "general"


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(event)
    row["task_class"] = infer_task_class(event)
    row["step_type"] = str(event.get("step_type") or "general")
    row["status"] = str(event.get("status") or "unknown")
    row["model"] = str(event.get("model") or "unknown")
    row["elapsed_ms"] = int(event.get("elapsed_ms") or 0)
    row["total_tokens"] = int(event.get("total_tokens") or 0)
    row["cost_usd"] = float(event.get("cost_usd") or 0.0)
    return row


def summarize_by(events: Iterable[Dict[str, Any]], field: str) -> List[SummaryRow]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for raw in events:
        row = normalize_event(raw)
        key = str(row.get(field) or "unknown")
        groups.setdefault(key, []).append(row)

    out: List[SummaryRow] = []
    for key, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        elapsed = [int(item["elapsed_ms"]) for item in items]
        tokens = [int(item["total_tokens"]) for item in items]
        ok = sum(1 for item in items if item.get("status") in {"done", "ok", "pass"})
        out.append(
            SummaryRow(
                key=key,
                samples=len(items),
                ok=ok,
                errors=len(items) - ok,
                median_ms=int(statistics.median(elapsed)) if elapsed else 0,
                p95_ms=percentile_nearest_rank(elapsed, 95),
                median_tokens=int(statistics.median(tokens)) if tokens else 0,
                p95_tokens=percentile_nearest_rank(tokens, 95),
                total_cost_usd=round(sum(float(item["cost_usd"]) for item in items), 6),
            )
        )
    return out


def render_table(title: str, rows: Sequence[SummaryRow]) -> List[str]:
    lines = [
        f"## {title}",
        "",
        "| key | samples | ok | error | median ms | p95 ms | median tokens | p95 tokens | total cost usd |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    if not rows:
        lines.append("| (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 |")
    else:
        for row in rows:
            lines.append(
                f"| `{row.key}` | {row.samples} | {row.ok} | {row.errors} | {row.median_ms} | {row.p95_ms} | {row.median_tokens} | {row.p95_tokens} | {row.total_cost_usd:.6f} |"
            )
    lines.append("")
    return lines


def build_report(events: List[Dict[str, Any]], source_path: Path, fixture_run: Optional[Dict[str, Any]] = None) -> str:
    normalized = [normalize_event(event) for event in events]
    lines = [
        "# Tool Cost Report",
        "",
        f"- Source metrics: `{_display_path(source_path)}`",
        f"- Samples: `{len(normalized)}`",
    ]
    if fixture_run:
        lines.append(f"- Fixture run: `{fixture_run['name']}` ({fixture_run['samples']} samples)")
    lines.extend(
        [
            "",
            "This is for choosing better defaults, not for building a dashboard cult.",
            "",
        ]
    )
    for name, field in GROUP_FIELDS.items():
        lines.extend(render_table(f"By {name}", summarize_by(normalized, field)))
    return "\n".join(lines).rstrip() + "\n"


def _record_fixture_entries(metrics_path: Path, fixtures: List[Dict[str, Any]], repeats: int) -> int:
    original = metrics._step_costs_path
    try:
        metrics._step_costs_path = lambda: metrics_path  # type: ignore[assignment]
        samples = 0
        for fixture in fixtures:
            fixture_repeats = int(fixture.get("repeats", repeats))
            for _ in range(max(1, fixture_repeats)):
                metrics.record_step_cost(
                    fixture["step_text"],
                    int(fixture["tokens_in"]),
                    int(fixture["tokens_out"]),
                    fixture.get("status", "done"),
                    goal=fixture.get("goal", fixture["task_class"]),
                    model=fixture.get("model", "mid"),
                    elapsed_ms=int(fixture.get("elapsed_ms", 0)),
                )
                samples += 1
        return samples
    finally:
        metrics._step_costs_path = original  # type: ignore[assignment]


def run_fixture_benchmark(fixtures_path: Path, output_dir: Path, repeats: int) -> tuple[Path, Dict[str, Any]]:
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("fixture file must contain a non-empty JSON array")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = output_dir / f"fixture-run-{Path(__file__).stem}-{len(fixtures)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = run_dir / "step-costs.fixture.jsonl"
    samples = _record_fixture_entries(metrics_path, fixtures, repeats)
    manifest = {
        "name": run_dir.name,
        "fixtures_path": _display_path(fixtures_path),
        "metrics_path": _display_path(metrics_path),
        "samples": samples,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return metrics_path, manifest


def _payload(events: List[Dict[str, Any]], metrics_path: Path, fixture_run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    normalized = [normalize_event(event) for event in events]
    return {
        "source_metrics": str(metrics_path),
        "fixture_run": fixture_run,
        "samples": len(normalized),
        "groups": {
            name: [row.__dict__ for row in summarize_by(normalized, field)]
            for name, field in GROUP_FIELDS.items()
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize step-cost telemetry and optional fixture benchmarks")
    parser.add_argument("--metrics", default=str(DEFAULT_METRICS_PATH), help="JSONL telemetry input")
    parser.add_argument("--write-report", help="Write markdown report to this path")
    parser.add_argument("--write-json", help="Write grouped JSON summary to this path")
    parser.add_argument("--run-fixtures", action="store_true", help="Generate deterministic fixture metrics first")
    parser.add_argument("--fixtures", default=str(DEFAULT_FIXTURES_PATH), help="Fixture workload JSON file")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for fixture runs and reports")
    parser.add_argument("--fixture-repeats", type=int, default=2, help="Default repeat count per fixture workload")
    args = parser.parse_args(argv)

    metrics_path = Path(args.metrics)
    fixture_run = None
    if args.run_fixtures:
        metrics_path, fixture_run = run_fixture_benchmark(Path(args.fixtures), Path(args.output_dir), args.fixture_repeats)

    events = load_jsonl(metrics_path)
    report = build_report(events, metrics_path, fixture_run)
    print(report, end="")

    if args.write_report:
        report_path = Path(args.write_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")

    if args.write_json:
        json_path = Path(args.write_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(_payload(events, metrics_path, fixture_run), indent=2) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
