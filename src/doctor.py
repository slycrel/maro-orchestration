"""maro-doctor — pre-flight environment check.

Verifies that the tools, credentials, and data directories needed for a run
are present and functional. Run before kicking off a mission to catch config
issues early.

Usage:
    maro-doctor
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


def _scan_config_paths(cfg: dict, *, _prefix: str = "") -> list:
    """Find path-shaped string config values that don't exist on this box.

    Heuristic: a value is path-shaped if it's a string starting with '/' or
    '~' and contains no whitespace — rules out shell commands with args
    (e.g. notify.command: "/usr/bin/curl -X POST ..."). Conservative on
    purpose: a false negative (a real broken path we miss) is far cheaper
    than a false positive (flagging a legitimate command as broken).
    """
    missing: list = []
    for key, value in cfg.items():
        dotted = f"{_prefix}.{key}" if _prefix else key
        if isinstance(value, dict):
            missing.extend(_scan_config_paths(value, _prefix=dotted))
        elif isinstance(value, str) and (value.startswith("/") or value.startswith("~")):
            if any(c.isspace() for c in value):
                continue
            if not Path(value).expanduser().exists():
                missing.append(f"{dotted}={value}")
    return missing


def run_doctor() -> bool:
    """Run all checks. Returns True if all pass."""
    print("maro-doctor — environment check\n")
    results = []

    # Python version
    major, minor = sys.version_info[:2]
    results.append(_check(
        "Python version",
        major == 3 and minor >= 10,
        f"{major}.{minor} (need 3.10+)",
    ))

    src_dir = Path(__file__).resolve().parent
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Config files — Maro's own two-tier config is the canonical source.
    try:
        from config import config_paths as _config_paths
        _paths = _config_paths()
        user_cfg = Path(_paths["user"])
        ws_cfg = Path(_paths["workspace"])
    except Exception:
        user_cfg = Path.home() / ".maro" / "config.yml"
        ws_cfg = Path.home() / ".maro" / "workspace" / "config.yml"
    _cfg_found = [str(p) for p in (user_cfg, ws_cfg) if p.exists()]
    results.append(_check(
        "Config (~/.maro)",
        bool(_cfg_found),
        ", ".join(_cfg_found) if _cfg_found
        else f"none found — run `maro-bootstrap install` (creates {user_cfg})",
    ))

    # config.yml is parsed with pyyaml; without it settings are SILENTLY
    # ignored. Unconditional (mandatory dep since 2026-07-09): a broken
    # install missing pyyaml is exactly when doctor must be loudest.
    try:
        import yaml  # noqa: F401
        results.append(_check("Config parseable (pyyaml)", True))
    except ImportError:
        results.append(_check(
            "Config parseable (pyyaml)",
            False,
            "pyyaml is not installed — every config.yml setting is "
            "silently ignored; pip install pyyaml",
        ))

    # Legacy OpenClaw config — optional fallback for telegram/gateway wiring.
    # Only reported when present; its absence is normal on a fresh install.
    _oc_path = Path.home() / ".openclaw" / "openclaw.json"
    if _oc_path.exists():
        try:
            json.loads(_oc_path.read_text())
            results.append(_check("Legacy openclaw.json", True, f"{_oc_path} (fallback only)"))
        except Exception as exc:
            results.append(_check("Legacy openclaw.json", False, f"parse error: {exc}"))

    # LLM backends — llm.detect_backends() is the single source of truth: it
    # walks the same configured order and availability predicates
    # build_adapter uses (keys from env OR credentials .env, CLAUDE_BIN,
    # codex auth), so doctor can't disagree with what a run would do.
    _usable: list[str] = []
    _degraded: list[str] = []
    try:
        from llm import detect_backends as _detect_backends
        _pkg_needs = {"anthropic": "anthropic", "openrouter": "requests", "openai": "requests"}
        for _name, _avail, _ in _detect_backends():
            if not _avail:
                continue
            _pkg = _pkg_needs.get(_name)
            if _pkg:
                try:
                    __import__(_pkg)
                except ImportError:
                    _degraded.append(f"{_name} key set but {_pkg} missing (pip install {_pkg})")
                    continue
            _usable.append("subprocess (claude CLI)" if _name == "subprocess" else _name)
        _backend_detail = ", ".join(_usable + _degraded) if (_usable or _degraded) else (
            "none — set ANTHROPIC_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY "
            "(env or credentials .env), or install the claude CLI"
        )
        results.append(_check("LLM backend available", bool(_usable), _backend_detail))
    except Exception as exc:
        results.append(_check("LLM backend available", False, f"detection failed: {str(exc)[:60]}"))

    # Escalation surface — how escalations reach a human. Two independent
    # surfaces (2026-07-12 decree, GOAL_BRAIN Decisions "escalation channel
    # DECREED"): (1) the durable file (output/escalations.jsonl) ships
    # unconditionally and is always live — doctor just reports it exists and
    # is writable; (2) an optional push lane (notify.command / Telegram) for
    # substrates that want to be told rather than poll. Neither is fatal —
    # the CLI lane works without either — but an unattended install with
    # NO push lane means nobody finds out about an escalation until they
    # think to look at the file.
    try:
        import os as _os
        from notify import escalations_path as _esc_path
        _ep = _esc_path()
        _ep.parent.mkdir(parents=True, exist_ok=True)
        # os.access, not an actual write — a real append goes through
        # file_lock.locked_append (fail-closed on lock contention), which a
        # healthcheck shouldn't attempt itself: it would either contend with
        # a real writer or pollute the escalation log with synthetic rows.
        # This still catches the concrete failure this check exists to
        # catch (read-only fs, permission-denied output/) even though it
        # can't prove a future locked append will succeed (adversarial
        # review 2026-07-12: the prior version of this check only proved
        # the parent dir *exists*, not that it's writable).
        _writable = _os.access(_ep.parent, _os.W_OK) and (
            not _ep.is_file() or _os.access(_ep, _os.W_OK)
        )
        _esc_rows = 0
        if _ep.is_file():
            _esc_rows = sum(1 for l in _ep.read_text(encoding="utf-8").splitlines() if l.strip())
        results.append(_check(
            "Escalation file surface", _writable,
            f"{_ep} ({_esc_rows} row(s) — always on, independent of any push lane)"
            if _writable else f"{_ep} not writable — escalation-class events will silently fail to log",
        ))
    except Exception as exc:
        results.append(_check("Escalation file surface", False, str(exc)[:80]))

    _notify_cmd = ""
    _notify_err = ""
    try:
        from config import get as _cfg_get
        _notify_cmd = str(_cfg_get("notify.command", "") or "")
    except Exception as exc:
        _notify_err = f"config read failed: {str(exc)[:50]}"
    _tg_ok = False
    if not _notify_err:
        try:
            from telegram_listener import is_configured as _tg_configured
            _tg_ok = _tg_configured()
        except Exception as exc:
            _notify_err = f"telegram probe failed: {str(exc)[:50]}"
    if _tg_ok:
        try:
            __import__("requests")
        except ImportError:
            _notify_err = "Telegram configured but requests missing (pip install requests)"
    if _notify_err:
        results.append(_check("Escalation push lane", False, _notify_err))
    elif _notify_cmd:
        results.append(_check("Escalation push lane", True, f"notify.command = {_notify_cmd[:60]}"))
    elif _tg_ok:
        results.append(_check("Escalation push lane", True, "Telegram configured (listener/notify lane)"))
    else:
        results.append(_check(
            "Escalation push lane", True,
            "NONE configured — escalations only land in the file surface "
            "above / events.jsonl; set notify.command for unattended use",
        ))

    # LLM connectivity (quick API probe)
    try:
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

    # Containerized executor — worker steps carrying real tools optionally run
    # inside a docker container for filesystem/network isolation
    # (docs/CONTAINER_EXECUTOR_DESIGN.md). OFF by default: one info row, probe
    # nothing (docker is never a hard requirement). When on/require the operator
    # opted in, so surface exactly which mode a run would get — SF-6's lesson:
    # the difference between "sandboxed" and "not" must be loud. The
    # token-spending login probe rides `maro-doctor --live`, not this sweep.
    try:
        from container_exec import (
            container_mode, container_mode_raw, container_image,
            docker_probe, image_probe, auth_volume_probe,
        )
        _cmode = container_mode()
        if _cmode == "off":
            _raw = container_mode_raw()
            if _raw.lower() in ("off", "false", ""):
                _off_detail = "executor.container=off — worker steps run on host under the write-fence"
            else:
                _off_detail = (f"executor.container={_raw!r} unrecognized — treated as off "
                               "(host/fence-only); valid: off / on / require")
            results.append(_check("Container executor", True, _off_detail))
        else:
            _dock_ok, _dock_detail = docker_probe()
            _degrade = (
                "executor calls will REFUSE without docker (executor.container=require)"
                if _cmode == "require"
                else "executor calls DEGRADE to host/fence-only without docker (executor.container=on)"
            )
            results.append(_check(
                f"Container executor ({_cmode})",
                _dock_ok,
                _dock_detail if _dock_ok else f"{_dock_detail} — {_degrade}",
            ))
            if _dock_ok:
                _img_ok, _img_detail = image_probe(container_image())
                results.append(_check("  Container image", _img_ok, _img_detail))
                _vol_ok, _vol_detail = auth_volume_probe()
                results.append(_check("  Container auth volume", _vol_ok, _vol_detail))
    except Exception as exc:
        results.append(_check("Container executor", False, str(exc)[:80]))

    # Memory directory — use the canonical resolution (env > config > orch
    # fallback), not a repo-relative guess. The repo-local memory/ is a stale
    # copy (tests write there); reporting it here misled diagnostics on any
    # box where the real data lives in ~/.maro/workspace/memory.
    try:
        from orch_items import memory_dir as _canonical_memory_dir
        mem_dir = _canonical_memory_dir()
    except Exception:
        mem_dir = Path(__file__).resolve().parent.parent / "memory"
    results.append(_check(
        "Memory directory",
        mem_dir.exists(),
        str(mem_dir),
    ))

    # Skills file (runtime JSONL)
    skills_path = mem_dir / "skills.jsonl"
    results.append(_check(
        "Skills data",
        skills_path.exists(),
        f"{skills_path} ({'exists' if skills_path.exists() else 'will be created on first run'})",
    ))

    # Phase 62: Check workspace skills for duplicates (same content_hash)
    try:
        workspace_skills = Path.home() / ".maro" / "workspace" / "memory" / "skills.jsonl"
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

    # Output directory (workspace, not repo-relative). Deliberately NOT via
    # config.output_dir() — that helper mkdirs as a side effect, which would
    # make this check a vacuous pass and doctor a filesystem mutator.
    try:
        from config import workspace_root as _workspace_root
        output_dir = _workspace_root() / "output"
    except Exception:
        output_dir = Path(__file__).resolve().parent.parent / "output"
    results.append(_check(
        "Output directory",
        output_dir.exists(),
        f"{output_dir} ({'exists' if output_dir.exists() else 'missing — run maro-bootstrap install'})",
    ))

    # Phase 41: tool registry
    try:
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

    # Local validator (optional, zero-cost first-pass validation)
    try:
        import local_models as _lm
        _models = _lm.configured_models()
        if not _models:
            results.append(_check("Local validator", True, "not configured — paid validation (default)"))
        else:
            _rt = _lm.resolve_runtime()
            _ep = _lm.resolve_endpoint(_rt)
            _loaded = set(_lm.loaded_models(_ep))
            _usable = [m for m in _models if m in _loaded]
            if _usable:
                results.append(_check("Local validator", True,
                                      f"{_rt} @ {_ep} — active: {', '.join(_usable)}"))
            else:
                results.append(_check("Local validator", False,
                                      f"{_rt} @ {_ep} unreachable or {_models} not loaded — "
                                      f"run scripts/local-validator.sh start"))
    except Exception as exc:
        results.append(_check("Local validator", True, f"skipped: {exc}"))  # optional, not fatal

    # Bughunter scan (quick check)
    try:
        from bughunter import run_bughunter
        _bh_report = run_bughunter()
        _bh_count = len(_bh_report.findings)
        results.append(_check(
            "Bughunter (src/)",
            _bh_count == 0,
            "clean" if _bh_count == 0 else f"{_bh_count} issue(s) — run maro-bughunter for details",
        ))
    except Exception as exc:
        results.append(_check("Bughunter (src/)", True, f"skipped: {exc}"))  # optional, not fatal

    # Continuation traversal config — default derives from the shared
    # restart-depth ceiling (loop_types.MAX_RESTART_DEPTH); see
    # tests/test_depth_cap_unified.py.
    from loop_types import MAX_RESTART_DEPTH as _restart_depth_default
    _max_depth = os.environ.get("MARO_MAX_CONTINUATION_DEPTH", "")
    results.append(_check(
        "MARO_MAX_CONTINUATION_DEPTH",
        True,  # optional — the shared default is fine, warn only when unset for awareness
        f"={_max_depth}" if _max_depth
        else f"not set (default: {_restart_depth_default} passes before escalation)",
    ))

    _step_timeout = os.environ.get("MARO_STEP_TIMEOUT", "")
    results.append(_check(
        "MARO_STEP_TIMEOUT",
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

    # channels (GitHub / Reddit / YouTube) — optional integrations, never fatal
    try:
        from channels import channels_health_check
        _ch = channels_health_check()
        _ch_detail = ", ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in _ch.get("channels", {}).items()
        )
        if not _ch.get("any_available", False):
            _ch_detail = (_ch_detail + " — optional, none configured").lstrip(" —")
        results.append(_check("channels (GitHub/Reddit/YouTube)", True, _ch_detail))
    except Exception as _exc:
        # "none configured" above is soft; the health check CRASHING is not.
        results.append(_check("channels (GitHub/Reddit/YouTube)", False, str(_exc)[:80]))

    # Post-migration checks (docs/MIGRATION.md, PORTABLE_LEARNING_DESIGN.md §5a)
    # — a restored workspace on a new box needs these three answered before
    # anything is re-armed. Cheap enough to run unconditionally rather than
    # gate behind a flag; on a healthy running box they're informational.
    try:
        from config import load_config as _load_cfg
        _missing_paths = _scan_config_paths(_load_cfg())
        results.append(_check(
            "Config paths on this box",
            not _missing_paths,
            "all path-shaped config values resolve" if not _missing_paths
            else f"{len(_missing_paths)} value(s) don't exist here (stale from another "
                 f"machine?): {', '.join(_missing_paths[:5])}"
                 + (f" (+{len(_missing_paths) - 5} more)" if len(_missing_paths) > 5 else ""),
        ))
    except Exception as exc:
        results.append(_check("Config paths on this box", True, f"skipped: {exc}"))

    # Machine state that shouldn't survive a copy to a new box unexamined —
    # never a hard FAIL (a live running box legitimately has all of these;
    # see the supervision-convergence fix for why structural-noise FAILs on
    # normal-operation state are a standing anti-pattern here). Informational
    # so a human following docs/MIGRATION.md knows what to delete.
    try:
        from config import workspace_root as _ws_root, memory_dir as _mem_dir
        _root = _ws_root()
        _mem = _mem_dir()
        _stale_candidates = [_mem / "jobs.json", _mem / "heartbeat-state.json",
                              _root / "telegram_offset.txt"]
        _present = [str(p) for p in _stale_candidates if p.exists()]
        _locks = sorted(str(p) for p in _root.rglob("*.lock"))
        _present.extend(_locks)
        results.append(_check(
            "Stale machine state",
            True,
            "none present" if not _present
            else f"{len(_present)} file(s) present — if this workspace was just "
                 f"restored from another machine, delete these before re-arming "
                 f"any schedule/heartbeat (see docs/MIGRATION.md): "
                 + ", ".join(_present[:4])
                 + (f" (+{len(_present) - 4} more)" if len(_present) > 4 else ""),
        ))
    except Exception as exc:
        results.append(_check("Stale machine state", True, f"skipped: {exc}"))

    # Memory index self-heal confirmation — opening the store is what
    # triggers catch-up/rebuild (memory_sqlite.py), so this check both
    # reports AND performs the designed self-heal; that's intentional, not
    # a side effect to avoid (docs/PORTABLE_LEARNING_DESIGN.md §5a).
    try:
        from memory_sqlite import SqliteMemoryStore
        from config import memory_dir as _mem_dir2
        _store_root = _mem_dir2() / "module"
        _rebuilt_before = not (_store_root / "index.db").exists()
        _store = SqliteMemoryStore(_store_root)
        _log_size = _store.log_path.stat().st_size if _store.log_path.exists() else 0
        _offset = int(_store._meta("log_offset") or 0)
        _store._db.close()
        _in_sync = _offset == _log_size
        results.append(_check(
            "Memory index sync",
            True,
            "fresh index built from event log" if _rebuilt_before
            else ("in sync with event log" if _in_sync
                  else f"caught up this run (offset {_offset} -> {_log_size})"),
        ))
    except Exception as exc:
        results.append(_check("Memory index sync", True, f"skipped: {exc}"))

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


def _skill_hash_is_stale(skill_dict: dict) -> bool:
    """Return True if the stored content_hash doesn't match the skill's actual content."""
    stored = skill_dict.get("content_hash", "")
    if not stored:
        return False  # no hash stored — not stale, just unset
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _src = _Path(__file__).parent
        if str(_src) not in _sys.path:
            _sys.path.insert(0, str(_src))
        from skill_types import dict_to_skill, compute_skill_hash
        skill_obj = dict_to_skill(skill_dict)
        return compute_skill_hash(skill_obj) != stored
    except Exception:
        return False  # can't verify → keep


def cleanup_workspace_skills(skills_path: "Path | None" = None) -> None:
    """Remove duplicate and stale-hash skills from workspace skills.jsonl.

    Stale-hash skills: stored content_hash doesn't match the skill's actual content.
    These are typically test fixtures that leaked into the workspace.

    Duplicates: multiple skills with the same content_hash. Keeps the best copy
    based on creation date and success metrics.

    Args:
        skills_path: Override the default workspace path (for testing).
    """
    from collections import defaultdict
    workspace_skills = skills_path or (Path.home() / ".maro" / "workspace" / "memory" / "skills.jsonl")

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

    # Pass 1: remove stale-hash skills (test fixtures that leaked in)
    stale = [s for s in all_skills if _skill_hash_is_stale(s)]
    if stale:
        print(f"Found {len(stale)} skill(s) with stale content_hash (test fixtures):")
        for s in stale:
            print(f"  {s.get('id', '?'):12} '{s.get('name', '?')}' — stored hash doesn't match content")
    else:
        print("No stale-hash skills found")
    stale_ids = {s.get("id") for s in stale}
    clean = [s for s in all_skills if s.get("id") not in stale_ids]

    # Pass 2: deduplicate by content_hash
    by_hash: dict = defaultdict(list)
    for skill in clean:
        hash_val = skill.get("content_hash", "")
        if hash_val:
            by_hash[hash_val].append(skill)
        else:
            # No hash — keep as-is (can't dedup without a key)
            by_hash[skill.get("id", id(skill))].append(skill)

    duplicates = {h: skills for h, skills in by_hash.items() if len(skills) > 1}
    if duplicates:
        print(f"Found {len(duplicates)} hash group(s) with duplicates:")
    else:
        print("No duplicates found")

    # Scoring: prefer recent + high success rate + high use count
    def score_skill(skill):
        created_at = skill.get("created_at", "")
        success_rate = float(skill.get("success_rate", 0))
        use_count = int(skill.get("use_count", 0))
        return (created_at, success_rate, use_count)

    total_dup_removed = 0
    for hash_val, skills in duplicates.items():
        best = max(skills, key=score_skill)
        removed = len(skills) - 1
        total_dup_removed += removed
        print(f"  {hash_val[:16]}... : keeping best of {len(skills)} copies of '{best.get('name', '?')}'")

    # Rewrite with clean, deduped set
    kept = [max(skills, key=score_skill) for skills in by_hash.values()]
    output_lines = [json.dumps(skill) for skill in kept]
    workspace_skills.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    total_removed = len(stale) + total_dup_removed
    print(
        f"Cleaned: {len(kept)} skills remain "
        f"({len(stale)} stale-hash + {total_dup_removed} duplicate(s) removed, "
        f"{total_removed} total)"
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Maro environment health check")
    parser.add_argument("--json", action="store_true", help="JSON output (not yet implemented, use text)")
    parser.add_argument("--cleanup-skills", action="store_true", help="Remove duplicate skills from workspace")
    parser.add_argument("--cleanup-lessons", action="store_true", help="Deduplicate lessons from workspace")
    parser.add_argument("--dry-run", action="store_true", help="Show what cleanup would do without writing")
    parser.add_argument("--live", action="store_true",
                        help="Probe each backend with a real 1-call completion "
                             "(catches 'installed but not logged in'; spends a "
                             "few tokens per backend)")
    args = parser.parse_args()

    if args.live:
        print("maro-doctor — live backend probe (spends a few tokens)\n")
        from llm import probe_backends
        all_ok, any_ok = True, False
        for name, ok, detail in probe_backends():
            _check(f"backend:{name}", ok, detail)
            all_ok = all_ok and ok
            any_ok = any_ok or ok
        # Container login — the real "installed but not logged in" catch,
        # launched through the container (spends a token). Only when containers
        # are configured on/require and the image is built; informational, so
        # it doesn't gate the live-probe exit code (that tracks the backends).
        try:
            from container_exec import container_mode, docker_probe, image_probe, login_probe
            if container_mode() in ("on", "require"):
                _d_ok, _ = docker_probe()
                _i_ok, _ = image_probe() if _d_ok else (False, "")
                if _d_ok and _i_ok:
                    _l_ok, _l_detail = login_probe()
                    _check("container:login", _l_ok, _l_detail)
        except Exception as exc:
            _check("container:login", False, str(exc)[:80])
        sys.exit(0 if any_ok else 1)
    elif args.cleanup_skills:
        cleanup_workspace_skills()
    elif args.cleanup_lessons:
        from memory_ledger import deduplicate_lessons
        stats = deduplicate_lessons(dry_run=args.dry_run)
        label = "[DRY RUN] " if args.dry_run else ""
        print(f"{label}lessons dedup: {stats['before']} → {stats['after']} "
              f"(-{stats['removed_exact']} exact, -{stats['removed_near']} near-dup)")
    else:
        ok = run_doctor()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
