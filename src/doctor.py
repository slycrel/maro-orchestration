"""poe-doctor — pre-flight environment check.

Verifies that the tools, credentials, and data directories needed for a run
are present and functional. Run before kicking off a mission to catch config
issues early.

Usage:
    poe-doctor
    python3 doctor.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _check(label: str, ok: bool, detail: str = "") -> dict:
    status = "PASS" if ok else "FAIL"
    icon = "✓" if ok else "✗"
    msg = f"  {icon} {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return {"label": label, "ok": ok, "detail": detail}


def run_doctor() -> bool:
    """Run all checks. Returns True if all pass."""
    print("poe-doctor — environment check\n")
    results = []

    # Python version
    major, minor = sys.version_info[:2]
    results.append(_check(
        "Python version",
        major == 3 and minor >= 10,
        f"{major}.{minor} (need 3.10+)",
    ))

    # Key dependencies
    for dep in ("requests", "anthropic"):
        try:
            __import__(dep)
            results.append(_check(f"Package: {dep}", True))
        except ImportError:
            results.append(_check(f"Package: {dep}", False, "pip install " + dep))

    # Config file
    cfg_path = Path.home() / ".openclaw" / "openclaw.json"
    cfg = None
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            results.append(_check("openclaw.json", True, str(cfg_path)))
        except Exception as exc:
            results.append(_check("openclaw.json", False, f"parse error: {exc}"))
    else:
        results.append(_check("openclaw.json", False, f"not found at {cfg_path}"))

    # Telegram bot token
    tg_token = ""
    if cfg:
        tg_token = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
    tg_token = tg_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    results.append(_check(
        "Telegram bot token",
        bool(tg_token),
        f"token length={len(tg_token)}" if tg_token else "missing (set in openclaw.json or TELEGRAM_BOT_TOKEN)",
    ))

    # Telegram chat ID
    tg_chat = ""
    if cfg:
        tg_chat = str(cfg.get("channels", {}).get("telegram", {}).get("chatId", ""))
    tg_chat = tg_chat or os.environ.get("TELEGRAM_CHAT_ID", "")
    results.append(_check(
        "Telegram chat ID",
        bool(tg_chat),
        f"chat_id={tg_chat}" if tg_chat else "missing (set in openclaw.json or TELEGRAM_CHAT_ID)",
    ))

    # LLM connectivity (quick API probe)
    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        from llm import build_adapter, LLMMessage
        adapter = build_adapter()
        resp = adapter.complete(
            [LLMMessage("user", "Reply with exactly: ok")],
            max_tokens=8,
            temperature=0.0,
        )
        ok = "ok" in resp.content.lower()
        results.append(_check("LLM API reachable", ok, resp.content.strip()[:40]))
    except Exception as exc:
        results.append(_check("LLM API reachable", False, str(exc)[:80]))

    # Memory directory
    mem_dir = Path(__file__).resolve().parent.parent / "memory"
    results.append(_check(
        "Memory directory",
        mem_dir.exists(),
        str(mem_dir),
    ))

    # Skills file (runtime JSONL)
    skills_path = Path(__file__).resolve().parent.parent / "skills.jsonl"
    if not skills_path.exists():
        skills_path = Path(__file__).resolve().parent.parent / "memory" / "skills.jsonl"
    results.append(_check(
        "Skills data",
        skills_path.exists(),
        f"{skills_path} ({'exists' if skills_path.exists() else 'will be created on first run'})",
    ))

    # Phase 62: Check workspace skills for duplicates (same content_hash)
    try:
        workspace_skills = Path.home() / ".poe" / "workspace" / "memory" / "skills.jsonl"
        if workspace_skills.exists():
            from collections import defaultdict
            all_skills = []
            for line in workspace_skills.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        all_skills.append(json.loads(line))
                    except Exception:
                        pass
            if all_skills:
                by_hash = defaultdict(list)
                for skill in all_skills:
                    hash_val = skill.get("content_hash", "")
                    if hash_val:
                        by_hash[hash_val].append(skill)
                duplicates = sum(1 for h, skills in by_hash.items() if len(skills) > 1)
                if duplicates > 0:
                    results.append(_check(
                        "Workspace skills (duplicates)",
                        False,
                        f"{duplicates} hash group(s) with duplicates — run: python3 -c \"from doctor import cleanup_workspace_skills; cleanup_workspace_skills()\"",
                    ))
                else:
                    results.append(_check("Workspace skills (duplicates)", True, "clean"))
            else:
                results.append(_check("Workspace skills (duplicates)", True, "no skills yet"))
        else:
            results.append(_check("Workspace skills (duplicates)", True, "workspace not initialized"))
    except Exception as exc:
        results.append(_check("Workspace skills (duplicates)", True, f"skipped: {exc}"))

    # Output directory
    output_dir = Path(__file__).resolve().parent.parent / "output"
    results.append(_check(
        "Output directory",
        output_dir.exists(),
        str(output_dir),
    ))

    # Phase 41: tool registry
    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        from tool_registry import registry as _reg
        _names = _reg.names()
        _required_tools = {"complete_step", "flag_stuck"}
        _missing = _required_tools - set(_names)
        results.append(_check(
            "Tool registry",
            not _missing,
            f"{len(_names)} tool(s) registered" if not _missing else f"missing: {', '.join(_missing)}",
        ))
    except Exception as exc:
        results.append(_check("Tool registry", False, str(exc)[:80]))

    # Phase 41: curated skills (SKILL.md files)
    try:
        from skill_loader import SkillLoader, SKILLS_DIR
        _skills_dir_ok = SKILLS_DIR.exists()
        if _skills_dir_ok:
            _loader = SkillLoader()
            _curated = _loader.load_summaries()
            results.append(_check(
                "Curated skills (skills/)",
                True,
                f"{len(_curated)} SKILL.md file(s) loaded",
            ))
        else:
            results.append(_check(
                "Curated skills (skills/)",
                False,
                "skills/ directory missing — run from repo root or create it",
            ))
    except Exception as exc:
        results.append(_check("Curated skills (skills/)", False, str(exc)[:80]))

    # Phase 41: step event bus
    try:
        from step_events import step_event_bus
        _handlers = step_event_bus.list_handlers()
        _pre_count = len(_handlers["pre"])
        _post_count = len(_handlers["post"])
        results.append(_check(
            "Step event bus",
            True,
            f"loaded — {_pre_count} pre-step handler(s), {_post_count} post-step handler(s)",
        ))
    except Exception as exc:
        results.append(_check("Step event bus", False, str(exc)[:80]))

    # Bughunter scan (quick check)
    try:
        from bughunter import run_bughunter
        _bh_report = run_bughunter()
        _bh_count = len(_bh_report.findings)
        results.append(_check(
            "Bughunter (src/)",
            _bh_count == 0,
            "clean" if _bh_count == 0 else f"{_bh_count} issue(s) — run poe-bughunter for details",
        ))
    except Exception as exc:
        results.append(_check("Bughunter (src/)", True, f"skipped: {exc}"))  # optional, not fatal

    # Continuation traversal config
    _max_depth = os.environ.get("POE_MAX_CONTINUATION_DEPTH", "")
    results.append(_check(
        "POE_MAX_CONTINUATION_DEPTH",
        True,  # optional — default of 4 is fine, warn only when unset for awareness
        f"={_max_depth}" if _max_depth else "not set (default: 4 passes before escalation)",
    ))

    _step_timeout = os.environ.get("POE_STEP_TIMEOUT", "")
    results.append(_check(
        "POE_STEP_TIMEOUT",
        True,  # optional
        f"={_step_timeout}s" if _step_timeout else "not set (default: 600s per step)",
    ))

    # Task store queue — check for stuck continuation/escalation tasks
    try:
        from task_store import list_tasks as _list_tasks
        _queued = _list_tasks(status_filter="queued")
        _continuations = [t for t in _queued if t.get("source") == "loop_continuation"]
        _escalations = [t for t in _queued if t.get("source") == "loop_escalation"]
        _task_detail = (
            f"{len(_continuations)} continuation(s), {len(_escalations)} escalation(s) queued"
            if (_continuations or _escalations)
            else f"{len(_queued)} task(s) queued — no stuck continuations"
        )
        results.append(_check(
            "Task store queue",
            len(_escalations) == 0,  # escalations waiting = needs attention
            _task_detail,
        ))
    except Exception as exc:
        results.append(_check("Task store queue", True, f"skipped: {exc}"))  # optional

    # SlowUpdateScheduler — verify import and snapshot API
    try:
        from slow_update_scheduler import SlowUpdateScheduler
        _sched = SlowUpdateScheduler(idle_cooldown=30.0)
        _snap = _sched.status()
        _state = _snap.get("state", "unknown")
        results.append(_check(
            "SlowUpdateScheduler",
            True,
            f"state={_state}, cooldown={_snap.get('idle_cooldown')}s, workers={_snap.get('active_workers', 0)}",
        ))
    except Exception as exc:
        results.append(_check("SlowUpdateScheduler", False, str(exc)[:80]))

    # channels (GitHub / Reddit / YouTube)
    try:
        from channels import channels_health_check
        _ch = channels_health_check()
        _ch_ok = _ch.get("any_available", False)
        _ch_detail = ", ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in _ch.get("channels", {}).items()
        )
        results.append(_check("channels (GitHub/Reddit/YouTube)", _ch_ok, _ch_detail))
    except Exception as _exc:
        results.append(_check("channels", False, str(_exc)[:80]))

    # polymarket-cli availability
    try:
        from polymarket import polymarket_health_check
        _pm = polymarket_health_check()
        results.append(_check(
            "polymarket-cli",
            _pm["available"],
            f"{len(_pm['functions'])} functions available" if _pm["available"]
            else "not found — pip install polymarket-cli",
        ))
    except Exception as _exc:
        results.append(_check("polymarket-cli", False, str(_exc)[:80]))

    # Summary
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print(f"\n{passed}/{total} checks passed")

    if passed < total:
        failed = [r["label"] for r in results if not r["ok"]]
        print(f"Failed: {', '.join(failed)}")
        return False

    print("All checks passed — ready to run.")
    return True


def cleanup_workspace_skills() -> None:
    """Remove duplicate skills (same content_hash) from workspace skills.jsonl.

    Keeps the best copy based on creation date and success metrics.
    """
    from collections import defaultdict
    workspace_skills = Path.home() / ".poe" / "workspace" / "memory" / "skills.jsonl"

    if not workspace_skills.exists():
        print("Workspace skills file not found — nothing to clean")
        return

    # Load all skills
    all_skills = []
    for line in workspace_skills.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                all_skills.append(json.loads(line))
            except Exception as e:
                print(f"Skipped unparseable line: {e}")

    print(f"Loaded {len(all_skills)} skills")

    # Group by content_hash
    by_hash = defaultdict(list)
    for skill in all_skills:
        hash_val = skill.get("content_hash", "")
        if hash_val:
            by_hash[hash_val].append(skill)

    # Find duplicates
    duplicates = {h: skills for h, skills in by_hash.items() if len(skills) > 1}
    if not duplicates:
        print("No duplicates found")
        return

    print(f"Found {len(duplicates)} hash group(s) with duplicates:")

    # Scoring: prefer recent + high success rate + high use count
    def score_skill(skill):
        created_at = skill.get("created_at", "")
        success_rate = float(skill.get("success_rate", 0))
        use_count = int(skill.get("use_count", 0))
        return (created_at, success_rate, use_count)

    total_removed = 0
    for hash_val, skills in duplicates.items():
        best = max(skills, key=score_skill)
        removed = len(skills) - 1
        total_removed += removed
        print(f"  {hash_val[:16]}... : keeping best of {len(skills)} copies of '{best.get('name', '?')}'")

    # Rewrite with deduped set
    kept = [max(skills, key=score_skill) for skills in by_hash.values()]
    output_lines = [json.dumps(skill) for skill in kept]
    workspace_skills.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    print(f"Cleaned: {len(kept)} skills remain ({total_removed} duplicates removed)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Poe environment health check")
    parser.add_argument("--json", action="store_true", help="JSON output (not yet implemented, use text)")
    parser.add_argument("--cleanup-skills", action="store_true", help="Remove duplicate skills from workspace")
    args = parser.parse_args()

    if args.cleanup_skills:
        cleanup_workspace_skills()
    else:
        ok = run_doctor()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
