"""Run small real backend comparisons for routing decisions.

Start narrow: compare JSONL vs SQLite memory backends on representative memory
workloads. Intentionally lean — enough signal to pick defaults without birthing
Benchmark Framework Enterprise Edition.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from memory_backends import JSONLBackend, SQLiteBackend

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "output" / "benchmarks"


@dataclass
class Sample:
    backend: str
    operation: str
    iteration: int
    elapsed_ms: float
    records: int


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _lesson_record(index: int) -> Dict[str, Any]:
    return {
        "lesson": f"Prefer direct evidence over vibes #{index}",
        "category": "execution",
        "task": "benchmark-memory-backend",
        "confidence": round(0.55 + ((index % 7) * 0.05), 2),
        "tags": ["benchmark", "memory", f"slot-{index % 5}"],
        "source": f"run-{index}",
    }


def _lookup_record(index: int) -> Dict[str, Any]:
    return {
        "lesson": f"Retrieval lesson #{index}",
        "category": "target" if index % 25 == 0 else "background",
        "task": "retrieval-hot" if index % 10 == 0 else "retrieval-cold",
        "confidence": round(0.45 + ((index % 11) * 0.05), 2),
        "tags": [f"bucket-{index % 20}", "benchmark", "lookup"],
        "source": f"seed-{index}",
    }


def _median(values: List[float]) -> float:
    return round(statistics.median(values), 3) if values else 0.0


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.9999) - 1))
    return round(ordered[rank], 3)


def _measure(fn: Callable[[], None]) -> float:
    start = time.perf_counter_ns()
    fn()
    end = time.perf_counter_ns()
    return round((end - start) / 1_000_000, 3)


def _build_backend(backend_name: str, memory_dir: Path):
    if backend_name == "jsonl":
        return JSONLBackend(memory_dir)
    if backend_name == "sqlite":
        return SQLiteBackend(memory_dir / "memory.db")
    raise ValueError(f"unsupported backend: {backend_name}")


def _summarize_samples(
    samples: List[Sample],
    *,
    backends: List[str],
    operations: List[str],
    recommendation: str,
    slice_name: str,
    records: int,
    iterations: int,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, List[float]]] = {backend: {op: [] for op in operations} for backend in backends}
    for sample in samples:
        grouped[sample.backend][sample.operation].append(sample.elapsed_ms)

    summary_rows: List[Dict[str, Any]] = []
    findings: List[str] = []
    for operation in operations:
        jsonl_median = _median(grouped["jsonl"][operation])
        sqlite_median = _median(grouped["sqlite"][operation])
        faster = "jsonl" if jsonl_median < sqlite_median else "sqlite"
        slower = "sqlite" if faster == "jsonl" else "jsonl"
        faster_value = min(jsonl_median, sqlite_median)
        slower_value = max(jsonl_median, sqlite_median)
        ratio = round((slower_value / faster_value), 2) if faster_value > 0 else None
        summary_rows.append(
            {
                "operation": operation,
                "jsonl": {"median_ms": jsonl_median, "p95_ms": _p95(grouped["jsonl"][operation])},
                "sqlite": {"median_ms": sqlite_median, "p95_ms": _p95(grouped["sqlite"][operation])},
                "faster_backend": faster,
                "speedup_vs_slower": ratio,
            }
        )
        if ratio is not None:
            findings.append(f"{operation}: {faster} median {faster_value} ms vs {slower} {slower_value} ms (~{ratio}x faster)")
        else:
            findings.append(f"{operation}: both backends too close to distinguish")

    payload = {
        "slice": slice_name,
        "records": records,
        "iterations": iterations,
        "summary": summary_rows,
        "samples": [asdict(sample) for sample in samples],
        "findings": findings,
        "recommendation": recommendation,
    }
    if extra:
        payload.update(extra)
    return payload


def run_memory_backend_slice(output_dir: Path, *, iterations: int = 5, records: int = 250) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records_payload = [_lesson_record(i) for i in range(records)]
    backends = ["jsonl", "sqlite"]
    operations = ["append_lessons", "read_all_lessons"]
    samples: List[Sample] = []

    with TemporaryDirectory(prefix="poe-memory-bench-") as tmp:
        temp_root = Path(tmp)
        for backend_name in backends:
            for iteration in range(1, iterations + 1):
                append_dir = temp_root / f"{backend_name}-append-{iteration}"
                backend = _build_backend(backend_name, append_dir)
                append_elapsed = _measure(lambda: [backend.append("lessons", row) for row in records_payload])
                samples.append(Sample(backend=backend_name, operation="append_lessons", iteration=iteration, elapsed_ms=append_elapsed, records=records))

                read_seed_dir = temp_root / f"{backend_name}-read-{iteration}"
                read_backend = _build_backend(backend_name, read_seed_dir)
                for row in records_payload:
                    read_backend.append("lessons", row)
                read_elapsed = _measure(lambda: read_backend.read_all("lessons"))
                samples.append(Sample(backend=backend_name, operation="read_all_lessons", iteration=iteration, elapsed_ms=read_elapsed, records=records))

    recommendation = (
        "Keep JSONL as the low-friction default for small append-heavy local runs; "
        "prefer SQLite when this slice starts to include larger read-heavy scans or multi-process contention."
    )
    return _summarize_samples(
        samples,
        backends=backends,
        operations=operations,
        recommendation=recommendation,
        slice_name="memory-backend-lessons",
        records=records,
        iterations=iterations,
    )


def _jsonl_filtered_lookup(memory_dir: Path, *, category: str, task: str, min_confidence: float, limit: int) -> List[Dict[str, Any]]:
    backend = JSONLBackend(memory_dir)
    matched: List[Dict[str, Any]] = []
    for row in reversed(backend.read_all("lessons")):
        if row.get("category") != category:
            continue
        if row.get("task") != task:
            continue
        if float(row.get("confidence", 0.0)) < min_confidence:
            continue
        matched.append(row)
        if len(matched) >= limit:
            break
    return matched


def _sqlite_filtered_lookup(memory_dir: Path, *, category: str, task: str, min_confidence: float, limit: int) -> List[Dict[str, Any]]:
    db_path = memory_dir / "memory.db"
    con = sqlite3.connect(str(db_path), timeout=10)
    try:
        cur = con.execute(
            """
            SELECT data
            FROM memory_records
            WHERE collection = ?
              AND json_extract(data, '$.category') = ?
              AND json_extract(data, '$.task') = ?
              AND CAST(json_extract(data, '$.confidence') AS REAL) >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            ("lessons", category, task, min_confidence, limit),
        )
        rows = cur.fetchall()
        return [json.loads(data) for (data,) in rows]
    finally:
        con.close()


def run_memory_backend_filtered_lookup_slice(
    output_dir: Path,
    *,
    iterations: int = 5,
    records: int = 20_000,
    limit: int = 25,
    min_confidence: float = 0.8,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records_payload = [_lookup_record(i) for i in range(records)]
    backends = ["jsonl", "sqlite"]
    operations = ["filtered_lookup_lessons"]
    samples: List[Sample] = []
    expected_matches = len(
        [
            row for row in records_payload
            if row["category"] == "target" and row["task"] == "retrieval-hot" and float(row["confidence"]) >= min_confidence
        ]
    )
    expected_returned = min(limit, expected_matches)

    with TemporaryDirectory(prefix="poe-memory-bench-filtered-") as tmp:
        temp_root = Path(tmp)
        for backend_name in backends:
            for iteration in range(1, iterations + 1):
                seed_dir = temp_root / f"{backend_name}-lookup-{iteration}"
                backend = _build_backend(backend_name, seed_dir)
                for row in records_payload:
                    backend.append("lessons", row)

                if backend_name == "jsonl":
                    fn = lambda: _jsonl_filtered_lookup(
                        seed_dir,
                        category="target",
                        task="retrieval-hot",
                        min_confidence=min_confidence,
                        limit=limit,
                    )
                else:
                    fn = lambda: _sqlite_filtered_lookup(
                        seed_dir,
                        category="target",
                        task="retrieval-hot",
                        min_confidence=min_confidence,
                        limit=limit,
                    )

                result: List[Dict[str, Any]] = []

                def _run() -> None:
                    nonlocal result
                    result = fn()

                elapsed = _measure(_run)
                if len(result) != expected_returned:
                    raise RuntimeError(
                        f"filtered lookup returned {len(result)} rows for {backend_name}, expected {expected_returned}"
                    )
                samples.append(
                    Sample(
                        backend=backend_name,
                        operation="filtered_lookup_lessons",
                        iteration=iteration,
                        elapsed_ms=elapsed,
                        records=records,
                    )
                )

    recommendation = (
        "JSONL is still simpler, but SQLite earns its keep once the workload wants selective retrieval "
        "instead of parsing an entire lessons file in Python every time."
    )
    return _summarize_samples(
        samples,
        backends=backends,
        operations=operations,
        recommendation=recommendation,
        slice_name="memory-backend-filtered-lookup",
        records=records,
        iterations=iterations,
        extra={
            "query": {
                "category": "target",
                "task": "retrieval-hot",
                "min_confidence": min_confidence,
                "limit": limit,
                "expected_matches": expected_matches,
                "expected_returned": expected_returned,
            }
        },
    )


def _contention_record(worker: int, seq: int) -> Dict[str, Any]:
    return {
        "id": f"w{worker}-n{seq}",
        "lesson": f"Concurrent append worker={worker} seq={seq}",
        "category": "contention",
        "task": "append-contention",
        "confidence": 0.9,
        "worker": worker,
        "seq": seq,
    }


def _jsonl_append_worker(target: str, worker: int, writes: int, queue: multiprocessing.Queue) -> None:
    success = 0
    failures = 0
    first_error = None
    for seq in range(writes):
        try:
            with open(target, "a", encoding="utf-8") as f:
                f.write(json.dumps(_contention_record(worker, seq)) + "\n")
            success += 1
        except OSError as exc:
            failures += 1
            if first_error is None:
                first_error = str(exc)
    queue.put({"success": success, "failures": failures, "first_error": first_error})


def _sqlite_append_worker(target: str, worker: int, writes: int, queue: multiprocessing.Queue) -> None:
    success = 0
    failures = 0
    first_error = None
    con = sqlite3.connect(target, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    try:
        for seq in range(writes):
            try:
                con.execute(
                    "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
                    ("lessons", json.dumps(_contention_record(worker, seq))),
                )
                con.commit()
                success += 1
            except sqlite3.Error as exc:
                failures += 1
                if first_error is None:
                    first_error = str(exc)
    finally:
        con.close()
    queue.put({"success": success, "failures": failures, "first_error": first_error})


def _read_jsonl_contention(path: Path) -> Tuple[int, int]:
    if not path.exists():
        return 0, 0
    valid = 0
    invalid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
            valid += 1
        except json.JSONDecodeError:
            invalid += 1
    return valid, invalid


def _run_contention_case(base_dir: Path, backend_name: str, workers: int, writes_per_worker: int) -> Dict[str, Any]:
    case_dir = base_dir / f"{backend_name}-workers-{workers}"
    case_dir.mkdir(parents=True, exist_ok=True)
    queue: multiprocessing.Queue = multiprocessing.Queue()
    processes: List[multiprocessing.Process] = []

    if backend_name == "jsonl":
        target = case_dir / "lessons.jsonl"
        worker_fn = _jsonl_append_worker
    else:
        target = case_dir / "memory.db"
        SQLiteBackend(target)
        worker_fn = _sqlite_append_worker

    start = time.perf_counter_ns()
    for worker in range(workers):
        proc = multiprocessing.Process(target=worker_fn, args=(str(target), worker, writes_per_worker, queue))
        proc.start()
        processes.append(proc)

    worker_results = []
    for _ in processes:
        worker_results.append(queue.get())
    for proc in processes:
        proc.join()
    elapsed_ms = round((time.perf_counter_ns() - start) / 1_000_000, 3)

    reported_success = sum(int(item["success"]) for item in worker_results)
    reported_failures = sum(int(item["failures"]) for item in worker_results)
    first_error = next((item["first_error"] for item in worker_results if item.get("first_error")), None)

    if backend_name == "jsonl":
        valid_rows, invalid_rows = _read_jsonl_contention(target)
        observed_success = valid_rows
        corruption_rows = invalid_rows
    else:
        backend = SQLiteBackend(target)
        observed_success = len(backend.read_all("lessons"))
        corruption_rows = 0

    return {
        "workers": workers,
        "writes_per_worker": writes_per_worker,
        "attempted_writes": workers * writes_per_worker,
        "reported_successful_writes": reported_success,
        "observed_successful_writes": observed_success,
        "failed_writes": reported_failures,
        "lost_writes": max(0, reported_success - observed_success),
        "corruption_rows": corruption_rows,
        "elapsed_ms": elapsed_ms,
        "first_error": first_error,
    }


def run_memory_backend_append_contention_slice(
    output_dir: Path,
    *,
    workers: Sequence[int] = (2, 4),
    writes_per_worker: int = 200,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    backends = ["jsonl", "sqlite"]
    operations = ["concurrent_append_contention"]
    samples: List[Sample] = []
    contention_results: Dict[str, List[Dict[str, Any]]] = {backend: [] for backend in backends}

    with TemporaryDirectory(prefix="poe-memory-bench-contention-") as tmp:
        temp_root = Path(tmp)
        for backend_name in backends:
            for worker_count in workers:
                result = _run_contention_case(temp_root, backend_name, worker_count, writes_per_worker)
                contention_results[backend_name].append(result)
                samples.append(
                    Sample(
                        backend=backend_name,
                        operation="concurrent_append_contention",
                        iteration=worker_count,
                        elapsed_ms=result["elapsed_ms"],
                        records=result["attempted_writes"],
                    )
                )

    findings: List[str] = []
    for worker_count in workers:
        jsonl_case = next(item for item in contention_results["jsonl"] if item["workers"] == worker_count)
        sqlite_case = next(item for item in contention_results["sqlite"] if item["workers"] == worker_count)
        faster = "jsonl" if jsonl_case["elapsed_ms"] < sqlite_case["elapsed_ms"] else "sqlite"
        findings.append(
            f"{worker_count} workers: {faster} finished faster ({jsonl_case['elapsed_ms']} ms jsonl vs {sqlite_case['elapsed_ms']} ms sqlite)"
        )
        findings.append(
            f"{worker_count} workers integrity: jsonl observed {jsonl_case['observed_successful_writes']}/{jsonl_case['attempted_writes']} writes"
            f" with {jsonl_case['corruption_rows']} corrupt rows; sqlite observed {sqlite_case['observed_successful_writes']}/{sqlite_case['attempted_writes']} writes"
            f" with {sqlite_case['failed_writes']} lock/failure events"
        )

    recommendation = (
        "JSONL can stay the default for single-writer simplicity, but SQLite is the safer choice once multiple workers append "
        "to the same collection and you care about integrity under contention."
    )
    payload = _summarize_samples(
        samples,
        backends=backends,
        operations=operations,
        recommendation=recommendation,
        slice_name="memory-backend-append-contention",
        records=max(workers) * writes_per_worker,
        iterations=len(tuple(workers)),
        extra={
            "workers": list(workers),
            "writes_per_worker": writes_per_worker,
            "contention": contention_results,
        },
    )
    payload["findings"] = findings
    return payload


def render_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# Backend Benchmark Report",
        "",
        f"- Slice: `{payload['slice']}`",
        f"- Records per run: `{payload['records']}`",
        f"- Iterations per backend/operation: `{payload['iterations']}`",
    ]
    if "query" in payload:
        query = payload["query"]
        lines.extend(
            [
                f"- Query: `category={query['category']}` · `task={query['task']}` · `confidence>={query['min_confidence']}` · `limit={query['limit']}`",
                f"- Matching records in seeded dataset: `{query['expected_matches']}` (returns `{query['expected_returned']}`)",
            ]
        )
    if "contention" in payload:
        lines.extend([
            f"- Worker counts: `{', '.join(str(w) for w in payload['workers'])}`",
            f"- Writes per worker: `{payload['writes_per_worker']}`",
        ])
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "| operation | jsonl median ms | sqlite median ms | faster | speedup |",
            "| --- | ---: | ---: | --- | ---: |",
        ]
    )
    for row in payload["summary"]:
        speedup = row["speedup_vs_slower"] if row["speedup_vs_slower"] is not None else "n/a"
        lines.append(
            f"| `{row['operation']}` | {row['jsonl']['median_ms']} | {row['sqlite']['median_ms']} | `{row['faster_backend']}` | {speedup}x |"
        )
    if "contention" in payload:
        lines.extend([
            "",
            "## Contention details",
            "",
            "| backend | workers | attempted | observed ok | failed writes | lost writes | corrupt rows | elapsed ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ])
        for backend_name in ("jsonl", "sqlite"):
            for case in payload["contention"][backend_name]:
                lines.append(
                    f"| `{backend_name}` | {case['workers']} | {case['attempted_writes']} | {case['observed_successful_writes']} | {case['failed_writes']} | {case['lost_writes']} | {case['corruption_rows']} | {case['elapsed_ms']} |"
                )
    lines.extend([
        "",
        "## Findings",
        "",
    ])
    for item in payload["findings"]:
        lines.append(f"- {item}")
    lines.extend([
        "",
        "## Recommendation",
        "",
        f"- {payload['recommendation']}",
        "",
    ])
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a small real backend comparison benchmark")
    parser.add_argument("--slice", choices=["memory-backend", "memory-backend-filtered-lookup", "memory-backend-append-contention"], default="memory-backend")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for benchmark artifacts")
    parser.add_argument("--write-report", help="Write markdown report to this path")
    parser.add_argument("--write-json", help="Write JSON payload to this path")
    parser.add_argument("--iterations", type=int, default=7, help="Runs per backend and operation")
    parser.add_argument("--records", type=int, default=250, help="Representative records per run")
    parser.add_argument("--limit", type=int, default=25, help="Lookup limit for filtered slice")
    parser.add_argument("--min-confidence", type=float, default=0.8, help="Confidence floor for filtered slice")
    parser.add_argument("--workers", type=int, nargs="+", default=[2, 4], help="Worker counts for append contention slice")
    parser.add_argument("--writes-per-worker", type=int, default=200, help="Writes each worker attempts in append contention slice")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    if args.slice == "memory-backend-filtered-lookup":
        payload = run_memory_backend_filtered_lookup_slice(
            output_dir,
            iterations=max(1, args.iterations),
            records=max(1, args.records),
            limit=max(1, args.limit),
            min_confidence=max(0.0, args.min_confidence),
        )
        stem = "backend-benchmark-memory-filtered-lookup"
    elif args.slice == "memory-backend-append-contention":
        payload = run_memory_backend_append_contention_slice(
            output_dir,
            workers=tuple(sorted({max(1, worker) for worker in args.workers})),
            writes_per_worker=max(1, args.writes_per_worker),
        )
        stem = "backend-benchmark-memory-append-contention"
    else:
        payload = run_memory_backend_slice(output_dir, iterations=max(1, args.iterations), records=max(1, args.records))
        stem = "backend-benchmark-memory"

    report = render_markdown(payload)
    print(report, end="\n")

    report_path = Path(args.write_report) if args.write_report else output_dir / f"{stem}.md"
    json_path = Path(args.write_json) if args.write_json else output_dir / f"{stem}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report + ("" if report.endswith("\n") else "\n"), encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
