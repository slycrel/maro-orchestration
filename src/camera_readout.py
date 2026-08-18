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

  6. Portability census (--portability, §14a slice 1) — per-LESSON view
     of the same join: every camera-cited lesson's citations split
     home/foreign (run project vs the slug of the lesson's own
     source_goal), verdicted foreign citations folded into a Beta
     posterior mean (s+1)/(s+f+2). INSTRUMENT ONLY — selection behavior
     is untouched; this is the evidence base the §14a
     stamp-categorical/globality-probabilistic direction (decision
     e2b83703) builds on IF the estimate discriminates. Pre-registered
     gate: if at ~30 foreign verdicted citations the per-lesson spread
     is indistinguishable from the 0.5 prior, the estimate is not
     discriminating and the ranker-consumption slice does NOT build.
     Unresolved lesson ids, unjudged runs, and metadata-less runs are
     counted in the output, never silently dropped.

Read-only. Usage:

    PYTHONPATH=src python3 -m camera_readout [--runs-root PATH] [--limit N]
    PYTHONPATH=src python3 -m camera_readout --portability
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from context_budget import clip as _clip
from jsonl_utils import loads_clean, store_text

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


def _lesson_origins(warn=print) -> Dict[str, Dict[str, str]]:
    """lesson_id -> {source_goal, task_type, preview} from the tiered
    stores (MEDIUM + LONG) plus the archive — no decay filtering, and
    archived lessons resolve too: a lesson cited while live and archived
    since still has its citations as evidence (live census: 23/79 cited
    ids had been archived by read time).

    ``warn`` defaults to print for the CLI readout; runtime callers
    (portability.refresh_cache rides loop finalize) pass a logger so an
    instrument warning can never pollute a run's captured stdout
    (slice-2 r1, architect)."""
    out: Dict[str, Dict[str, str]] = {}
    for tier in ("medium", "long"):
        try:
            from knowledge_web import load_tiered_lessons
            for tl in load_tiered_lessons(tier, limit=None, min_score=0.0):
                lid = getattr(tl, "lesson_id", "") or ""
                if lid and lid not in out:
                    out[lid] = {
                        "source_goal": getattr(tl, "source_goal", "") or "",
                        "task_type": getattr(tl, "task_type", "") or "",
                        # NO `or ""` on scope: it would flatten False/0/[]/{}
                        # to "" before the screen ran, so falsy corruption
                        # reported as honestly-unstamped and the malformed
                        # WARNING could never fire for it (r3 skeptic — the
                        # r2 "0 malformed" live claim was really "0 among the
                        # truthy shapes"). Pass the raw value; coerce_scope
                        # classifies it.
                        "scope": getattr(tl, "scope", ""),
                        # §14a: stamps are comparable WITHIN a labeller, not
                        # across them (memory.py's scope-vocabulary note).
                        # Imported rows were stamped on another machine by
                        # another model; the census has to be able to say so.
                        "imported": bool(getattr(tl, "imported", None)),
                        "preview": _clip(getattr(tl, "lesson", "") or "",
                                         60),
                    }
        except Exception as exc:
            warn(f"  WARNING: {tier}-tier lesson store read failed "
                 f"({exc}) — that tier's ids will report unresolved")
    try:
        from knowledge_web import _lessons_archive_path
        with _lessons_archive_path().open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                lid = str(row.get("lesson_id") or "")
                if lid and lid not in out:
                    out[lid] = {
                        "source_goal": str(row.get("source_goal") or ""),
                        "task_type": str(row.get("task_type") or ""),
                        "scope": row.get("scope", ""),   # raw — see above
                        "imported": bool(row.get("imported")),
                        "preview": _clip(str(row.get("lesson") or ""), 60),
                    }
    except FileNotFoundError:
        pass
    except Exception as exc:
        warn(f"  WARNING: lesson archive read failed ({exc}) — archived "
             f"ids will report unresolved")
    return out


# Shared with rank-time consumption (§14a slice 2): the census and the
# recall weighting hook must classify identically or the measurement and
# the behavior drift apart. Definitions live in portability.py; the
# aliases keep this module's census self-contained to read.
from portability import (  # noqa: E402
    beta_mean as _beta_mean,
    is_home as _is_home,
    unattributable_source as _unattributable_source,
)
# The evidence bar is read through the MODULE, never aliased: `from x import
# CONST` snapshots the value at import time, so a census claiming to "mirror
# the rank-time bar" would keep reporting 3 while ranking used something else
# (r1 review — verified: reassigning portability.MIN_FOREIGN_EVIDENCE left the
# alias at 3, and apply_portability reads the global at call time). The
# functions above are safe to alias; a rebindable scalar is not.
import portability as _portability  # noqa: E402
# The scope screen lives in knowledge_web, next to the vocabulary it screens
# against — r2 put a copy here and left three bare membership tests on the
# write and transport paths, one of which became a crash. One screen, four
# callers (r3 design lens). Store rows are JSON and nothing validates them on
# read, so `scope` can hold any decodable type: `["world"] in _LESSON_SCOPES`
# RAISES, and `format(123, ":7s")` raises too, so the type check must come
# before the vocabulary check for the per-row print to survive at all.
from knowledge_web import _LESSON_SCOPES, coerce_scope as _clean_scope  # noqa: E402

# Comparing the two stamped buckets on a handful of citations would be
# numerology. Stated floor, per bucket, so the readout can say plainly whether
# the method-vs-world question is readable yet instead of leaving a reader to
# eyeball it (r1 review: a 1-lesson bucket printed identically to a rich one).
_SCOPE_COMPARISON_FLOOR = 10


def portability_census(per_run: List[Dict[str, Any]],
                       warn=print) -> Dict[str, Any]:
    """Per-lesson citation×verdict aggregation (§14a slice 1). Pure read.

    Home/foreign uses the same goal→slug derivation the run side uses
    (loop_artifacts.resolve_project_slug on the lesson's source_goal vs
    the run's recorded project) — time-dependent project matching means
    a historical mismatch is possible. Exact goal-text equality is also
    honored as home, compared under the mint-time 120-char excerpt cap
    (r1 review: source_goal is stored truncated — 121/123 verdicted
    citations in the live corpus — so full-string equality was dead for
    realistic goals; the slug leg, first-five-words, was doing the real
    work).

    Verdict source is metadata.json (goal_achieved), NOT run_card.json
    like this module's frame-level section 4: post-hoc verdict repairs
    (audit_repair, operator re-stamp) land in metadata, so it is the
    fresher authority. Measured divergence across the 50 frame-bearing
    runs at review time: 0.
    """
    origins = _lesson_origins(warn=warn)
    lessons: defaultdict = defaultdict(lambda: {
        "cites": 0, "runs": set(), "home": 0, "foreign": 0,
        "foreign_s": 0, "foreign_f": 0, "unjudged": 0,
        "unattributable": 0})
    unresolved: Counter = Counter()
    runs_no_meta = 0
    for r in per_run:
        meta = None
        try:
            meta = json.loads((r["dir"] / "metadata.json").read_text(
                encoding="utf-8"))
        except Exception:
            runs_no_meta += 1
        project = str((meta or {}).get("project") or "")
        goal = str((meta or {}).get("prompt") or "")
        achieved = (meta or {}).get("goal_achieved")
        run_key = r["dir"].name
        for f in r["frames"]:
            for lid in (f.get("chosen") or {}).get("lesson_ids") or []:
                origin = origins.get(lid)
                if origin is None:
                    unresolved[lid] += 1
                    continue
                rec = lessons[lid]
                rec["cites"] += 1
                rec["runs"].add(run_key)
                src_goal = origin["source_goal"]
                if _unattributable_source(src_goal):
                    rec["unattributable"] += 1
                    continue
                if _is_home(src_goal, goal, project):
                    rec["home"] += 1
                    continue
                rec["foreign"] += 1
                if achieved is True:
                    rec["foreign_s"] += 1
                elif achieved is False:
                    rec["foreign_f"] += 1
                else:
                    rec["unjudged"] += 1
    rows = []
    for lid, rec in lessons.items():
        s, fl = rec["foreign_s"], rec["foreign_f"]
        scope_clean, scope_bad = _clean_scope(origins[lid].get("scope", ""))
        rows.append({
            "lesson_id": lid,
            "preview": origins[lid]["preview"],
            # §14a slice 3: mint-time method/world stamp, "" for rows minted
            # before the stamp existed (the whole pre-2026-08-15 corpus).
            # Sanitized HERE, the one place every downstream consumer (the
            # per-row print, the rollup, refresh_cache) reads it from — a
            # forged row's int scope crashed the print's `:7s` format and an
            # unhashable one crashed the rollup's bucket key (r2 Expert QA).
            "scope": scope_clean,
            "imported": bool(origins[lid].get("imported")),
            # The row carries the SANITIZED scope (see above), so the rollup
            # can no longer tell "" apart from "was corrupt" — double
            # sanitization would silently absorb exactly the corruption the
            # WARNING exists to report. Carry the verdict alongside the value.
            "scope_malformed": scope_bad,
            "cites": rec["cites"],
            "runs": len(rec["runs"]),
            "home": rec["home"],
            "foreign": rec["foreign"],
            "foreign_s": s,
            "foreign_f": fl,
            "foreign_unjudged": rec["unjudged"],
            "unattributable": rec["unattributable"],
            "portability": _beta_mean(s, fl) if (s + fl) else None,
        })
    rows.sort(key=lambda r: (-(r["foreign_s"] + r["foreign_f"]),
                             -r["cites"]))
    return {"rows": rows, "unresolved": unresolved,
            "runs_no_meta": runs_no_meta,
            "by_scope": _scope_rollup(rows)}


def _scope_rollup(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate portability evidence by mint-time scope stamp (§14a slice 3).

    This is the census half of the slice: the stamp exists so the
    method-vs-world transferability claim — settled in §14a, contested by the
    2026-08-11 panel — finally has a denominator instead of an argument. It
    reports rather than concludes, and the honest reading today is that it
    cannot conclude yet: at slice-3 ship the entire cited corpus was minted
    before the stamp existed, so every row lands in "unstamped" and the
    method/world buckets are empty by construction. They fill only as
    newly-minted lessons accrue foreign verdicted citations.

    ``pooled`` is the pooled Beta mean over the bucket's citations, NOT the
    mean of per-lesson portabilities: one lesson with 5 foreign verdicts is
    stronger evidence than five with one each, and averaging the per-lesson
    figures would hand them equal weight.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    malformed = 0
    for r in rows:
        # Validate at the READ site too, not just at every write site. The
        # census reads rows off disk, and a hand-edited or corrupted store row
        # carrying `"scope": "banana"` would otherwise mint its own bucket:
        # invisible in the printed table (which walks a fixed name list) yet
        # counted as stamped and evidenced, and — before this guard — able to
        # crash the readout outright (r2 fix-audit reproduced the IndexError).
        # Unrecognized values fold into "unstamped", which is what they are,
        # and get counted so corruption is reported rather than absorbed.
        raw_scope, bad = _clean_scope(r.get("scope", ""))
        # OR, not either-or: rows off portability_census arrive pre-sanitized
        # and carry their verdict in `scope_malformed`; rows handed straight to
        # this function (tests, future callers) carry the raw value. Reading
        # only one of the two loses the report on whichever path it isn't.
        malformed += bool(bad or r.get("scope_malformed"))
        b = buckets.setdefault(raw_scope or "unstamped",
                               {"lessons": 0, "cites": 0, "foreign": 0,
                                "foreign_s": 0, "foreign_f": 0, "evidenced": 0,
                                "imported": 0})
        # Only imported_fv needs the setdefault — it is absent from the literal
        # above on purpose, so a bucket built by an older call still gains it.
        b.setdefault("imported_fv", 0)
        b["lessons"] += 1
        b["imported"] += bool(r.get("imported"))
        # Membership is not contamination. A pooled figure is only polluted by
        # an imported row whose VERDICTS are in it — an imported row with zero
        # foreign verdicted citations contributes nothing to foreign_s/f, so
        # suppressing the comparison over its mere presence would let one
        # uncited pack row void the §14a verdict permanently (r4, two lenses).
        if r.get("imported") and (r["foreign_s"] + r["foreign_f"]) > 0:
            b["imported_fv"] += 1
        b["cites"] += r["cites"]
        b["foreign"] += r["foreign"]
        b["foreign_s"] += r["foreign_s"]
        b["foreign_f"] += r["foreign_f"]
        # "Evidenced" mirrors the rank-time bar (portability.MIN_FOREIGN_
        # EVIDENCE) so the census counts what would actually be actionable.
        if (r["foreign_s"] + r["foreign_f"]
                >= _portability.MIN_FOREIGN_EVIDENCE):
            b["evidenced"] += 1
    for b in buckets.values():
        fv = b["foreign_s"] + b["foreign_f"]
        b["pooled"] = _beta_mean(b["foreign_s"], b["foreign_f"]) if fv else None
    for b in buckets.values():
        b.setdefault("malformed", 0)
    if malformed:
        # Reported on the bucket the rows actually landed in, so the count
        # can never be mistaken for a scope category of its own. setdefault,
        # not indexing: a direct caller (this function's documented second
        # audience) can hand in a row with a VALID scope and a truthy
        # scope_malformed, and there would then be no "unstamped" bucket to
        # write to — same instrument-must-never-crash class as the `both[0]`
        # guard below (r3, two lenses).
        buckets.setdefault("unstamped",
                           {"lessons": 0, "cites": 0, "foreign": 0,
                            "foreign_s": 0, "foreign_f": 0, "evidenced": 0,
                            "imported": 0, "pooled": None,
                            "malformed": 0})["malformed"] = malformed
    return buckets


def _stamp_coverage() -> Dict[str, Any]:
    """Store-wide stamp counts, read straight off the tiers — NOT the
    citation join.

    Everything else the census reports about `scope` is citation-gated, and a
    fresh mint takes days-to-weeks to accrue foreign verdicted citations. So
    if the extraction schema stopped emitting the slot tomorrow, the readout
    would keep printing "every cited lesson predates the stamp … by
    construction" — the same reassuring line it prints when the writer is
    working perfectly. Total write-path failure and healthy-but-early were
    indistinguishable (r3 Expert QA). This line separates them: it answers
    "is the stamp landing at all?" without waiting on citations.
    """
    out: Dict[str, Any] = {"total": 0, "readable": 0, "malformed": 0,
                           "imported": 0, "quarantined": 0, "newest": ""}
    for sc in _LESSON_SCOPES:
        out[sc] = 0
    try:
        from knowledge_web import (load_tiered_lessons, MemoryTier,
                                   _tiered_lessons_path,
                                   unparseable_sidecar_count)
        for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
            rows = load_tiered_lessons(tier, limit=None, min_score=0.0,
                                       raw=True)
            # Denominator from the FILE, not the loader. load_tiered_lessons
            # swallows every read failure internally and returns [], so
            # counting its output made an unreadable store look like an empty
            # one — and unparseable rows are excluded by construction, so a
            # store that is 90% unreadable printed "100% stamped" (r4, two
            # lenses). "Whole store" has to mean the bytes on disk.
            path = _tiered_lessons_path(tier)
            on_disk = 0
            if path.exists():
                on_disk = sum(1 for line in
                              path.read_text(encoding="utf-8",
                                             errors="replace").splitlines()
                              if line.strip())
            if on_disk and not rows:
                raise RuntimeError(
                    f"{tier}: {on_disk} row(s) on disk, loader returned none")
            out["total"] += on_disk
            out["readable"] += len(rows)
            out["quarantined"] += unparseable_sidecar_count(tier)
            for tl in rows:
                scope, bad = _clean_scope(getattr(tl, "scope", ""))
                if bad:
                    out["malformed"] += 1
                elif scope:
                    # .get()+1, not out[scope]: driven by the vocabulary, so
                    # adding a third scope value cannot KeyError the whole
                    # instrument (r4 Expert QA).
                    out[scope] = out.get(scope, 0) + 1
                    if getattr(tl, "imported", None):
                        # An imported stamp is evidence ANOTHER machine's
                        # writer fired, not ours. BACKLOG reads this counter
                        # as "did the production lane stamp?", so the two
                        # must not be pooled.
                        out["imported"] += 1
                    stamped_at = str(getattr(tl, "recorded_at", "") or "")
                    if stamped_at > out["newest"]:
                        out["newest"] = stamped_at
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _print_portability(per_run: List[Dict[str, Any]]) -> None:
    print("\n== Portability census (§14a slice 1 — per-lesson "
          "citation × verdict) ==")
    c = portability_census(per_run)
    rows = c["rows"]
    if not rows:
        print("  no resolvable cited lessons in the scanned runs")
    total_fv = sum(r["foreign_s"] + r["foreign_f"] for r in rows)
    for r in rows:
        port = ("insufficient" if r["portability"] is None
                else f"{r['portability']:.2f}")
        print(f"  {r['lesson_id']:10s} cites={r['cites']:3d} "
              f"runs={r['runs']:3d} home={r['home']:3d} "
              f"foreign={r['foreign']:3d} "
              f"judged(s/f)={r['foreign_s']}/{r['foreign_f']} "
              f"portability={port:12s} "
              f"scope={(r.get('scope') or '-'):7s} {r['preview']}")
    print(f"  -- foreign verdicted citations total: {total_fv} "
          f"(pre-registered gate reads at ~30)")
    print("\n  -- by mint-time scope stamp (§14a slice 3) --")
    cov = _stamp_coverage()
    if cov.get("error"):
        print(f"     stamp coverage: UNREADABLE ({cov['error']}) — the "
              f"citation-gated lines below cannot be trusted as a statement "
              f"about the write path")
    else:
        stamped = sum(cov.get(sc, 0) for sc in _LESSON_SCOPES)
        local = stamped - cov["imported"]
        extra = ""
        if cov["imported"]:
            extra += f", {cov['imported']} of them IMPORTED (another machine's labeller)"
        if cov["quarantined"]:
            extra += f", {cov['quarantined']} row(s) quarantined as unparseable"
        if cov["readable"] != cov["total"]:
            extra += (f", {cov['total'] - cov['readable']} row(s) on disk the "
                      f"loader cannot read")
        print(f"     stamp coverage (whole store, not citation-gated): "
              f"{stamped}/{cov['total']} rows stamped "
              f"(method {cov.get('method', 0)} / world {cov.get('world', 0)} / "
              f"malformed {cov['malformed']}){extra}, newest stamped mint "
              f"{cov['newest'][:19] or 'none'}")
        if cov["total"] and not local:
            print("     ^^ ZERO locally-minted stamps store-wide: this box's "
                  "write path has not landed a single stamp. Until this is non-zero the "
                  "lines below say nothing about method-vs-world — they only "
                  "say the corpus predates the stamp, which is also what a "
                  "silently broken extractor looks like.")
    by_scope = c.get("by_scope") or {}
    # Vocabulary-driven, with "unstamped" pinned last — the third hard-coded
    # mirror of _LESSON_SCOPES in this file (r4 fixed the coverage counter and
    # the coverage seed and missed this one, r5 test-auditor). A grown
    # vocabulary would have silently vanished from the printed table while
    # every total above it kept counting the rows.
    for name in sorted(_LESSON_SCOPES) + ["unstamped"]:
        b = by_scope.get(name)
        if not b:
            continue
        fv = b["foreign_s"] + b["foreign_f"]
        pooled = "insufficient" if b["pooled"] is None else f"{b['pooled']:.2f}"
        print(f"     {name:10s} lessons={b['lessons']:3d} cites={b['cites']:4d} "
              f"foreign={b['foreign']:3d} judged(s/f)={b['foreign_s']}/{b['foreign_f']} "
              f"pooled={pooled:12s} evidenced={b['evidenced']} "
              f"imported={b.get('imported', 0)}"
              f"({b.get('imported_fv', 0)} cited)")
    stamped_buckets = {k: v for k, v in by_scope.items() if k != "unstamped"}
    stamped = sum(v["lessons"] for v in stamped_buckets.values())
    bar = _portability.MIN_FOREIGN_EVIDENCE
    if not rows:
        # Distinct from "nothing is stamped": an empty census means the
        # citation→lesson resolution found nothing at all, which may be a
        # broken lookup rather than an expected state. Claiming "every cited
        # lesson predates the stamp" here would explain away a possible bug
        # (r1 review, reproduced with an all-unresolved corpus).
        print("     (no resolvable cited lessons at all — this is a resolution "
              "result, not a statement about stamps; see the unresolved count)")
    elif not stamped:
        print("     (every cited lesson predates the stamp — the method-vs-world "
              "comparison has no data yet, by construction)")
    elif not any(v["evidenced"] for v in stamped_buckets.values()):
        print(f"     ({stamped} stamped lesson(s) cited, none yet at the "
              f"{bar}-verdict bar — comparison still premature)")
    else:
        # Some evidence exists. Say explicitly whether it is enough to read,
        # rather than printing two pooled figures side by side and letting
        # them imply a comparison they cannot support.
        both = [k for k in ("method", "world")
                if (stamped_buckets.get(k) or {}).get("evidenced")]
        thinnest = min((stamped_buckets[k]["foreign_s"] + stamped_buckets[k]["foreign_f"]
                        for k in both), default=0)
        if not both:
            # Belt to the read-site validation's suspenders: `stamped` counts
            # every non-unstamped bucket, so reaching here at all would mean a
            # scope category exists that this branch does not know about.
            # Indexing both[0] unguarded is what crashed the whole readout in
            # the r2 audit — never let an instrument take the process down.
            print("     (evidence exists under no recognized scope — the "
                  "readout cannot name it; treat the store as suspect)")
        elif len(both) < 2:
            print(f"     (only the {both[0]!r} bucket has evidence — a "
                  f"one-sided figure is not a comparison)")
        elif any(stamped_buckets[k].get("imported_fv") for k in both):
            # Stamps are comparable WITHIN a labeller, not across them: the
            # production lane labels ~81% method at 97.5% self-agreement, the
            # hosted-free lane ~44% at 88.8%. An imported row was stamped on
            # another machine by another model, so a bucket holding one has no
            # single base rate and the pooled figure is not a measurement of
            # anything. Refuse the verdict rather than print a comparison the
            # stamp's own contract forbids (r3, two lenses — pack.py claimed
            # the census separated these and it did not).
            n_imp = sum(stamped_buckets[k].get("imported_fv", 0) for k in both)
            print(f"     ({n_imp} of the compared buckets' stamped lesson(s) "
                  f"were minted on another machine by another labeller and "
                  f"their verdicts are pooled into the figures above — stamps "
                  f"are comparable within a labeller, not across them, so this "
                  f"is not a readable comparison)")
        elif thinnest < _SCOPE_COMPARISON_FLOOR:
            print(f"     (both buckets have evidence but the thinner one rests on "
                  f"{thinnest} verdicted citation(s), under the stated floor of "
                  f"{_SCOPE_COMPARISON_FLOOR} — read as a hint, not a result)")
        else:
            print(f"     (both buckets clear the floor of "
                  f"{_SCOPE_COMPARISON_FLOOR} verdicted citations — this "
                  f"comparison is readable)")
    n_malformed = sum(v.get("malformed", 0) for v in by_scope.values())
    if n_malformed:
        print(f"     WARNING: {n_malformed} cited lesson(s) carry a scope value "
              f"outside {{method, world}} — counted as unstamped; the store has "
              f"rows no write path in this tree can produce")
    n_unattr = sum(r["unattributable"] for r in rows)
    if n_unattr:
        print(f"  -- citations from sentinel/empty-source lessons "
              f"(manual/evolver/prereq mints): {n_unattr} — excluded from "
              f"home/foreign, counted here")
    if c["unresolved"]:
        print(f"  -- unresolved lesson ids: {len(c['unresolved'])} "
              f"({sum(c['unresolved'].values())} citations) — counted, "
              f"not hidden")
    if c["runs_no_meta"]:
        print(f"  -- runs with frames but unreadable metadata.json: "
              f"{c['runs_no_meta']} (their citations count as unjudged)")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-root", type=Path, default=None,
                    help="runs/ directory (default: workspace runs root)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the N most recently modified run dirs")
    ap.add_argument("--portability", action="store_true",
                    help="per-lesson citation×verdict census (§14a slice 1)")
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
    n_cards_unreadable = 0
    for rd in run_dirs:
        frames, n_torn = _load_frames(rd)
        n_torn_total += n_torn
        if not frames:
            continue
        card = None
        card_path = rd / "run_card.json"
        if card_path.is_file():
            try:
                card = loads_clean(store_text(card_path))
            except Exception:
                # Unreadable is NOT uncurated: the run WAS curated and its
                # card is torn — count it like torn frame lines, below.
                n_cards_unreadable += 1
        per_run.append({"dir": rd, "frames": frames, "card": card})

    n_frames = sum(len(r["frames"]) for r in per_run)
    print("== Camera readout ==")
    print(f"runs scanned: {len(run_dirs)}   runs with frames: {len(per_run)}"
          f"   frames: {n_frames}")
    if n_torn_total:
        print(f"  WARNING: {n_torn_total} unparsable frame line(s) skipped "
              f"— counts below undercount actual activity")
    if n_cards_unreadable:
        print(f"  WARNING: {n_cards_unreadable} run_card.json file(s) "
              f"unreadable — those runs render as uncurated below, but "
              f"they WERE curated")
    if not per_run:
        print("no camera frames yet — the writer ships with this readout; "
              "frames appear as instrumented runs execute.")
        return 0
    if args.portability:
        _print_portability(per_run)
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
