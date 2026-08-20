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
from datetime import datetime, timezone
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
        _pkg_needs = {"anthropic": "anthropic", "openrouter": "requests", "openai": "requests", "xai": "requests"}
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
            no_tools=True,
            purpose="llm-probe",
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
            auth_breaker_state,
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
            # Reactive auth breaker (container_exec) — tripped means the
            # volume's OAuth session died mid-lane. File read only, so it
            # renders with docker DOWN too (review 2026-08-13: a tripped lane
            # must not hide behind a down daemon — the re-seed action is the
            # same either way).
            _ab, _ab_status = auth_breaker_state()
            if _ab_status == "unreadable":
                results.append(_check(
                    "  Container auth breaker", False,
                    "marker UNREADABLE — lane state unknown; inspect "
                    "memory/container_auth_breaker.json (resolve fails open)"))
            else:
                results.append(_check(
                    "  Container auth breaker",
                    _ab is None,
                    "clear" if _ab is None else (
                        f"TRIPPED — {str(_ab.get('reason', ''))[:80]}; re-seed the "
                        "auth volume (maro-bootstrap container-setup step 2)"),
                ))
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
        workspace_skills = _workspace_skills_path()
        if workspace_skills.exists():
            from collections import defaultdict
            from jsonl_utils import loads_clean as _lc, store_text as _st
            all_skills = []
            unreadable = 0
            for line in _st(workspace_skills).splitlines():
                line = line.strip()
                if line:
                    try:
                        all_skills.append(_lc(line))
                    except Exception:
                        unreadable += 1
            if unreadable:
                results.append(_check(
                    "Workspace skills (readable)", False,
                    f"{unreadable} unparseable/byte-tainted row(s) in "
                    f"{workspace_skills} — not counted below",
                ))
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

    # Hosted-free validator tier (optional, zero-cost first-pass validation;
    # the local-model tier was removed 2026-07-21 by decree)
    try:
        import hosted_free as _hf
        _providers = _hf.configured_providers()
        if not _providers:
            results.append(_check("Hosted-free validator", True,
                                  "not configured — paid validation (default)"))
        elif _hf.available():
            results.append(_check("Hosted-free validator", True,
                                  f"providers: {', '.join(_providers)}"))
        else:
            results.append(_check("Hosted-free validator", False,
                                  f"configured ({', '.join(_providers)}) but all "
                                  f"breakers tripped — will fall through to paid"))
    except Exception as exc:
        results.append(_check("Hosted-free validator", True, f"skipped: {exc}"))  # optional, not fatal

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


# The three fields two rows MUST disagree on to be two rows at all: their
# identity, the hash derived from their content, and the timestamp this verb
# breaks ties by. Everything else — every field, including ones added after
# this line was written — has to match before one row may be deleted as a
# duplicate of another.
#
# r4 named a dozen fields "bookkeeping" and adversarial r5 refuted the list
# 5/5, with `circuit_state` as the proof: an "open" circuit EXCLUDES a skill
# from matching (skills.py, find_matching_skills), so a row differing only
# there is not an identical copy in any sense a user would accept — probed
# both directions, a forged open row evicting a healthy closed one, and a
# forged closed row resurrecting a circuit-broken skill. `failure_notes` and
# `source_loop_ids` are the other half of the same mistake: they are
# EVIDENCE, and deleting the row that carries them destroys it.
#
# The general lesson, which is why this is now three names and not twelve: an
# exclusion list over an open field space is a denylist, and a denylist
# guarding a DESTRUCTIVE decision fails open on every field nobody thought
# about — including the ones a future commit adds. Measured before shipping:
# on the 423-row live store this removes exactly as many duplicate groups as
# the r4 list did (zero), so the strictness costs nothing observable and the
# summary line's word "identical" becomes literally true.
_DEDUP_BOOKKEEPING = frozenset({"id", "content_hash", "created_at"})


def _dedup_identity(row: dict) -> str:
    """Everything about a stored row that says what the skill DOES.

    Two rows with the same identity are interchangeable and one may be
    deleted; two rows that differ anywhere else are different skills that
    happen to collide on a hash covering only four of their fields. See the
    comment at the dedup pass for the adversarial round that forced this.
    """
    return json.dumps({k: v for k, v in row.items()
                       if k not in _DEDUP_BOOKKEEPING},
                      sort_keys=True, default=repr)


def _workspace_skills_path() -> Path:
    """The skills store maro actually uses.

    Was hardcoded to ``~/.maro/workspace/memory/skills.jsonl`` in both the
    doctor check and the cleanup verb, while every runtime caller resolves
    through ``config.workspace_root()`` — so under a MARO_WORKSPACE override
    doctor reported on, and rewrote, a store the running system was not
    using. Identical to the old constant when no override is set.
    """
    try:
        from orch_items import memory_dir
        return memory_dir() / "skills.jsonl"
    except Exception:
        return Path.home() / ".maro" / "workspace" / "memory" / "skills.jsonl"


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
    workspace_skills = skills_path or _workspace_skills_path()

    if not workspace_skills.exists():
        print("Workspace skills file not found — nothing to clean")
        return

    # Load all skills. Announced + strand-and-carry (2026-08-20,
    # destructive-rewrite sweep triage): this is a REWRITE, so a row this
    # loop drops is gone from disk, not merely absent from the result.
    # Probed live before the fix — a truncated row (the shape a crashed
    # append actually leaves) was deleted by the rewrite while the closing
    # summary said "0 total removed", and one non-UTF-8 byte crashed the
    # whole verb with UnicodeDecodeError. Deliberate drops (stale-hash,
    # duplicates) still drop; what this verb was never asked to delete now
    # rides the rewrite verbatim.
    # The read happens INSIDE the lock, and the lock is held through the
    # rewrite. Adversarial round 2026-08-20, 5/5 consensus HIGH: taking the
    # lock only around the write left a window where a save_skill() landing
    # between the snapshot and the lock was overwritten by the stale
    # snapshot — a lost update in a verb whose whole job is repair, and the
    # exact "loud read becomes silent deletion downstream" shape the arc
    # exists to remove. Probed: a skill saved mid-cleanup did not survive.
    from file_lock import locked_write, atomic_write
    from jsonl_utils import (loads_clean as _loads_clean,
                             store_text as _store_text,
                             is_frame_blank as _is_frame_blank)

    with locked_write(workspace_skills):
        all_skills = []
        stranded: "list[tuple[int, str]]" = []
        kinds: "list[str]" = []
        # Position by object identity, NOT a key on the row: every key a row
        # carries is part of `_dedup_identity`, so stamping one on would make
        # every row unique and silently disable the dedup this verb exists
        # for — and it would be written into the store.
        ordinals: "dict[int, int]" = {}

        def _strand(raw: str, kind: str, e: Exception) -> None:
            """Carry a row through untouched, and say which repair it needs.

            Order is load-bearing. `skills.load_skills` reads the file in
            reverse and lets the LAST row for an id win, so where a row sits
            in the file decides which skill is live. Adversarial r7 (Skeptic,
            probed): the rewrite appended all stranded rows AFTER the
            admitted ones, and a legacy row sharing an id with a verified one
            was promoted from ignored to live — by a verb that printed "0
            removed" and "kept in place". Nothing was deleted and the system
            still changed behaviour. Positions are carried through and the
            file is rebuilt in the order it was read.
            """
            stranded.append((len(all_skills) + len(stranded), raw))
            kinds.append(kind)
            what = ("Unreadable line" if kind == "corrupt"
                    else "Readable line that is not provably a skill")
            print(f"{what} kept as-is (not removed) in {workspace_skills}: "
                  f"{type(e).__name__}: {e}")
        # split("\n"), not splitlines(): JSONL frames on LF alone, while
        # splitlines() also breaks on U+2028/U+2029 and friends, which are
        # legal INSIDE a JSON string — a rewrite would turn one such row
        # into two invalid fragments. The raw line is what gets carried;
        # only a stripped copy is offered to the parser.
        for raw in _store_text(workspace_skills).split("\n"):
            if _is_frame_blank(raw):
                continue
            try:
                # The RAW line, not a stripped copy. `str.strip()` removes
                # Unicode whitespace that JSON does not permit, so
                # "\u2028" + a valid row parsed after stripping, was
                # admitted, and came back re-serialised with those bytes
                # gone and nothing announced (adversarial r9, 2 lenses,
                # probed). A rewrite that normalises what it could not read
                # verbatim is the launder this verb exists to prevent.
                row = _loads_clean(raw)
            except Exception as e:
                # The BYTES or the JSON are the problem. Recorded as a kind
                # here, where it is known, rather than inferred downstream
                # from the exception's class name: r7 counted the two
                # refusals by `reason.startswith(("KeyError", "TypeError",
                # "ValueError"))`, and adversarial r8 (Minimalist, probed)
                # walked past it with a 401-digit `success_rate` — valid
                # JSON, readable bytes, refused with OverflowError, reported
                # to the operator as byte corruption. That is a denylist
                # again, and it is the same lesson r5 already wrote down.
                _strand(raw, "corrupt", e)
                continue
            try:
                if not isinstance(row, dict):
                    # `[]`, `null` and `"x"` are valid JSON but not rows;
                    # without this they reached .get() and crashed the verb.
                    raise TypeError(f"not a JSON object: {type(row).__name__}")
                # ...and a dict is not yet a Skill. Adversarial r2
                # (2026-08-20, verified): _skill_hash_is_stale() returns
                # "not stale" for anything it cannot build, so a row that is
                # merely an object could carry a healthy skill's content_hash
                # plus a higher score, win the dedup, and DELETE the healthy
                # row — confident destructive output derived from garbage.
                # Probed: healthy skill gone, forged row kept, no warning.
                #
                # r2 answered that with dict_to_skill(), which adversarial r3
                # (5/5 consensus) then showed is a CONSTRUCTOR, not a
                # validator: `description=7` sails through it, the hash
                # cannot be recomputed, and the same forgery still deleted
                # the healthy skill. Probed: 2 rows in, only `forged` out.
                # validate_skill_row PROVES the row before it is admitted to
                # a decision about which rows to remove.
                from skill_types import validate_skill_row as _to_skill
                _to_skill(row)
            except Exception as e:
                # Readable. Just not provably a skill — a different repair.
                _strand(raw, "unprovable", e)
                continue
            ordinals[id(row)] = len(all_skills) + len(stranded)
            all_skills.append(row)

        print(f"Loaded {len(all_skills)} skills")
        _cleanup_pass(workspace_skills, all_skills, stranded,
                      atomic_write, kinds, ordinals)


def _cleanup_pass(workspace_skills, all_skills, stranded, atomic_write,
                  kinds=(), ordinals=None) -> None:
    """Classify and rewrite. Split out only so the lock's scope is one
    `with` block; the caller holds the lock for the whole of this."""
    from collections import defaultdict

    # Pass 1: remove stale-hash skills (test fixtures that leaked in)
    stale = [s for s in all_skills if _skill_hash_is_stale(s)]
    if stale:
        print(f"Found {len(stale)} skill(s) with stale content_hash (test fixtures):")
        for s in stale:
            # created_at too: it is the only thing that tells two rows
            # sharing an id apart, and this branch DELETES the row
            # (adversarial r8, 2 lenses — r7 named the rows on the duplicate
            # branch and left its sibling naming half of one).
            print(f"  {s.get('id', '?'):12} '{s.get('name', '?')}' "
                  f"({s.get('created_at', '?')}) in {workspace_skills} — "
                  f"stored hash doesn't match content")
    else:
        print("No stale-hash skills found")
    # Filter by ROW, not by id. Adversarial round 2026-08-20 (Minimalist,
    # verified): an id set removed EVERY row carrying a stale row's id, so a
    # healthy skill sharing that id was destroyed too — and the closing
    # summary counted only the stale one. Probed: 2 rows in, 0 left, "1
    # removed" reported. Duplicate ids are not hypothetical here: a
    # byte-tainted twin never id-matches on rewrite, so ids do accumulate.
    stale_rows = {id(s) for s in stale}
    clean = [s for s in all_skills if id(s) not in stale_rows]

    # Pass 2: deduplicate by what the rows SAY THE SKILL DOES, not by the
    # hash they declare.
    #
    # Adversarial r4 (2026-08-20, 5/5 consensus) is the reason this is not
    # `by_hash[row["content_hash"]]` any more. Grouping by the stored hash
    # asks a row to nominate its own peers, and `compute_skill_hash` covers
    # only name + description + steps_template + objective — so a row could
    # copy a healthy skill's hash, change `trigger_patterns` (which decides
    # what the skill MATCHES), carry junk in `tier`, and win the group on a
    # later `created_at`. The healthy row was then deleted for being a
    # "duplicate" of something that behaves differently. Five lenses found
    # five different shapes of that; each one was a new field to validate,
    # which is the shape of a whack-a-mole, not a fix.
    #
    # So: two rows are duplicates only when they agree on EVERYTHING except
    # bookkeeping. A field this list does not name is a field whose
    # disagreement means "not provably redundant" — and the safe answer to
    # that is to keep both, which is also what the retention decree wants.
    # The old `else` branch here grouped hash-less rows by ID under a
    # comment that said "can't dedup without a key"; it deduped by ID, and
    # two unrelated hash-less rows sharing one were one delete away from
    # each other (Minimalist, probed). There is no fallback key now.
    by_hash: dict = defaultdict(list)
    for skill in clean:
        by_hash[_dedup_identity(skill)].append(skill)

    duplicates = {h: skills for h, skills in by_hash.items() if len(skills) > 1}
    if duplicates:
        print(f"Found {len(duplicates)} group(s) of identical skills:")
    else:
        print("No duplicates found")

    # Scoring: prefer recent + high success rate + high use count.
    #
    # "Recent" means the INSTANT, not the string. Adversarial r6 (2026-08-20,
    # Failure Operator, probed): `2026-01-01T00:00:00+14:00` sorts after
    # `2025-12-31T23:00:00-12:00` lexically and before it in real time, so
    # the older row was kept and the newer deleted. Both rows validate and
    # both timestamps are legal ISO-8601. Every row reaching here has been
    # proven to carry a parseable `created_at` (validate_skill_row), so this
    # cannot raise.
    def moment_of(skill):
        return datetime.fromisoformat(skill.get("created_at", ""))

    def score_skill(skill):
        return (moment_of(skill), float(skill.get("success_rate", 0)),
                int(skill.get("use_count", 0)))

    # Every destructive verb in this arc owes the operator the path it is
    # about to rewrite — "the path is part of the result" (retention decree).
    # r7 named the rows and still never named the store; five of five r8
    # seats found that independently, and it is the claim the r7 comment
    # itself makes two lines below without an executing line behind it.
    print(f"Rewriting {workspace_skills}")
    total_dup_removed = 0
    undecidable = []
    kept = []
    for skills in by_hash.values():
        # A group whose timestamps mix offset-aware and naive values cannot
        # be ranked: `replace(tzinfo=utc)` is not a conversion, it ASSERTS a
        # fact the row does not carry, and a naive value can denote either
        # side of an aware one. r6 did exactly that and deleted a row on the
        # invented instant (adversarial r7, Architect, probed). Both shapes
        # are in the live store, and `max()` over mixed awareness raises, so
        # doing nothing is not an option either — the answer the retention
        # decree already gives is: keep both, and say why.
        aware = {moment_of(s).tzinfo is not None for s in skills}
        if len(skills) > 1 and len(aware) > 1:
            undecidable.append(skills)
            kept.extend(skills)
            continue
        best = max(skills, key=score_skill)
        kept.append(best)
        if len(skills) <= 1:
            continue
        total_dup_removed += len(skills) - 1
        # Name the rows. Adversarial r7 (4 lenses): the group line printed a
        # shared hash prefix and a shared name — the two things that by
        # construction cannot tell the rows apart — so an operator could not
        # tell WHICH record a repair verb had just destroyed, or recover it.
        # The path is part of the result; so is the id.
        losers = [s for s in skills if s is not best]
        print(f"  {best.get('content_hash', '')[:16] or '(no hash)'}... : "
              f"keeping '{best.get('id', '?')}' "
              f"({best.get('created_at', '?')}) of "
              f"{len(skills)} identical copies of '{best.get('name', '?')}'")
        for s in losers:
            # The path on the line that announces the destruction, not only
            # on a header four lines up: adversarial r9 (5/5 seats) —
            # operator logs are read, filtered and forwarded line by line,
            # and a deletion line that cannot say which store it deleted
            # from is not a receipt.
            print(f"      removing '{s.get('id', '?')}' "
                  f"({s.get('created_at', '?')}) from {workspace_skills} "
                  f"— same behaviour, older")

    for skills in undecidable:
        print(f"  keeping ALL {len(skills)} copies of "
              f"'{skills[0].get('name', '?')}' "
              f"({', '.join(repr(s.get('id', '?')) for s in skills)}) — their "
              f"timestamps mix offset-aware and naive values, so which one is "
              f"newer is not something this verb can prove")

    # Rewrite in the order the rows were READ, admitted and stranded alike.
    # `skills.load_skills` reads the file in reverse and lets the last row
    # for an id win, so position decides which skill is live: appending the
    # stranded rows at the end promoted a legacy row over a verified one
    # (adversarial r7, probed) in a run that reported "0 removed".
    ordinals = ordinals or {}
    rows = [(ordinals.get(id(s), 0), json.dumps(s)) for s in kept] + list(stranded)
    output_lines = [text for _, text in sorted(rows, key=lambda r: r[0])]
    atomic_write(workspace_skills, "\n".join(output_lines) + "\n",
                 errors="surrogateescape")
    total_removed = len(stale) + total_dup_removed
    print(
        f"Cleaned {workspace_skills}: {len(kept)} skills remain "
        f"({len(stale)} stale-hash + {total_dup_removed} duplicate(s) removed, "
        f"{total_removed} total)"
    )
    if stranded:
        # Two different refusals, two different repairs. A byte-tainted or
        # unparseable row needs the bytes fixed; a row that parses fine but
        # cannot be proven to be a Skill needs a field. r6's stricter
        # validator made the second kind common and the summary reported
        # both as corruption (adversarial r7, QA), which points the operator
        # at the wrong tool.
        schema = list(kinds).count("unprovable")
        corrupt = len(stranded) - schema
        parts = []
        if corrupt:
            parts.append(f"{corrupt} unparseable/byte-tainted")
        if schema:
            parts.append(f"{schema} readable but unprovable as a skill")
        print(f"Kept in place in {workspace_skills}: {' + '.join(parts)} "
              f"row(s) — this verb removes stale and duplicate skills, not "
              f"rows it cannot read or cannot prove")


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
