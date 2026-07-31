"""Camera readout — the consumer for source/camera_frames.jsonl.

Ships in the same chunk as the writer (consumer-first): walks run dirs,
reads each run's camera frames (camera_log.log_fork_frame), and reports:

  1. Coverage — how many runs/frames the numbers below actually rest on.
  2. Axis composition — which observation substrates were present at the
     fork, and how large (the "what could the player see" axes).
  3. Candidate stats — set sizes per source, where the chosen lessons
     ranked, how much of the scored mass they carried.
  4. Verdict join — chosen-score stats split by run_card.goal_achieved
     (the chunk-4 run-keyed join; runs without a card are counted, not
     hidden).
  5. Crude overdraw v1 — fraction of injected (rendered) lessons whose
     content tokens never echo in the run's result text. HONEST LABEL:
     echo is a crude proxy for use — a lesson can steer behavior without
     being quoted; treat this as an upper bound on waste, per-axis prior
     from the panel was 60-80% never-cited.

Read-only. Usage:

    PYTHONPATH=src python3 -m camera_readout [--runs-root PATH] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

_STOP = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "when", "where", "which",
    "how", "if", "as", "by", "from", "not", "can", "will", "do", "did",
    "does", "have", "had", "has", "should", "would", "could", "may",
})

_RESULT_READ_CAP = 200_000  # chars of result text per run


def _tokens(text: str) -> set:
    return {
        t for t in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split()
        if t not in _STOP and len(t) > 2
    }


def _load_frames(run_dir: Path) -> tuple:
    """Returns (frames, n_torn). Torn/unparsable lines are counted, not
    hidden — silently dropping them would present lost data as lower
    activity (adversarial-review 2026-07-31 F3)."""
    fp = run_dir / "source" / "camera_frames.jsonl"
    if not fp.exists():
        return [], 0
    frames = []
    n_torn = 0
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except Exception:
            n_torn += 1
    return frames, n_torn


def _result_text(card: Dict[str, Any]) -> str:
    """Best available result text for the overdraw proxy."""
    parts = []
    rp = card.get("result_path")
    if rp:
        try:
            parts.append(Path(rp).read_text(
                encoding="utf-8", errors="replace")[:_RESULT_READ_CAP])
        except Exception:
            pass
    if not parts:
        for k in ("result_excerpt", "answer_summary"):
            if card.get(k):
                parts.append(str(card[k]))
    return "\n".join(parts)


def _chosen_source(frame: Dict[str, Any], lesson_id: str) -> str:
    """Which candidate source a chosen lesson came from (first match in
    selection-priority order)."""
    for source in ("agenda", "untyped", "flat"):
        for c in frame.get("candidates", {}).get(source, []):
            if lesson_id and c.get("lesson_id") == lesson_id:
                return source
    return "unmatched"


def _fmt_pct(num: int, den: int) -> str:
    return f"{num}/{den} ({100.0 * num / den:.0f}%)" if den else "0/0 (—)"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-root", type=Path, default=None,
                    help="runs/ directory (default: workspace runs root)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the N most recently modified run dirs")
    args = ap.parse_args(argv)

    root = args.runs_root
    if root is None:
        from runs import runs_root
        root = runs_root()
    if not root.exists():
        print(f"no runs root at {root}")
        return 1

    run_dirs = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime, reverse=True)
    if args.limit:
        run_dirs = run_dirs[:args.limit]

    per_run: List[Dict[str, Any]] = []
    n_torn_total = 0
    for rd in run_dirs:
        frames, n_torn = _load_frames(rd)
        n_torn_total += n_torn
        if not frames:
            continue
        card = None
        try:
            card = json.loads((rd / "run_card.json").read_text(
                encoding="utf-8"))
        except Exception:
            pass
        per_run.append({"dir": rd, "frames": frames, "card": card})

    n_frames = sum(len(r["frames"]) for r in per_run)
    print("== Camera readout ==")
    print(f"runs scanned: {len(run_dirs)}   runs with frames: {len(per_run)}"
          f"   frames: {n_frames}")
    if n_torn_total:
        print(f"  WARNING: {n_torn_total} unparsable frame line(s) skipped "
              f"— counts below undercount actual activity")
    if not per_run:
        print("no camera frames yet — the writer ships with this readout; "
              "frames appear as instrumented runs execute.")
        return 0
    with_card = [r for r in per_run if r["card"]]
    print(f"runs with frames + run_card: {len(with_card)} "
          f"(verdict join rests on these)")

    # -- 2. axis composition ------------------------------------------------
    print("\n== Axis composition (substrate presence at the fork) ==")
    presence: Counter = Counter()
    chars: defaultdict = defaultdict(list)
    for r in per_run:
        for f in r["frames"]:
            for name, sz in (f.get("axes", {})
                             .get("substrate_chars", {}) or {}).items():
                if isinstance(sz, (int, float)) and sz > 0:
                    presence[name] += 1
                    chars[name].append(sz)
    for name in sorted(presence, key=lambda k: -presence[k]):
        mean_sz = sum(chars[name]) / len(chars[name])
        print(f"  {name:18s} present {_fmt_pct(presence[name], n_frames):>14s}"
              f"   mean {mean_sz:7.0f} chars")
    absent = {k for r in per_run for f in r['frames']
              for k in (f.get('axes', {}).get('substrate_chars', {}) or {})}
    for name in sorted(absent - set(presence)):
        print(f"  {name:18s} present {_fmt_pct(0, n_frames):>14s}")

    # -- 3. candidate stats -------------------------------------------------
    print("\n== Candidates ==")
    src_sizes: defaultdict = defaultdict(list)
    for r in per_run:
        for f in r["frames"]:
            for source, cands in (f.get("candidates") or {}).items():
                src_sizes[source].append(len(cands))
    for source, sizes in sorted(src_sizes.items()):
        print(f"  {source:8s} in {len(sizes)}/{n_frames} frames, "
              f"mean set size {sum(sizes)/len(sizes):.1f}")

    chosen_srcs: Counter = Counter()
    chosen_ranks: List[int] = []
    chosen_shares: List[float] = []
    n_chosen = 0
    for r in per_run:
        for f in r["frames"]:
            ids = (f.get("chosen") or {}).get("lesson_ids") or []
            n_chosen += len(ids)
            for lid in ids:
                source = _chosen_source(f, lid)
                chosen_srcs[source] += 1
                for pos, c in enumerate(
                        (f.get("candidates") or {}).get(source, [])):
                    if c.get("lesson_id") == lid:
                        chosen_ranks.append(pos)
                        if isinstance(c.get("score_share"), (int, float)):
                            chosen_shares.append(c["score_share"])
                        break
    print(f"  chosen lessons: {n_chosen} "
          f"({n_chosen / n_frames:.1f}/frame)" if n_frames else "")
    for source, n in chosen_srcs.most_common():
        print(f"    from {source:9s} {_fmt_pct(n, n_chosen)}")
    if chosen_ranks:
        rank_hist = Counter(chosen_ranks)
        hist = "  ".join(f"r{k}:{rank_hist[k]}" for k in sorted(rank_hist))
        print(f"  chosen rank in its source list: {hist}")
    if chosen_shares:
        print(f"  mean score_share of scored chosen: "
              f"{sum(chosen_shares)/len(chosen_shares):.3f} "
              f"(n={len(chosen_shares)})")

    # -- 4. verdict join ----------------------------------------------------
    # Scored sources cover the full selection chain: agenda leads, untyped
    # tops up (recall.py) — an agenda-empty run's untyped selections are
    # real scored choices, not join misses (F2). Buckets split by ranker
    # family because raw scores are unitless across families (F6).
    print("\n== Verdict join (run_card.goal_achieved) ==")
    by_verdict: defaultdict = defaultdict(lambda: {"frames": 0, "tops": [],
                                                   "chosen": []})
    for r in per_run:
        v = "no-card"
        if r["card"] is not None:
            ga = r["card"].get("goal_achieved")
            v = {True: "achieved", False: "not-achieved"}.get(ga, "unjudged")
        for f in r["frames"]:
            ranker = str((f.get("extra") or {}).get("ranker") or "?")
            bucket = by_verdict[(v, ranker)]
            bucket["frames"] += 1
            scored_cands = [
                c for source in ("agenda", "untyped")
                for c in (f.get("candidates") or {}).get(source) or []
                if isinstance(c.get("score"), (int, float))]
            if scored_cands:
                bucket["tops"].append(max(c["score"] for c in scored_cands))
            ids = set((f.get("chosen") or {}).get("lesson_ids") or [])
            seen_chosen = set()
            for c in scored_cands:
                lid = c.get("lesson_id")
                if lid in ids and lid not in seen_chosen:
                    bucket["chosen"].append(c["score"])
                    seen_chosen.add(lid)
    for (v, ranker), b in sorted(by_verdict.items()):
        top = (f"top-cand {sum(b['tops'])/len(b['tops']):.4f}"
               if b["tops"] else "top-cand —")
        cho = (f"chosen {sum(b['chosen'])/len(b['chosen']):.4f}"
               if b["chosen"] else "chosen —")
        print(f"  {v:13s} [{ranker:6s}] frames {b['frames']:4d}   "
              f"mean {top}   mean {cho}")
    print("  (scores are raw ranker scores — rows are per ranker family; "
          "never compare across families)")

    # -- 5. crude overdraw v1 ----------------------------------------------
    print("\n== Overdraw v1 (chosen-but-never-echoed in result text) ==")
    n_runs_scored = 0
    n_lessons_seen = 0
    n_lessons_echoed = 0
    for r in per_run:
        if not r["card"]:
            continue
        text = _result_text(r["card"])
        if not text:
            continue
        result_toks = _tokens(text)
        previews: List[str] = []
        for f in r["frames"]:
            previews.extend((f.get("chosen") or {}).get("previews") or [])
        if not previews:
            continue
        n_runs_scored += 1
        for p in previews:
            toks = _tokens(p)
            if not toks:
                continue
            n_lessons_seen += 1
            overlap = len(toks & result_toks) / len(toks)
            if overlap >= 0.5:
                n_lessons_echoed += 1
    if n_lessons_seen:
        overdraw = 1.0 - n_lessons_echoed / n_lessons_seen
        print(f"  runs with frames+result text: {n_runs_scored}")
        print(f"  chosen lessons echoed (≥50% content-token overlap): "
              f"{_fmt_pct(n_lessons_echoed, n_lessons_seen)}")
        print(f"  overdraw v1: {overdraw:.0%} of injected lessons never "
              f"echo in the result")
        print("  (crude proxy: echo ≠ use — an upper bound on waste, not "
              "a verdict; panel prior was 60-80%)")
    else:
        print("  not computable yet — needs runs with frames AND result "
              "text AND rendered lessons")

    return 0


if __name__ == "__main__":
    sys.exit(main())
