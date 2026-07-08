"""Retrieval-quality evaluation for MemoryStore adapters.

Builds an evaluation corpus from real workspace memory (read-only), loads
items into both JsonlMemoryStore and SqliteMemoryStore, and measures
retrieval quality: hit@1, hit@5, MRR, median recall latency.

Output:
  - Prints comparison table to stdout
  - Writes dated markdown report to output/memory_quality/
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")


@dataclass
class EvalQuery:
    """A retrieval evaluation query."""
    text: str
    expected_item_id: Optional[str] = None  # For leave-one-out: the correct answer
    kind: str = "self"  # "self" (from item salient words) or "probe" (hand-written)


@dataclass
class EvalResult:
    """Metrics for one query against one adapter."""
    adapter_name: str
    query_text: str
    hit_at_1: bool
    hit_at_5: bool
    mrr: float  # Reciprocal rank, 0 if not in top-k
    latency_ms: float
    rank: int  # Position in results, or 0 if not found


def _extract_salient_words(text: str, n: int = 5) -> List[str]:
    """Extract the most frequent significant words from text."""
    if not text:
        return []

    words = re.findall(r'\b[a-z]{2,}\b', text.lower())

    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'of', 'to', 'in', 'for', 'on',
        'is', 'are', 'was', 'were', 'be', 'been', 'being', 'with', 'at',
        'by', 'from', 'as', 'not', 'do', 'does', 'did', 'done', 'have',
        'has', 'had', 'that', 'this', 'it', 'its', 'they', 'them', 'their',
    }

    words = [w for w in words if w not in stopwords]

    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1

    sorted_words = sorted(freq.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:n]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_corpus_from_workspace(limit: Optional[int] = None) -> Tuple[List[Dict[str, Any]], int]:
    """Read lesson and outcome JSONL files from workspace memory.

    Returns (items, total_read) where items are converted to MemoryItem dicts.
    """
    workspace_mem = Path.home() / '.maro' / 'workspace' / 'memory'
    items = []
    total = 0

    if not workspace_mem.exists():
        log.warning("Workspace memory directory not found: %s", workspace_mem)
        return [], 0

    # Read lessons
    lessons_file = workspace_mem / 'lessons.jsonl'
    if lessons_file.exists():
        try:
            for line in lessons_file.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    total += 1
                    item = {
                        'kind': 'lesson',
                        'content': data.get('lesson', ''),
                        'scope': '',
                        'trust': min(1.0, data.get('confidence', 1.0)),
                        'provenance': {
                            'source_goal': data.get('source_goal', ''),
                            'task_type': data.get('task_type', ''),
                            'outcome': data.get('outcome', ''),
                        },
                        'meta': {
                            'times_applied': data.get('times_applied', 0),
                            'times_reinforced': data.get('times_reinforced', 0),
                        }
                    }
                    items.append(item)
                except (json.JSONDecodeError, KeyError) as e:
                    log.debug("Skipping malformed lesson line: %s", e)
                if limit and len(items) >= limit:
                    break
        except OSError as e:
            log.warning("Cannot read lessons.jsonl: %s", e)

    # Read outcomes if we haven't hit limit
    if not limit or len(items) < limit:
        outcomes_file = workspace_mem / 'outcomes.jsonl'
        if outcomes_file.exists():
            try:
                for line in outcomes_file.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        total += 1
                        content = f"{data.get('goal', '')} — {data.get('summary', '')}"
                        item = {
                            'kind': 'outcome',
                            'content': content,
                            'scope': '',
                            'trust': 1.0,
                            'provenance': {
                                'status': data.get('status', ''),
                                'project': data.get('project', ''),
                                'task_type': data.get('task_type', ''),
                            },
                            'meta': {
                                'tokens_in': data.get('tokens_in', 0),
                                'tokens_out': data.get('tokens_out', 0),
                                'elapsed_ms': data.get('elapsed_ms', 0),
                            }
                        }
                        items.append(item)
                    except (json.JSONDecodeError, KeyError) as e:
                        log.debug("Skipping malformed outcome line: %s", e)
                    if limit and len(items) >= limit:
                        break
            except OSError as e:
                log.warning("Cannot read outcomes.jsonl: %s", e)

    return items, total


def _generate_self_retrieval_queries(items: List[Dict[str, Any]]) -> List[EvalQuery]:
    """Generate leave-one-out queries: extract salient words from each item.

    The query is the item's own salient words; the item should rank highly.
    """
    queries = []
    for i, item in enumerate(items):
        content = item.get('content', '')
        if not content:
            continue

        words = _extract_salient_words(content, n=4)
        if not words:
            continue

        query_text = ' '.join(words)
        # We'll match by content, not id, in the eval loop
        queries.append(EvalQuery(text=query_text, expected_item_id=None, kind='self'))
        # Store the item index for matching
        queries[-1]._expected_index = i  # type: ignore

    return queries


def _generate_hand_written_queries() -> List[EvalQuery]:
    """Hand-written probe queries with known good answers (expect to find something)."""
    return [
        EvalQuery(text="lesson dry-run success", expected_item_id=None, kind='probe'),
        EvalQuery(text="agenda task completed", expected_item_id=None, kind='probe'),
        EvalQuery(text="research polymarket strategies", expected_item_id=None, kind='probe'),
        EvalQuery(text="failure escalation recovery", expected_item_id=None, kind='probe'),
        EvalQuery(text="token efficiency memory", expected_item_id=None, kind='probe'),
    ]


def _load_into_adapters(items: List[Dict[str, Any]], tmpdir: Path) -> Tuple[Any, Any, Dict[int, str]]:
    """Load corpus into both JsonlMemoryStore and SqliteMemoryStore.

    Returns (jsonl_store, sqlite_store, item_ids) instances.
    """
    from memory_jsonl import JsonlMemoryStore
    from memory_sqlite import SqliteMemoryStore
    from memory_port import MemoryItem

    jsonl_dir = tmpdir / 'jsonl_store'
    sqlite_dir = tmpdir / 'sqlite_store'
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    sqlite_dir.mkdir(parents=True, exist_ok=True)

    jsonl_store = JsonlMemoryStore(jsonl_dir)
    sqlite_store = SqliteMemoryStore(sqlite_dir)

    item_ids = {}  # Map content hash to assigned id for recall matching

    for i, item_dict in enumerate(items):
        try:
            mem_item = MemoryItem(
                kind=item_dict.get('kind', 'note'),
                content=item_dict.get('content', ''),
                scope=item_dict.get('scope', ''),
                trust=item_dict.get('trust', 1.0),
                provenance=item_dict.get('provenance', {}),
                meta=item_dict.get('meta', {}),
            )

            # Both stores need the same item id, so append to jsonl first
            item_id = jsonl_store.append(mem_item)

            # Create a new item with the assigned id for sqlite
            mem_item_copy = MemoryItem(
                kind=mem_item.kind,
                content=mem_item.content,
                scope=mem_item.scope,
                trust=mem_item.trust,
                provenance=mem_item.provenance,
                meta=mem_item.meta,
                id=item_id,  # Use the same id
                created_at=mem_item.created_at,
            )
            sqlite_store.append(mem_item_copy)

            # Store mapping for leave-one-out eval
            item_ids[i] = item_id
        except Exception as e:
            log.warning("Failed to load item %d: %s", i, e)

    return jsonl_store, sqlite_store, item_ids


def _evaluate_adapter(
    store: Any,
    adapter_name: str,
    queries: List[EvalQuery],
    items: List[Dict[str, Any]],
    item_ids: Dict[int, str],
) -> Tuple[List[EvalResult], float]:
    """Run evaluation queries against one adapter.

    Returns (results, total_latency_ms).
    """
    results = []
    total_latency = 0.0

    for query in queries:
        start = time.perf_counter()
        recalled = store.recall(query.text, k=5)
        elapsed_ms = (time.perf_counter() - start) * 1000
        total_latency += elapsed_ms

        recalled_ids = {item.id for item in recalled}
        recalled_contents = {item.content for item in recalled}

        # For self-retrieval queries, find the matching item
        rank = 0
        if query.kind == 'self' and hasattr(query, '_expected_index'):
            expected_idx = query._expected_index  # type: ignore
            expected_content = items[expected_idx].get('content', '')

            for i, item in enumerate(recalled, start=1):
                if item.content == expected_content:
                    rank = i
                    break

        # For probe queries, just check if we got any results
        if query.kind == 'probe':
            rank = 1 if recalled else 0

        hit_at_1 = rank == 1
        hit_at_5 = rank > 0 and rank <= 5
        mrr = 1.0 / rank if rank > 0 else 0.0

        results.append(EvalResult(
            adapter_name=adapter_name,
            query_text=query.text,
            hit_at_1=hit_at_1,
            hit_at_5=hit_at_5,
            mrr=mrr,
            latency_ms=elapsed_ms,
            rank=rank,
        ))

    return results, total_latency


def _compute_metrics(results: List[EvalResult]) -> Dict[str, Any]:
    """Compute aggregate metrics from query results."""
    if not results:
        return {
            'hit_at_1': 0.0,
            'hit_at_5': 0.0,
            'mrr': 0.0,
            'median_latency_ms': 0.0,
            'count': 0,
        }

    latencies = [r.latency_ms for r in results]

    return {
        'hit_at_1': sum(1 for r in results if r.hit_at_1) / len(results),
        'hit_at_5': sum(1 for r in results if r.hit_at_5) / len(results),
        'mrr': statistics.mean(r.mrr for r in results),
        'median_latency_ms': statistics.median(latencies),
        'count': len(results),
    }


def _print_comparison_table(
    metrics_by_adapter: Dict[str, Dict[str, Any]],
    item_count: int,
    query_count: int,
):
    """Print a comparison table to stdout."""
    print()
    print("=" * 80)
    print(f"Memory Quality Evaluation — {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 80)
    print(f"Corpus: {item_count} items | Queries: {query_count}")
    print()

    adapters = sorted(metrics_by_adapter.keys())

    # Header
    print(f"{'Adapter':<20} | {'hit@1':<8} | {'hit@5':<8} | {'MRR':<8} | {'Latency (ms)':<12}")
    print("-" * 80)

    # Rows
    for adapter in adapters:
        m = metrics_by_adapter[adapter]
        print(f"{adapter:<20} | {m['hit_at_1']:>7.1%} | {m['hit_at_5']:>7.1%} | "
              f"{m['mrr']:>7.4f} | {m['median_latency_ms']:>11.2f}")

    print("=" * 80)
    print()


def _write_markdown_report(
    metrics_by_adapter: Dict[str, Dict[str, Any]],
    results_by_adapter: Dict[str, List[EvalResult]],
    item_count: int,
    query_count: int,
    corpus_summary: str,
) -> Path:
    """Write a dated markdown report to output/memory_quality/."""
    report_dir = Path('/home/clawd/claude/maro-orchestration/output/memory_quality')
    report_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    timestamp = now.strftime('%Y%m%d-%H%M%S')
    report_path = report_dir / f'memory_quality_{timestamp}.md'

    lines = [
        f"# Memory Quality Evaluation — {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## Summary",
        f"- Corpus: {item_count} items",
        f"- Queries: {query_count}",
        f"- Date: {now.isoformat()}",
        "",
        "## Corpus",
        corpus_summary,
        "",
        "## Results",
        "",
        "| Adapter | hit@1 | hit@5 | MRR | Median Latency (ms) |",
        "|---------|-------|-------|-----|---------------------|",
    ]

    for adapter in sorted(metrics_by_adapter.keys()):
        m = metrics_by_adapter[adapter]
        lines.append(
            f"| {adapter} | {m['hit_at_1']:.1%} | {m['hit_at_5']:.1%} | "
            f"{m['mrr']:.4f} | {m['median_latency_ms']:.2f} |"
        )

    lines.extend([
        "",
        "## Details",
        "",
    ])

    # Per-adapter details
    for adapter in sorted(results_by_adapter.keys()):
        results = results_by_adapter[adapter]
        lines.extend([
            f"### {adapter}",
            "",
            "| Query | Rank | hit@1 | hit@5 | Latency (ms) |",
            "|-------|------|-------|-------|--------------|",
        ])

        for r in results[:20]:  # Limit to first 20 for readability
            hit1 = "✓" if r.hit_at_1 else "✗"
            hit5 = "✓" if r.hit_at_5 else "✗"
            query_abbrev = r.query_text[:40] if r.query_text else "(empty)"
            lines.append(
                f"| {query_abbrev} | {r.rank} | {hit1} | {hit5} | {r.latency_ms:.2f} |"
            )

        if len(results) > 20:
            lines.append(f"| ... ({len(results)-20} more) | | | | |")

        lines.append("")

    lines.extend([
        "## Notes",
        "- Self-retrieval queries: extract salient words from each item, rank that item highly.",
        "- Probe queries: hand-written queries expecting to find relevant results.",
        "- MRR = Mean Reciprocal Rank (higher is better).",
        "- Latency = time to recall() one query.",
        "",
    ])

    report_path.write_text("\n".join(lines), encoding='utf-8')
    return report_path


def main():
    """Main entry point: run evaluation and print results."""
    import argparse
    ap = argparse.ArgumentParser(prog="memory_quality")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap corpus size (default: full workspace corpus)")
    opts = ap.parse_args()

    # Load corpus from workspace (read-only)
    log.info("Loading corpus from workspace memory...")
    corpus, total_read = _load_corpus_from_workspace(limit=opts.limit)
    if opts.limit and total_read > len(corpus):
        log.warning("corpus CAPPED at %d of %d readable items (--limit)",
                    len(corpus), total_read)

    if not corpus:
        log.error("No corpus loaded. Check ~/.maro/workspace/memory/ exists.")
        sys.exit(1)

    log.info("Loaded %d items from %d total read", len(corpus), total_read)

    # Generate queries
    log.info("Generating evaluation queries...")
    self_queries = _generate_self_retrieval_queries(corpus)
    probe_queries = _generate_hand_written_queries()
    all_queries = self_queries + probe_queries

    log.info("Generated %d self-retrieval + %d probe queries = %d total",
             len(self_queries), len(probe_queries), len(all_queries))

    # Load into adapters
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix='memory_quality_'))
    try:
        log.info("Loading corpus into adapters in %s...", tmpdir)
        jsonl_store, sqlite_store, item_ids = _load_into_adapters(corpus, tmpdir)

        log.info("Evaluating jsonl adapter...")
        jsonl_results, _ = _evaluate_adapter(
            jsonl_store, 'jsonl', all_queries, corpus, item_ids)

        log.info("Evaluating sqlite adapter...")
        sqlite_results, _ = _evaluate_adapter(
            sqlite_store, 'sqlite-fts5', all_queries, corpus, item_ids)

        # Compute metrics
        metrics_by_adapter = {
            'jsonl': _compute_metrics(jsonl_results),
            'sqlite-fts5': _compute_metrics(sqlite_results),
        }

        results_by_adapter = {
            'jsonl': jsonl_results,
            'sqlite-fts5': sqlite_results,
        }

        # Print results
        _print_comparison_table(metrics_by_adapter, len(corpus), len(all_queries))

        # Generate corpus summary
        kinds = {}
        for item in corpus:
            k = item.get('kind', 'unknown')
            kinds[k] = kinds.get(k, 0) + 1

        corpus_summary_lines = [
            f"- Total items: {len(corpus)}",
            "- Distribution by kind:",
        ]
        for k in sorted(kinds.keys()):
            corpus_summary_lines.append(f"  - {k}: {kinds[k]}")
        corpus_summary = "\n".join(corpus_summary_lines)

        # Write report
        log.info("Writing report...")
        report_path = _write_markdown_report(
            metrics_by_adapter,
            results_by_adapter,
            len(corpus),
            len(all_queries),
            corpus_summary,
        )
        log.info("Report written to %s", report_path)

    finally:
        # Cleanup
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
