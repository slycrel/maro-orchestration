#!/usr/bin/env python3
"""Generate the paraphrase-query cache for memory_quality's fair lane.

Self-retrieval queries (an item's own salient words) are essentially the
lexical-overlap ranker's scoring function used as a query generator — they
rig the comparison. This script samples corpus items, asks a cheap LLM to
rephrase each as a natural question WITHOUT reusing the item's wording,
and caches the result keyed by content sha1 so the instrument can match
queries to items across runs.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/gen_paraphrase_queries.py [--n 60]

Writes output/memory_quality/paraphrase_queries.jsonl. Incremental: items
already in the cache are skipped, so re-runs only pay for new samples.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from memory_quality import (  # noqa: E402
    _content_sha1,
    _load_corpus_from_workspace,
)

CACHE = Path(__file__).resolve().parent.parent / \
    "output" / "memory_quality" / "paraphrase_queries.jsonl"

PROMPT = (
    "Below is a note from an engineering system's memory. Write ONE short "
    "search query (5-10 words) that someone would type to find this note "
    "later, WITHOUT reusing its distinctive words — rephrase with synonyms "
    "and different framing, as a colleague who half-remembers it would. "
    "Reply with the query only, no quotes, no explanation.\n\nNote: {content}"
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60,
                    help="target number of cached paraphrase queries")
    opts = ap.parse_args()

    corpus, _ = _load_corpus_from_workspace()
    if not corpus:
        print("no corpus", file=sys.stderr)
        return 1

    cached = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                cached[row["content_sha1"]] = row

    # Deterministic, spread sample: order by content hash, stride the list.
    usable = [it for it in corpus if len(it.get("content", "")) > 40]
    usable.sort(key=lambda it: _content_sha1(it["content"]))
    stride = max(1, len(usable) // opts.n)
    sample = usable[::stride][:opts.n]

    todo = [it for it in sample
            if _content_sha1(it["content"]) not in cached]
    print(f"corpus={len(corpus)} usable={len(usable)} sample={len(sample)} "
          f"cached={len(cached)} todo={len(todo)}")
    if not todo:
        return 0

    from llm import LLMMessage, MODEL_CHEAP, build_adapter
    adapter = build_adapter(model=MODEL_CHEAP)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(CACHE, "a", encoding="utf-8") as fh:
        for it in todo:
            content = it["content"][:600]
            try:
                resp = adapter.complete(
                    [LLMMessage("user", PROMPT.format(content=content))],
                    max_tokens=60, temperature=0.3)
                query = (resp.content or "").strip().strip('"').splitlines()[0]
            except Exception as exc:
                print(f"skip ({exc})", file=sys.stderr)
                continue
            if not query or len(query) < 8:
                continue
            fh.write(json.dumps({
                "content_sha1": _content_sha1(it["content"]),
                "query": query,
                "content_preview": it["content"][:80],
            }, ensure_ascii=False) + "\n")
            fh.flush()
            written += 1
            if written % 10 == 0:
                print(f"{written}/{len(todo)}")

    print(f"wrote {written} paraphrase queries → {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
