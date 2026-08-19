#!/usr/bin/env python3
"""Reconstruct, for every run in a maro workspace, which path it took through
the orchestration pipeline. Emits one JSON blob for the process-map viewer.

Node ids match the topology declared in the viewer (TOPOLOGY in process_map.html).
Every node visit records how it was detected so the viewer can distinguish
evidence strength:
  "a" = attributed  (event carried this run's loop_id, or came from a run-scoped file)
  "w" = windowed    (event had no loop_id; it fell inside the run's time window,
                     and the captain's-log slice is NOT run-scoped -- may belong
                     to a concurrent run)
"""
import json, os, re, sys
from pathlib import Path
from collections import defaultdict

WS = Path(os.environ.get("MARO_WORKSPACE", Path.home() / "maro-box-copy/workspace"))
RUNS = WS / "runs"

# event types worth reading out of the (large, non-run-scoped) log slices
EVENTS = {
    "RECALL_PERFORMED", "SCOPE_GENERATED", "SCOPE_PARSE_FAILED", "SCOPE_SKIPPED",
    "CUTS_DRAWN", "LOOP_CREATED", "FENCE_EXTENDED", "FENCE_WRITE_BLOCKED",
    "BOUNDARY_EXPANDED", "STEP_TOO_BROAD", "VALIDATION_LADDER", "SCAVENGE_DETECTED",
    "METACOGNITIVE_DECISION", "NAVIGATOR_DECIDED", "NAVIGATOR_ACTED",
    "NAVIGATOR_ADJUDICATED", "AUTO_RECOVERY", "DIAGNOSIS", "CLOSURE_VERDICT",
    "QUALITY_GATE_VERDICT", "QUALITY_GATE_SECOND_FAMILY", "QUALITY_GATE_CROSS_REF",
    "QUALITY_GATE_OVERRULED", "CLAIM_PROBED", "CLAIM_VERIFIER_OUTCOME",
    "FABRICATION_DETECTED", "DONE_WITHOUT_VERDICT", "REANCHOR_CHECKED",
    "WORLD_FACTS_LANDED", "LESSON_RECORDED", "SKILL_PROMOTED",
    "KNOWLEDGE_NODE_PROMOTED", "SKILL_VARIANT_CREATED", "HYPOTHESIS_CREATED",
    "BUDGET_EXTENDED", "MEMORY_CONSOLIDATED",
}


def jload(p):
    try:
        return json.loads(p.read_text(errors="replace"))
    except Exception:
        return None


def jlines(p, cap=None):
    out = []
    try:
        with p.open(errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
                if cap and len(out) >= cap:
                    break
    except Exception:
        pass
    return out


def slice_events(rd, loop_ids, started, ended):
    """Read the captain's-log slice. The slice is a byte range of the GLOBAL log
    from this run's start offset to EOF, so it carries concurrent runs' events and
    a post-ended_at learning tail. Partition rather than trust."""
    p = rd / "build" / "captains_log_slice.jsonl"
    att, win = [], []
    if not p.exists():
        return att, win
    lset = set(loop_ids)
    try:
        with p.open(errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                # cheap prefilter before json.loads -- these files get big
                if not any(e in line for e in EVENTS):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                et = ev.get("event_type") or ev.get("event") or ev.get("kind")
                if et not in EVENTS:
                    continue
                ts = ev.get("ts") or ev.get("timestamp") or ""
                ctx = ev.get("context") or {}
                lid = ev.get("loop_id") or ctx.get("loop_id")
                if lid and lid in lset:
                    att.append((et, ev, ctx, ts))
                elif not lid and started <= ts <= (ended or "9"):
                    win.append((et, ev, ctx, ts))
    except Exception:
        pass
    return att, win


def build(rd):
    meta = jload(rd / "metadata.json")
    if not meta:
        return None
    hid = meta.get("handle_id") or rd.name.split("-")[0]
    started = meta.get("started_at") or ""
    ended = meta.get("ended_at") or ""
    card = jload(rd / "run_card.json") or {}
    build_dir = rd / "build"
    src = rd / "source"

    loops_meta = meta.get("loops") or []
    loop_ids = [l.get("loop_id") for l in loops_meta if l.get("loop_id")]
    if not loop_ids:
        loop_ids = meta.get("loop_ids") or []
    logs = sorted(build_dir.glob("loop-*-log.json"))
    if not loop_ids:
        loop_ids = [p.name[5:-9] for p in logs]

    V = {}  # node id -> {"n": count, "e": evidence, ...attrs}

    def hit(node, ev="a", **attrs):
        d = V.setdefault(node, {"n": 0, "e": ev, "a": {}})
        d["n"] += 1
        # attributed evidence outranks windowed
        if ev == "a":
            d["e"] = "a"
        for k, v in attrs.items():
            if v is not None:
                d["a"][k] = v

    # ---------- R0 intake ----------
    origin = meta.get("origin") or {}
    osrc = origin.get("source") or ""
    hit("intake.arrive", label=osrc or "unrecorded")
    # `origin` was added late -- absence means "not recorded", NOT "came from the CLI".
    if osrc:
        hit({
            "telegram": "intake.listener", "slack": "intake.listener",
            "task_store": "intake.queue", "queue": "intake.queue",
            "scheduler": "intake.scheduler", "heartbeat": "intake.scheduler",
            "loop_continuation": "intake.queue", "loop_escalation": "intake.queue",
        }.get(osrc, "intake.cli"), src=osrc)
    nav = origin.get("dispatch_navigator") or {}
    if nav:
        hit("intake.navigator", move=nav.get("move"), conf=nav.get("confidence"))
        if (nav.get("move") or "") == "escalate":
            hit("intake.nav_escalate")
    if meta.get("classification_reason") == "recall_guard":
        hit("intake.guard_refuse")
    hit("intake.open_run")

    # ---------- R1 routing ----------
    lane = meta.get("lane") or "agenda"
    hit("route.classify", lane=lane)
    if (src / "recall_citations.json").exists():
        hit("route.recall")
    if meta.get("persona"):
        hit("route.persona", persona=meta.get("persona"),
            conf=meta.get("persona_confidence"),
            fallback=bool(meta.get("persona_fallback")),
            forced=bool(meta.get("persona_forced")))
    if lane == "now":
        hit("route.now")
        nowfiles = list((rd / "artifact").glob("now-*.json")) if (rd / "artifact").exists() else []
        if nowfiles:
            hit("route.now_verify")
    else:
        hit("route.agenda")
    if meta.get("status") == "clarification_needed":
        hit("route.clarify_stop", q=(meta.get("clarification_question") or "")[:160])
    if (src / "resolved_intent.md").exists():
        hit("route.rewrite")
    if (src / "scope.md").exists():
        hit("route.scope_ok")
    if (build_dir / "scope-raw-FAILED.txt").exists():
        hit("route.scope_fail")

    # ---------- gather events ----------
    att, win = slice_events(rd, loop_ids, started, ended)

    def ev_apply(rows, strength):
        for et, ev, ctx, ts in rows:
            tail = bool(ended and ts > ended)
            if et == "RECALL_PERFORMED":
                hit("plan.recall" if ctx.get("slice") == "loop" else "route.recall", strength)
            elif et == "SCOPE_GENERATED":
                hit("route.scope_ok", strength, fm=ctx.get("failure_modes_count"))
            elif et == "SCOPE_PARSE_FAILED":
                hit("route.scope_fail", strength)
            elif et == "SCOPE_SKIPPED":
                hit("route.scope_skip", strength)
            elif et == "CUTS_DRAWN":
                hit("plan.cuts", strength, n=ctx.get("constraint count") or ctx.get("constraints"))
            elif et == "LOOP_CREATED":
                hit("plan.loop_created", strength, reason=ctx.get("reason"),
                    depth=ctx.get("continuation_depth"), max_steps=ctx.get("max_steps"))
                r = ctx.get("reason")
                if r == "closure_restart":
                    hit("verify.restart", strength)
                elif r == "quality_gate_escalate":
                    hit("gate.escalate_rerun", strength)
            elif et in ("FENCE_EXTENDED", "FENCE_WRITE_BLOCKED"):
                hit("plan.fence", strength)
                if et == "FENCE_WRITE_BLOCKED":
                    hit("exec.write_fence", strength)
            elif et == "BOUNDARY_EXPANDED":
                hit("exec.boundary", strength, sub=len(ctx.get("sub_steps") or []))
            elif et == "STEP_TOO_BROAD":
                hit("exec.too_broad", strength, idx=ctx.get("step_index"),
                    secs=ctx.get("elapsed_s"))
            elif et == "VALIDATION_LADDER":
                hit("exec.validate", strength, tier=ctx.get("tier"),
                    passed=ctx.get("passed"))
            elif et == "SCAVENGE_DETECTED":
                hit("exec.scavenge", strength)
            elif et == "REANCHOR_CHECKED":
                hit("exec.reanchor", strength, on_course=ctx.get("on_course"))
            elif et == "BUDGET_EXTENDED":
                hit("exec.budget_ladder", strength)
            elif et == "METACOGNITIVE_DECISION":
                act = (ctx.get("action") or "").split(":")[0] or "retry"
                hit("exec.blocked", strength)
                hit({
                    "retry": "exec.retry", "redecompose": "exec.redecompose",
                    "split": "exec.split", "stuck": "exec.stuck",
                    "budget_bump": "exec.budget_ladder",
                }.get(act, "exec.retry"), strength, idx=ctx.get("step_idx"))
            elif et in ("NAVIGATOR_DECIDED", "NAVIGATOR_ADJUDICATED"):
                hit("exec.navigator", strength, move=ctx.get("move"),
                    conf=ctx.get("confidence"))
            elif et == "NAVIGATOR_ACTED":
                hit("exec.nav_escalate", strength)
            elif et == "AUTO_RECOVERY":
                hit("fin.auto_recovery", strength, action=ctx.get("action"),
                    risk=ctx.get("risk"))
            elif et == "DIAGNOSIS":
                hit("fin.diagnose", strength, cls=ev.get("subject") or ctx.get("failure_class"),
                    sev=ctx.get("severity"))
            elif et == "WORLD_FACTS_LANDED":
                hit("fin.world_facts", strength)
            elif et == "HYPOTHESIS_CREATED":
                hit("fin.world_facts", strength)
            elif et == "CLOSURE_VERDICT":
                hit("verify.closure", strength, complete=ctx.get("complete"),
                    conf=ctx.get("confidence"), run=ctx.get("checks_run"),
                    passed=ctx.get("checks_passed"), inc=ctx.get("inconclusive_count"),
                    judged=ctx.get("judged"), gaps=ctx.get("gap_count"),
                    skip=ctx.get("skip_reason"))
                if ctx.get("verdict_audit"):
                    hit("verify.audit", strength)
            elif et == "QUALITY_GATE_VERDICT":
                dec = ctx.get("decision") or ctx.get("verdict")
                hit("gate.verdict", strength, decision=dec, conf=ctx.get("confidence"))
                if dec == "ESCALATE" or ctx.get("escalate"):
                    hit("gate.escalate", strength)
                else:
                    hit("gate.pass", strength)
            elif et in ("QUALITY_GATE_SECOND_FAMILY", "QUALITY_GATE_CROSS_REF"):
                hit("gate.crossref", strength)
            elif et == "QUALITY_GATE_OVERRULED":
                hit("gate.overruled", strength)
            elif et in ("CLAIM_PROBED", "CLAIM_VERIFIER_OUTCOME", "FABRICATION_DETECTED"):
                hit("gate.claims", strength)
                if et == "FABRICATION_DETECTED":
                    hit("exec.fabrication", strength)
            elif et == "DONE_WITHOUT_VERDICT":
                hit("close.no_verdict", strength)
            elif et in ("LESSON_RECORDED", "SKILL_PROMOTED", "KNOWLEDGE_NODE_PROMOTED",
                        "SKILL_VARIANT_CREATED", "MEMORY_CONSOLIDATED"):
                # the learning tail routinely fires after ended_at and belongs to
                # whatever ran next -- only count it when the loop_id matches.
                if strength == "a" and not tail:
                    hit("close.learning", strength)

    ev_apply(att, "a")
    ev_apply(win, "w")

    # ---------- loop logs: the step spine ----------
    loops = []
    for lp in logs:
        lj = jload(lp)
        if not lj:
            continue
        lid = lj.get("loop_id") or lp.name[5:-9]
        steps = lj.get("steps") or []
        approx = any(not s.get("ended_ts") for s in steps)
        seen_text = defaultdict(int)
        srows = []
        for s in steps:
            txt = (s.get("text") or "")
            seen_text[txt] += 1
            srows.append({
                "it": s.get("iteration"), "ix": s.get("index"),
                "st": s.get("status"), "t": txt[:150],
                "ms": s.get("elapsed_ms"), "usd": s.get("provider_cost_usd"),
                "ti": s.get("tokens_in"), "to": s.get("tokens_out"),
                "end": s.get("ended_ts") or "",
                "rep": seen_text[txt],          # attempt number for this exact text
                "art": s.get("artifact_check") or "",
            })
        tot = lj.get("totals") or {}
        lm = next((l for l in loops_meta if l.get("loop_id") == lid), {})
        loops.append({
            "id": lid, "status": lj.get("status"), "started": lj.get("started_at"),
            "ms": lj.get("elapsed_ms"), "stuck": (lj.get("stuck_reason") or "")[:400],
            "reason": lm.get("loop_reason"), "parent": lm.get("parent_loop_id"),
            "depth": lm.get("continuation_depth"),
            "approx": approx, "steps": srows, "totals": tot,
        })
        if steps:
            hit("exec.step")
            V["exec.step"]["a"]["steps"] = V["exec.step"]["a"].get("steps", 0) + len(steps)
        if any(s.get("status") == "blocked" for s in steps):
            hit("exec.blocked")
        # retry = same text appearing more than once
        if any(v > 1 for v in seen_text.values()):
            hit("exec.retry")
        # injected steps carry index -1
        if any(s.get("index") == -1 for s in steps):
            hit("exec.inject")
        if any(s.get("executor_session_resumed") for s in steps):
            hit("exec.session_reuse")
        sr = (lj.get("stuck_reason") or "")
        if sr.startswith("NAVIGATOR_ESCALATE"):
            hit("exec.nav_escalate")
        if "budget" in sr.lower() or "slush" in sr.lower():
            hit("exec.budget_break")
        if "MISSING_INPUT" in sr:
            hit("exec.missing_input")
        if "TIMEOUT" in sr.upper():
            hit("exec.timeout")
    if not logs:
        hit("exec.never_ran")

    # ---------- finalize artifacts ----------
    if list(build_dir.glob("loop-*-RESULT.md")):
        hit("fin.result")
    if list(build_dir.glob("loop-*-PARTIAL.md")):
        hit("fin.partial")
    if (build_dir / "checkpoint.json").exists():
        hit("fin.checkpoint")
    if list(build_dir.glob("loop-*-plan.md")):
        hit("plan.manifest")
    if (src / "skills_manifest.jsonl").exists():
        hit("plan.skills")
    calls = sorted(build_dir.glob("calls/call-*.json"))
    purposes = defaultdict(int)
    for c in calls[:400]:
        d = jload(c) or {}
        p = (d.get("purpose") or "").strip()
        if p:
            purposes[p] += 1
    for p in purposes:
        if p.startswith("decompose"):
            hit("plan.decompose", kind=p)
        elif p == "scope":
            hit("route.scope_ok")
        elif p == "cuts":
            hit("plan.cuts")
        elif p == "clarity check":
            hit("route.clarity")
        elif p == "goal rewrite":
            hit("route.rewrite")
        elif p == "routing":
            hit("route.classify")
        elif p.startswith("closure plan"):
            hit("verify.plan")
        elif p.startswith("closure verdict"):
            hit("verify.closure")
            if "audit" in p:
                hit("verify.audit")
        elif p == "quality gate verdict":
            hit("gate.verdict")
        elif p == "adversarial claim review":
            hit("gate.claims")
        elif p.startswith("skill "):
            hit("close.learning")
        elif p in ("lesson extraction", "knowledge extraction"):
            hit("close.learning")
        elif p.startswith("curation"):
            hit("close.curate")
        elif p == "navigator decision":
            hit("exec.navigator")
        elif p == "strategic advisor":
            hit("exec.advisor")
        elif p == "adaptive supervision":
            hit("exec.director")
        elif p == "verify-step":
            hit("exec.ralph")
        elif p == "milestone re-anchor":
            hit("exec.reanchor")
        elif p == "timeout-split":
            hit("exec.split")

    # ---------- closure verdicts file (run-scoped, authoritative) ----------
    cv = jlines(build_dir / "closure_verdicts.jsonl")
    checks = []
    if cv:
        last = cv[-1]
        hit("verify.closure", complete=last.get("complete"), conf=last.get("confidence"),
            run=last.get("checks_run"), passed=last.get("checks_passed"),
            inc=last.get("inconclusive_count"), judged=last.get("judged"),
            gaps=len(last.get("gaps") or []))
        hit("verify.plan")
        for c in (last.get("check_results") or [])[:12]:
            checks.append({"d": (c.get("description") or "")[:120],
                           "o": c.get("outcome"), "x": c.get("exit_code")})
        if last.get("downgrade_reason"):
            hit("verify.downgrade", reason=last.get("downgrade_reason"))

    # ---------- verdict stamp ----------
    gvs = meta.get("goal_verdict_source")
    if gvs:
        hit("verify.stamp", source=gvs, achieved=meta.get("goal_achieved"),
            conf=meta.get("goal_verdict_confidence"))
        if gvs == "provenance":
            hit("verify.provenance")
    if meta.get("goal_verdict_contested"):
        hit("verify.contested", by=meta.get("goal_verdict_contested_by"))
    if meta.get("stop_verdict"):
        hit("fin.stop_verdict", verdict=meta.get("stop_verdict"))
    if meta.get("pause_reason"):
        hit("fin.pause", reason=meta.get("pause_reason"))

    # ---------- close-out ----------
    if card:
        hit("close.curate", stages=len((card.get("_curation") or {}).get("completed") or []))
        sc = card.get("success_class") or "unknown"
        hit("close.terminal", cls=sc)
        hit(f"term.{sc}")
    else:
        hit("close.terminal", cls=meta.get("status") or "unknown")
        hit("term." + (meta.get("status") or "unknown"))
    if meta.get("stranded_detected_at"):
        hit("close.stranded")

    n_steps = sum(len(l["steps"]) for l in loops)
    n_blocked = sum(1 for l in loops for s in l["steps"] if s["st"] == "blocked")
    return {
        "id": hid, "nick": rd.name, "prompt": (meta.get("prompt") or "")[:400],
        "lane": lane, "status": meta.get("status"), "model": meta.get("model"),
        "project": meta.get("project"), "persona": meta.get("persona"),
        "started": started, "ended": ended,
        "achieved": meta.get("goal_achieved"), "vsource": gvs,
        "stop": meta.get("stop_verdict"), "pause": meta.get("pause_reason"),
        "cls": (card.get("success_class") if card else None),
        "usd": (card.get("total_cost_usd") if card else None),
        "usd_log": round(sum((l["totals"] or {}).get("provider_cost_usd") or 0 for l in loops), 4) or None,
        "ncalls": len(calls), "nsteps": n_steps, "nblocked": n_blocked,
        "summary": (meta.get("goal_verdict_summary") or "")[:400],
        "gaps": (meta.get("goal_verdict_gaps") or [])[:6],
        "checks": checks,
        "sha": ((jload(src / "environment.json") or {}).get("maro_git_sha") or "")[:12],
        "loops": loops, "visits": V,
    }


def main():
    out = []
    dirs = sorted(d for d in RUNS.iterdir() if d.is_dir())
    for i, rd in enumerate(dirs):
        try:
            r = build(rd)
        except Exception as e:
            print(f"!! {rd.name}: {e}", file=sys.stderr)
            r = None
        if r:
            out.append(r)
        if i % 100 == 0:
            print(f"  {i}/{len(dirs)}", file=sys.stderr)

    # aggregate node traffic across the whole corpus
    agg = defaultdict(lambda: {"runs": 0, "att": 0})
    for r in out:
        for nid, d in r["visits"].items():
            agg[nid]["runs"] += 1
            if d.get("e") == "a":
                agg[nid]["att"] += 1
    blob = {"generated_from": str(WS), "n_runs": len(out),
            "agg": dict(agg), "runs": out}
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "process_paths.json")
    dest.write_text(json.dumps(blob, separators=(",", ":")))
    print(f"wrote {dest} ({dest.stat().st_size/1e6:.2f} MB, {len(out)} runs)", file=sys.stderr)


if __name__ == "__main__":
    main()
