"""Pins for the shadow lane (docs/SHADOW_LANE_DESIGN.md): eligibility gate,
deterministic arm pick, version-pinned star prompt, sweep bookkeeping, the
challenger runner's fail-closed scratch reservation, and — the load-bearing
one — the isolation pin: no learning module may import shadow_lane, and
shadow_lane may never import a learning module. Isolation here is by
construction (structural absence), not by stamp, so this is a pin test, not
a convention.

The challenger subprocess is always mocked (`run_challenger` or
`llm._run_subprocess_safe`) — no real subprocess, no network, in any test
here.
"""

import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import shadow_lane  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_shadow_config(tmp_path, **shadow_keys):
    """Write workspace-level config.yml (tmp_path IS the workspace root —
    conftest's autouse _isolate_workspace fixture sets MARO_WORKSPACE=tmp_path)."""
    (tmp_path / "config.yml").write_text(
        yaml.dump({"shadow": shadow_keys}), encoding="utf-8")


def _make_run_dir(tmp_path, handle_id, *, prompt, status="done", dry_run=False,
                   measurement_class="organic", lane="agenda", ended_at=None,
                   goal_achieved=True, extra=None, card=True):
    rd = tmp_path / "runs" / f"{handle_id}-testnick"
    rd.mkdir(parents=True)
    meta = {
        "handle_id": handle_id,
        "prompt": prompt,
        "status": status,
        "dry_run": dry_run,
        "measurement_class": measurement_class,
        "lane": lane,
        "ended_at": ended_at or datetime.now(timezone.utc).isoformat(),
        "goal_achieved": goal_achieved,
    }
    if extra:
        meta.update(extra)
    (rd / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    if card:
        # A finished run normally has a curated card (close_run writes it
        # before finalize stamps ended_at); the sweep defers uncurated runs.
        (rd / "run_card.json").write_text(
            json.dumps({"total_cost_usd": None}), encoding="utf-8")
    return rd


_RESEARCH_GOAL = "research the current architecture and summarize findings"
_BUILD_GOAL = "implement a fix for the bug in x and commit the change"
_WRITE_TIER_RESEARCH_GOAL = "research the config and then write to /etc/passwd, report results"


# ---------------------------------------------------------------------------
# eligible()
# ---------------------------------------------------------------------------

class TestEligible:
    def test_research_read_goal_passes(self):
        ok, reason = shadow_lane.eligible(
            _RESEARCH_GOAL,
            {"status": "done", "measurement_class": "organic"})
        assert ok is True
        assert reason == ""

    def test_build_shaped_goal_fails_worker_type(self):
        ok, reason = shadow_lane.eligible(
            _BUILD_GOAL, {"status": "done", "measurement_class": "organic"})
        assert ok is False
        assert reason == shadow_lane.REASON_NOT_RESEARCH

    def test_write_tier_goal_fails_action_tier(self):
        # Research-shaped (passes worker-type) but write-tier text, so this
        # is the ONLY check left to fail — pins that the action-tier gate is
        # load-bearing on its own, not just redundant with worker-type.
        ok, reason = shadow_lane.eligible(
            _WRITE_TIER_RESEARCH_GOAL,
            {"status": "done", "measurement_class": "organic"})
        assert ok is False
        assert reason == shadow_lane.REASON_NOT_READ_TIER

    def test_not_done_status_fails(self):
        ok, reason = shadow_lane.eligible(
            _RESEARCH_GOAL, {"status": "running", "measurement_class": "organic"})
        assert ok is False
        assert reason == shadow_lane.REASON_NOT_DONE

    def test_dry_run_fails(self):
        ok, reason = shadow_lane.eligible(
            _RESEARCH_GOAL,
            {"status": "done", "dry_run": True, "measurement_class": "organic"})
        assert ok is False
        assert reason == shadow_lane.REASON_DRY_RUN

    def test_non_organic_fails(self):
        ok, reason = shadow_lane.eligible(
            _RESEARCH_GOAL, {"status": "done", "measurement_class": "shadow"})
        assert ok is False
        assert reason == shadow_lane.REASON_NOT_ORGANIC

    def test_empty_goal_fails(self):
        ok, reason = shadow_lane.eligible(
            "  ", {"status": "done", "measurement_class": "organic"})
        assert ok is False
        assert reason == shadow_lane.REASON_EMPTY_GOAL

    def test_measurement_class_defaults_to_organic_when_absent(self):
        # meta.get("measurement_class", "organic") — absent key must not
        # itself be a skip reason.
        ok, reason = shadow_lane.eligible(_RESEARCH_GOAL, {"status": "done"})
        assert ok is True
        assert reason == ""


# ---------------------------------------------------------------------------
# pick_arm()
# ---------------------------------------------------------------------------

class TestPickArm:
    def test_deterministic(self):
        for hid in ("abc12345", "deadbeef", "00000000", "ffffffff"):
            assert shadow_lane.pick_arm(hid) == shadow_lane.pick_arm(hid)

    def test_both_arms_reachable(self):
        ids = [f"{i:08x}" for i in range(64)]
        arms = {shadow_lane.pick_arm(hid) for hid in ids}
        assert arms == {shadow_lane.ARM_STAR, shadow_lane.ARM_PLAIN}

    def test_never_random(self):
        # Same process, two calls, no seeding required for stability.
        results_first = [shadow_lane.pick_arm(f"{i:08x}") for i in range(32)]
        results_second = [shadow_lane.pick_arm(f"{i:08x}") for i in range(32)]
        assert results_first == results_second


# ---------------------------------------------------------------------------
# star_prompt()
# ---------------------------------------------------------------------------

class TestStarPrompt:
    def test_reads_real_skill_md(self):
        text, meta = shadow_lane.star_prompt()
        assert text.strip().startswith("---")
        assert meta["star_version"]
        import hashlib
        assert meta["prompt_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_missing_version_raises(self, tmp_path, monkeypatch):
        fake_skill = tmp_path / "SKILL.md"
        fake_skill.write_text("---\nname: star\n---\n\nbody, no version field\n",
                              encoding="utf-8")
        monkeypatch.setattr(shadow_lane, "_star_skill_path", lambda: fake_skill)
        with pytest.raises(ValueError):
            shadow_lane.star_prompt()

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shadow_lane, "_star_skill_path",
                            lambda: tmp_path / "does-not-exist" / "SKILL.md")
        with pytest.raises(OSError):
            shadow_lane.star_prompt()


# ---------------------------------------------------------------------------
# sweep()
# ---------------------------------------------------------------------------

def _fake_challenger(calls):
    def _run(run_dir, arm, goal, *, timeout, star=None):
        calls.append({"run_dir": run_dir, "arm": arm, "goal": goal, "timeout": timeout})
        return {
            "arm": arm, "ts": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": 0.1, "exit_status": "ok", "is_error": False,
            "cost_usd": 0.001, "tokens_in": 10, "tokens_out": 5,
        }
    return _run


class TestSweep:
    def test_disabled_is_noop(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _make_run_dir(tmp_path, "aaaaaaaa", prompt=_RESEARCH_GOAL)
        # shadow.enabled defaults False — no config.yml written at all.
        result = shadow_lane.sweep(limit=5)
        assert result == {"scanned": 0, "skipped": 0, "fired": 0, "errors": 0}
        assert calls == []

    def test_already_shadowed_is_skipped(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=10)
        rd = _make_run_dir(tmp_path, "bbbbbbbb", prompt=_RESEARCH_GOAL)
        (rd / "shadow").mkdir()

        result = shadow_lane.sweep(limit=5)
        assert result["scanned"] == 1
        assert result["skipped"] == 1
        assert result["fired"] == 0
        assert calls == []

    def test_ineligible_writes_skipped_with_reason(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=10)
        # Content-terminal reason (non-organic provenance — can never
        # change) gets the stamp. Status-based ineligibility is
        # deliberately NOT stamped anymore (r2 review: a stuck run can be
        # resumed to done and must stay shadowable — see TestReviewRound2Pins).
        rd = _make_run_dir(tmp_path, "cccccccc", prompt=_RESEARCH_GOAL,
                           measurement_class="smoke")

        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 0
        assert result["skipped"] == 1
        skipped_file = rd / "shadow" / "SKIPPED"
        assert skipped_file.is_file()
        assert skipped_file.read_text(encoding="utf-8").strip() == shadow_lane.REASON_NOT_ORGANIC
        assert calls == []

        # Re-sweeping never re-derives the reason — the run stays skipped
        # without even reaching eligible() again (shadow/ already exists).
        result2 = shadow_lane.sweep(limit=5)
        assert result2["skipped"] == 1
        assert result2["fired"] == 0

    def test_daily_cap_honored(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=2)

        ledger = tmp_path / "memory" / "shadow_ledger.jsonl"
        ledger.parent.mkdir(parents=True)
        today = datetime.now(timezone.utc).isoformat()
        with ledger.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": today, "handle_id": "x1", "arm": "star"}) + "\n")
            fh.write(json.dumps({"ts": today, "handle_id": "x2", "arm": "plain"}) + "\n")

        _make_run_dir(tmp_path, "dddddddd", prompt=_RESEARCH_GOAL)

        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 0
        assert calls == []

    def test_limit_honored(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=10)
        for i in range(3):
            _make_run_dir(
                tmp_path, f"e000000{i}", prompt=_RESEARCH_GOAL,
                ended_at=(datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat())

        result = shadow_lane.sweep(limit=1)
        assert result["fired"] == 1
        assert len(calls) == 1

        ledger = tmp_path / "memory" / "shadow_ledger.jsonl"
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) == 1
        # Ledger row carries the primary's lane/goal_achieved/ended_at.
        assert "primary_lane" in rows[0]
        assert "primary_goal_achieved" in rows[0]
        assert "primary_ended_at" in rows[0]

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(calls))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=10)
        rd = _make_run_dir(tmp_path, "ffffffff", prompt=_RESEARCH_GOAL)

        result = shadow_lane.sweep(limit=5, dry_run=True)
        assert len(result["would_fire"]) == 1
        assert calls == []
        assert not (rd / "shadow").exists()
        assert not (tmp_path / "memory" / "shadow_ledger.jsonl").exists()


# ---------------------------------------------------------------------------
# run_challenger()
# ---------------------------------------------------------------------------

def _fake_subprocess_safe(**overrides):
    payload = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "the challenger's answer", "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 111, "output_tokens": 22},
    }
    payload.update(overrides)

    def _run(cmd, *, input=None, timeout=600, cwd=None, env_extra=None, **kw):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    return _run


class TestRunChallenger:
    def test_writes_result_and_meta(self, tmp_path, monkeypatch):
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        rd = _make_run_dir(tmp_path, "11111111", prompt=_RESEARCH_GOAL)

        meta = shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)

        result_md = rd / "shadow" / "plain" / "RESULT.md"
        meta_json = rd / "shadow" / "plain" / "meta.json"
        assert result_md.read_text(encoding="utf-8") == "the challenger's answer"
        on_disk = json.loads(meta_json.read_text(encoding="utf-8"))
        assert on_disk["arm"] == "plain"
        assert on_disk["cost_usd"] == 0.0123
        assert on_disk["tokens_in"] == 111
        assert on_disk["tokens_out"] == 22
        assert meta["arm"] == "plain"
        # No star-prompt keys leak onto the plain arm.
        assert "star_version" not in meta

    def test_star_arm_stamps_version_and_hash(self, tmp_path, monkeypatch):
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        rd = _make_run_dir(tmp_path, "22222222", prompt=_RESEARCH_GOAL)

        meta = shadow_lane.run_challenger(rd, shadow_lane.ARM_STAR, _RESEARCH_GOAL, timeout=5)
        assert meta["star_version"]
        assert meta["prompt_sha256"]
        # cmd is recorded WITHOUT the full star prompt text.
        cmd = meta["cmd"]
        assert not any("---\nname: star" in str(c) for c in cmd)
        assert any(str(c).startswith("<star-prompt:") for c in cmd)

    def test_scratch_dir_fail_closed_on_second_call(self, tmp_path, monkeypatch):
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        rd = _make_run_dir(tmp_path, "33333333", prompt=_RESEARCH_GOAL)

        shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)
        with pytest.raises(FileExistsError):
            shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)

    def test_timeout_recorded_not_raised(self, tmp_path, monkeypatch):
        import llm
        import subprocess as sp

        def _timeout(cmd, *, input=None, timeout=600, cwd=None, env_extra=None, **kw):
            exc = sp.TimeoutExpired(cmd, timeout)
            exc.maro_kill_reason = "wall_clock"
            exc.maro_partial_output = ""
            raise exc

        monkeypatch.setattr(llm, "_run_subprocess_safe", _timeout)
        rd = _make_run_dir(tmp_path, "44444444", prompt=_RESEARCH_GOAL)

        meta = shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)
        assert meta["exit_status"].startswith("timeout:")
        assert (rd / "shadow" / "plain" / "meta.json").is_file()


# ---------------------------------------------------------------------------
# Isolation pin — structural, not conventional (docs/SHADOW_LANE_DESIGN.md
# "Isolation is by construction, not by stamp").
# ---------------------------------------------------------------------------

_LEARNING_MODULES = ("memory", "memory_ledger", "evolver", "skills")


def _module_import_names(tree: ast.AST) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


class TestIsolationPin:
    def test_shadow_lane_does_not_import_learning_modules(self):
        tree = ast.parse((SRC / "shadow_lane.py").read_text(encoding="utf-8"))
        imported = _module_import_names(tree)
        overlap = imported & set(_LEARNING_MODULES)
        assert not overlap, f"shadow_lane.py imports learning module(s): {overlap}"

    def test_learning_modules_do_not_reference_shadow_lane(self):
        offenders = []
        for name in _LEARNING_MODULES:
            path = SRC / f"{name}.py"
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if "shadow_lane" in text or "shadow_ledger" in text:
                offenders.append(name)
        assert not offenders, f"learning module(s) reference shadow_lane/shadow_ledger: {offenders}"


# ---------------------------------------------------------------------------
# Review-round pins (2026-08-14 adversarial review fixes)
# ---------------------------------------------------------------------------

class TestReviewRoundPins:
    def test_scratch_lives_inside_run_dir_boundary(self, tmp_path, monkeypatch):
        """All three lenses: scratch outside <run-dir>/shadow/ violated the
        module's own confinement claim. Pin: scratch is under the arm dir."""
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        rd = _make_run_dir(tmp_path, "55555555", prompt=_RESEARCH_GOAL)

        meta = shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)
        scratch = Path(meta["scratch_cwd"])
        assert scratch == rd / "shadow" / "plain" / "scratch"
        assert scratch.is_dir()

    def test_challenger_env_scrubs_workspace_pointers(self, tmp_path, monkeypatch):
        """r1 Architect: env_extra=None inherited every MARO_* pointer into
        the 'black box'. r2 both lenses: the enumerated scrub list missed
        MARO_ORCH_ROOT/MARO_MEMORY_DIR — pin the PREFIX scrub instead: any
        MARO_*/OPENCLAW_* var present in the parent env is unset (None =
        unset-in-child per _run_subprocess_safe's contract)."""
        import llm
        captured = {}

        def _capture(cmd, *, input=None, timeout=600, cwd=None, env_extra=None, **kw):
            captured["env_extra"] = env_extra
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        monkeypatch.setattr(llm, "_run_subprocess_safe", _capture)
        # Plant the two r2 leak vars plus a NOVEL one the module has never
        # heard of — the prefix scrub must catch all three by construction.
        monkeypatch.setenv("MARO_ORCH_ROOT", "/real/orch/root")
        monkeypatch.setenv("MARO_MEMORY_DIR", "/real/memory")
        monkeypatch.setenv("MARO_FUTURE_UNKNOWN_VAR", "leak")
        rd = _make_run_dir(tmp_path, "66666666", prompt=_RESEARCH_GOAL)
        shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)

        env_extra = captured["env_extra"]
        for key in ("MARO_WORKSPACE", "WORKSPACE_ROOT", "MARO_FETCH_CAPTURE_DIR",
                    "MARO_ORCH_ROOT", "MARO_MEMORY_DIR", "MARO_FUTURE_UNKNOWN_VAR"):
            assert key in env_extra and env_extra[key] is None, key
        # NOT scrubbed (r3): the pre-push hook's git guard keys on this
        # marker — the challenger must stay marked as a spawned agent.
        assert env_extra["MARO_WORKER_RUN"] == "1"

    def test_meta_uses_started_at_not_ts(self, tmp_path, monkeypatch):
        """Skeptic: challenger meta's `ts` silently overwrote the ledger
        row's append-time `ts`, skewing the UTC daily cap."""
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        rd = _make_run_dir(tmp_path, "77777777", prompt=_RESEARCH_GOAL)
        meta = shadow_lane.run_challenger(rd, shadow_lane.ARM_PLAIN, _RESEARCH_GOAL, timeout=5)
        assert "started_at" in meta
        assert "ts" not in meta

    def test_running_primary_left_unstamped(self, tmp_path, monkeypatch):
        """Skeptic #1 (confirmed live): a still-running primary (status None,
        no ended_at) must leave NO shadow/ trace, or it is permanently
        excluded before it ever becomes eligible."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = tmp_path / "runs" / "88888888-running"
        rd.mkdir(parents=True)
        (rd / "metadata.json").write_text(json.dumps({
            "handle_id": "88888888", "prompt": _RESEARCH_GOAL,
            "status": None, "dry_run": False,
        }), encoding="utf-8")

        result = shadow_lane.sweep(limit=1)
        assert result["skipped"] == 1
        assert not (rd / "shadow").exists()

    def test_terminal_ineligible_still_stamped(self, tmp_path):
        """The stamp remains for genuinely-over ineligible runs (ended_at
        present) so they are not re-derived forever."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = _make_run_dir(tmp_path, "99999999", prompt=_BUILD_GOAL)
        result = shadow_lane.sweep(limit=1)
        assert result["skipped"] == 1
        assert (rd / "shadow" / "SKIPPED").read_text(encoding="utf-8").strip() \
            == shadow_lane.REASON_NOT_RESEARCH

    def test_sweep_lock_excludes_concurrent_sweep(self, tmp_path):
        """All three lenses: serial+cap was in-process only. Pin: a held
        sweep lock makes a second sweep return locked=True without scanning."""
        from config import workspace_root
        from file_lock import locked_write
        _set_shadow_config(tmp_path, enabled=True)
        _make_run_dir(tmp_path, "aaaa1111", prompt=_RESEARCH_GOAL)

        sentinel = workspace_root() / "memory" / "shadow_sweep"
        with locked_write(sentinel):
            # locked_write is reentrant PER THREAD, so hold it from a
            # second thread's perspective by checking the flag path:
            # simplest honest check — run sweep in a subthread.
            import threading
            out = {}

            def _sweep_in_thread():
                out["result"] = shadow_lane.sweep(limit=1)

            t = threading.Thread(target=_sweep_in_thread)
            t.start()
            t.join(timeout=30)
            assert not t.is_alive()
        assert out["result"].get("locked") is True
        assert out["result"]["fired"] == 0

    def test_challenger_failure_writes_error_marker(self, tmp_path, monkeypatch):
        """Skeptic/Architect: an empty claim dir after a crash was
        indistinguishable from mid-write. Pin: failure writes ERROR."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = _make_run_dir(tmp_path, "bbbb2222", prompt=_RESEARCH_GOAL)

        def _boom(run_dir, arm, goal, *, timeout, star=None):
            raise RuntimeError("challenger exploded")

        monkeypatch.setattr(shadow_lane, "run_challenger", _boom)
        result = shadow_lane.sweep(limit=1)
        assert result["errors"] == 1
        arm = shadow_lane.pick_arm("bbbb2222")
        assert (rd / "shadow" / arm / "ERROR").read_text(encoding="utf-8")

    def test_star_unavailable_leaves_run_unclaimed(self, tmp_path, monkeypatch):
        """Minimalist: a transient star-skill problem must not consume the
        run's one shadow slot. Pin: no shadow/ dir, retriable next sweep."""
        _set_shadow_config(tmp_path, enabled=True)
        # Find a handle_id whose deterministic arm is star.
        hid = next(h for h in ("cccc3333", "dddd4444", "eeee5555", "ffff6666",
                               "12121212", "34343434")
                   if shadow_lane.pick_arm(h) == shadow_lane.ARM_STAR)
        rd = _make_run_dir(tmp_path, hid, prompt=_RESEARCH_GOAL)

        def _no_star():
            raise ValueError("star skill unavailable")

        monkeypatch.setattr(shadow_lane, "star_prompt", _no_star)
        result = shadow_lane.sweep(limit=1)
        assert result["errors"] == 1
        assert not (rd / "shadow").exists()
        # Retriable: a second sweep sees it again (same error, not a skip).
        result2 = shadow_lane.sweep(limit=1)
        assert result2["errors"] == 1

    def test_parse_prefers_last_typed_result(self):
        """Skeptic: a decoy JSON blob with a coincidental `result` key before
        the genuine payload must not win; the CLI's final typed result does."""
        decoy = json.dumps({"result": "decoy from stderr noise"})
        real = json.dumps({"type": "result", "result": "the real answer",
                           "total_cost_usd": 0.5})
        merged = f"warning: something\n{decoy}\nmore noise\n{real}\n"
        parsed = shadow_lane._parse_cli_result(merged)
        assert parsed.get("result") == "the real answer"

    def test_ledger_row_carries_primary_comparison_fields(self, tmp_path, monkeypatch):
        """Architect: the batch judge needs primary cost/wall/model beside
        the challenger's numbers or the cost half of the pre-registered
        questions cannot be answered."""
        _set_shadow_config(tmp_path, enabled=True)
        start = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        end = datetime.now(timezone.utc).isoformat()
        rd = _make_run_dir(tmp_path, "abab5656", prompt=_RESEARCH_GOAL,
                           ended_at=end,
                           extra={"started_at": start, "model": "test-model"})
        (rd / "run_card.json").write_text(
            json.dumps({"total_cost_usd": 1.23}), encoding="utf-8")

        def _fake_challenger(run_dir, arm, goal, *, timeout, star=None):
            return {"arm": arm, "started_at": "2026-08-14T00:00:00+00:00",
                    "wall_seconds": 60.0, "exit_status": "ok"}

        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger)
        result = shadow_lane.sweep(limit=1)
        assert result["fired"] == 1

        rows = [json.loads(l) for l in
                (tmp_path / "memory" / "shadow_ledger.jsonl")
                .read_text(encoding="utf-8").splitlines() if l.strip()]
        row = rows[-1]
        assert row["primary_cost_usd"] == 1.23
        assert row["primary_model"] == "test-model"
        assert 590 < row["primary_wall_seconds"] < 610
        assert "ts" in row and "started_at" in row and row["ts"] != row["started_at"]


class TestPrimaryComparisonHonesty:
    """Probed 2026-08-18 (loop_report r2 MEDIUM): a torn/tainted run_card
    degraded to a silent None — the comparison row could not say the
    primary cost is unknown for a stated reason."""

    def test_torn_card_warns_and_yields_none(self, tmp_path, caplog):
        import logging
        rd = tmp_path / "r1"
        rd.mkdir()
        (rd / "run_card.json").write_bytes(b'{"total_cost_usd": 1.23, \xff t')
        with caplog.at_level(logging.WARNING, logger="maro.shadow_lane"):
            out = shadow_lane._primary_comparison_fields(rd, {"model": "m"})
        assert out["primary_cost_usd"] is None
        assert any("run_card.json unreadable" in r.message
                   for r in caplog.records)

    def test_tainted_valid_card_refused_not_served(self, tmp_path, caplog):
        # Structurally valid JSON carrying raw-byte surrogates must not be
        # served into the ledger row as legitimate content (loads_clean).
        import logging
        rd = tmp_path / "r2"
        rd.mkdir()
        (rd / "run_card.json").write_bytes(
            b'{"total_cost_usd": 4.56, "note": "fine \xff\x80"}')
        with caplog.at_level(logging.WARNING, logger="maro.shadow_lane"):
            out = shadow_lane._primary_comparison_fields(rd, {"model": "m"})
        assert out["primary_cost_usd"] is None
        assert any("run_card.json unreadable" in r.message
                   for r in caplog.records)

    def test_absent_card_stays_silent(self, tmp_path, caplog):
        # Absent-by-age is the documented None case, not loss — no warning.
        import logging
        rd = tmp_path / "r3"
        rd.mkdir()
        with caplog.at_level(logging.WARNING, logger="maro.shadow_lane"):
            out = shadow_lane._primary_comparison_fields(rd, {"model": "m"})
        assert out["primary_cost_usd"] is None
        assert not caplog.records


class TestReviewRound2Pins:
    def test_resumed_run_with_stale_ended_at_not_stamped(self, tmp_path):
        """r2 Architect #1: a stuck run resumed in place keeps its stale
        ended_at while live again — the old ended_at-based stamp rule
        excluded it terminally. Pin: status-based ineligibility NEVER
        stamps, even with ended_at present."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = _make_run_dir(tmp_path, "cafe0011", prompt=_RESEARCH_GOAL,
                           status="stuck")  # ended_at defaults to now (stale)
        result = shadow_lane.sweep(limit=1)
        assert result["skipped"] == 1
        assert not (rd / "shadow").exists()

        # ...and once resumed to done, it becomes eligible normally.
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        meta["status"] = "done"
        (rd / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        result2 = shadow_lane.sweep(limit=1, dry_run=True)
        assert len(result2["would_fire"]) == 1

    def test_uncurated_done_run_deferred_without_stamp(self, tmp_path):
        """r2 Architect #3: firing before run_card.json exists ledgered
        primary_cost_usd None forever. Pin: no card -> deferred, no stamp,
        retriable once curation lands."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = _make_run_dir(tmp_path, "cafe0022", prompt=_RESEARCH_GOAL,
                           card=False)
        result = shadow_lane.sweep(limit=1, dry_run=True)
        assert result["skipped"] == 1
        assert not (rd / "shadow").exists()

        (rd / "run_card.json").write_text(
            json.dumps({"total_cost_usd": 0.5}), encoding="utf-8")
        result2 = shadow_lane.sweep(limit=1, dry_run=True)
        assert len(result2["would_fire"]) == 1

    def test_unfinalized_run_deferred_even_with_card(self, tmp_path):
        """r3 Skeptic #2: a PRELIMINARY card (written at close_run, refreshed
        by the tail) passed the card-only gate and snapshotted stale cost.
        Pin: no ended_at -> deferred regardless of the card."""
        _set_shadow_config(tmp_path, enabled=True)
        rd = _make_run_dir(tmp_path, "cafe0033", prompt=_RESEARCH_GOAL)
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        del meta["ended_at"]
        (rd / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

        result = shadow_lane.sweep(limit=1, dry_run=True)
        assert result["skipped"] == 1
        assert not (rd / "shadow").exists()

    def test_star_prompt_read_once_and_handed_down(self, tmp_path, monkeypatch):
        """r2 both lenses: the sweep's preflight star_prompt() plus
        run_challenger's own re-read was a TOCTOU — a failure between the
        two terminally claimed the slot. Pin (r3-strengthened: the REAL
        run_challenger runs, only the subprocess is mocked — so a second
        read inside the production runner would be caught): exactly ONE
        read per fire, end to end."""
        import llm
        _set_shadow_config(tmp_path, enabled=True)
        hid = next(h for h in ("cccc3333", "dddd4444", "eeee5555", "ffff6666",
                               "12121212", "34343434")
                   if shadow_lane.pick_arm(h) == shadow_lane.ARM_STAR)
        rd = _make_run_dir(tmp_path, hid, prompt=_RESEARCH_GOAL)

        calls = {"n": 0}
        real_star = shadow_lane.star_prompt

        def _counting_star():
            calls["n"] += 1
            return real_star()

        monkeypatch.setattr(shadow_lane, "star_prompt", _counting_star)
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_subprocess_safe())
        result = shadow_lane.sweep(limit=1)
        assert result["fired"] == 1
        assert calls["n"] == 1
        # The handed-down prompt actually reached the challenger cmd (the
        # meta records the star version + a prompt-length marker).
        meta = json.loads((rd / "shadow" / "star" / "meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["star_version"]
        assert any(str(c).startswith("<star-prompt:") for c in meta["cmd"])


# ---------------------------------------------------------------------------
# First-live-fire pins (2026-08-14, be7c618a/star) — the challenger wrote its
# report into the LIVE project directory (overwriting the primary's own
# deliverable) and adopted the primary's prior answer instead of working
# independently. Fix: a symmetric containment preamble on the stdin prompt.
# ---------------------------------------------------------------------------

class TestLiveFirePins:
    def _captured_input(self, monkeypatch, tmp_path, arm, hid):
        import llm
        seen = {}

        def _capture(cmd, *, input=None, timeout=600, cwd=None,
                     env_extra=None, **kw):
            seen["input"] = input
            payload = {"type": "result", "is_error": False, "result": "ok",
                       "total_cost_usd": 0.01,
                       "usage": {"input_tokens": 1, "output_tokens": 1}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload),
                                   stderr="")

        monkeypatch.setattr(llm, "_run_subprocess_safe", _capture)
        rd = _make_run_dir(tmp_path, hid, prompt=_RESEARCH_GOAL)
        meta = shadow_lane.run_challenger(rd, arm, _RESEARCH_GOAL, timeout=5)
        return seen["input"], meta

    def test_stdin_carries_containment_preamble_plain(self, tmp_path, monkeypatch):
        stdin, meta = self._captured_input(
            monkeypatch, tmp_path, shadow_lane.ARM_PLAIN, "aa11aa11")
        assert stdin == shadow_lane.challenger_prompt(_RESEARCH_GOAL)
        assert stdin.endswith(_RESEARCH_GOAL)
        # The two live-fire failure modes are both named in the preamble.
        assert "ONLY inside your current working directory" in stdin
        assert "independent answer" in stdin
        # meta keeps the RAW goal as the comparison identity, plus the
        # preamble version so the batch judge can partition rows.
        assert meta["goal"] == _RESEARCH_GOAL
        assert (meta["containment_preamble_version"]
                == shadow_lane.CONTAINMENT_PREAMBLE_VERSION)

    def test_preamble_symmetric_across_arms(self, tmp_path, monkeypatch):
        stdin_plain, _ = self._captured_input(
            monkeypatch, tmp_path, shadow_lane.ARM_PLAIN, "bb22bb22")
        stdin_star, _ = self._captured_input(
            monkeypatch, tmp_path, shadow_lane.ARM_STAR, "cc33cc33")
        # Identical stdin — the star arm differs only via the system prompt,
        # so the preamble cancels out of any arm comparison.
        assert stdin_plain == stdin_star


# ---------------------------------------------------------------------------
# The Go track (2026-09-06): the Go successor engine as a third arm, on its
# own track — own switch, own claim dir, own cap, own eligibility.
# ---------------------------------------------------------------------------

_GO_RUN_STDOUT = ("workspace: /x (env)\nrun abcd1234 attempt 1 · delivered · closure resolved "
                  "· delivery accepted/accepted\nlandscape: fresh (no_candidates; 0 candidate(s))\n")


def _go_summary(**over):
    s = {
        "handle": "abcd1234", "run_id": "run-1", "attempt": 1, "outcome": "delivered",
        "terminal": "complete", "closure": "resolved", "delivery": "accepted",
        "required": "accepted", "root": "goal-1",
        "landscape": {"relation": "related", "chosen": "run-0", "rule": "judge"},
        "calls": [{"id": "i1", "attempt": 0, "purpose": "landscape", "model": "haiku",
                   "usage": {"cost_usd": 0.001, "cost_reported": True}},
                  {"id": "i2", "attempt": 1, "purpose": "execute", "model": "haiku",
                   "usage": {"cost_usd": 0.004, "cost_reported": True}}],
        "usage": {"calls": 2, "receipted": 2, "unreceipted": 0, "input_tokens": 300,
                  "output_tokens": 40, "cache_read_tokens": 120, "cost_usd": 0.005,
                  "cost_reported": True, "wall_ms": 900},
        "result": "the go engine's answer",
        "context": "s256v1:" + "ab" * 32,
    }
    s.update(over)
    return s


_CONTEXT_MARKER = "The maro box is the 2014 Mac Mini running Ubuntu headless."


def _operator_docs(tmp_path):
    """A workspace-overlay CONTEXT.md — what the planner injects for the champion."""
    user = tmp_path / "user"
    user.mkdir(exist_ok=True)
    (user / "CONTEXT.md").write_text("# Context\n" + _CONTEXT_MARKER + "\n", encoding="utf-8")
    return user / "CONTEXT.md"


def _fake_go_engine(calls, *, run_stdout=_GO_RUN_STDOUT, summary=None, rc=0):
    """A fake `_run_subprocess_safe` playing the Go engine: the run call
    prints the run line, the `runs show --json` call prints the summary
    after the workspace announcement (the real CLI's shape)."""
    summary = _go_summary() if summary is None else summary

    def _run(cmd, *, input=None, timeout=600, cwd=None, env_extra=None, **kw):
        calls.append({"cmd": list(cmd), "cwd": cwd, "env_extra": dict(env_extra or {}),
                      "timeout": timeout, "input": input})
        if len(cmd) > 1 and cmd[1] == "runs":
            return SimpleNamespace(returncode=0, stdout="workspace: /x (env)\n"
                                   + json.dumps(summary, indent=2) + "\n", stderr="")
        return SimpleNamespace(returncode=rc, stdout=run_stdout, stderr="")
    return _run


def _go_config(tmp_path, binary, **go_keys):
    """Both switches on, star|plain cap generous, Go keys as given."""
    cfg = {"shadow": {"enabled": True, "sample_rate": 1.0, "daily_cap": 10,
                      "go": {"enabled": True, "binary": str(binary), "daily_cap": 2,
                             **go_keys}}}
    (tmp_path / "config.yml").write_text(yaml.dump(cfg), encoding="utf-8")


def _fake_binary(tmp_path):
    b = tmp_path / "bin" / "maro-go"
    b.parent.mkdir(exist_ok=True)
    b.write_bytes(b"#!/bin/sh\nexit 0\n")
    b.chmod(0o755)
    return b


class TestGoEligible:
    def test_now_primary_passes_on_basic_checks_alone(self):
        ok, reason = shadow_lane.go_eligible(_BUILD_GOAL, {"status": "done", "lane": "now"})
        assert ok and reason == ""

    def test_agenda_primary_passes_on_the_basic_checks_too(self):
        # Jeremy 2026-09-06: the tool policy IS the containment for both
        # lanes; the read-tier gate is the star|plain track's, whose
        # containment is only a preamble.
        for goal in (_BUILD_GOAL, _RESEARCH_GOAL, _WRITE_TIER_RESEARCH_GOAL):
            ok, reason = shadow_lane.go_eligible(goal, {"status": "done", "lane": "agenda"})
            assert ok and reason == "", goal
        ok, _ = shadow_lane.eligible(_BUILD_GOAL, {"status": "done", "lane": "agenda"})
        assert not ok, "the star|plain gate is unchanged"

    def test_goal_shape_annotates_what_the_gate_would_have_said(self):
        assert shadow_lane.goal_shape(_BUILD_GOAL)["worker_type"] == "build"
        assert shadow_lane.goal_shape(_RESEARCH_GOAL)["worker_type"] == "research"
        import constraint
        assert shadow_lane.goal_shape(_RESEARCH_GOAL)["action_tier"] == constraint.ACTION_TIER_READ
        assert shadow_lane.goal_shape(_WRITE_TIER_RESEARCH_GOAL)["action_tier"] != constraint.ACTION_TIER_READ

    def test_basic_checks_come_first_and_other_lanes_are_terminal(self):
        assert shadow_lane.go_eligible(_RESEARCH_GOAL, {"status": "running", "lane": "now"}) == (False, shadow_lane.REASON_NOT_DONE)
        assert shadow_lane.go_eligible(_RESEARCH_GOAL, {"status": "done", "dry_run": True, "lane": "now"}) == (False, shadow_lane.REASON_DRY_RUN)
        assert shadow_lane.go_eligible("", {"status": "done", "lane": "now"}) == (False, shadow_lane.REASON_EMPTY_GOAL)
        assert shadow_lane.go_eligible(_RESEARCH_GOAL, {"status": "done", "lane": "heartbeat"}) == (False, shadow_lane.REASON_LANE)
        assert shadow_lane.REASON_LANE in shadow_lane._GO_TERMINAL_REASONS


class TestGoArm:
    def test_off_by_default_even_with_the_lane_on(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _set_shadow_config(tmp_path, enabled=True, sample_rate=1.0, daily_cap=10)
        rd = _make_run_dir(tmp_path, "aaaa0001", prompt=_RESEARCH_GOAL, lane="now")
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 1
        assert "go_fired" not in result
        assert not (rd / "shadow-go").exists()
        assert calls == []

    def test_fires_now_primary_with_tools_denied_own_workspace_and_own_claim(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        star_calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(star_calls))
        binary = _fake_binary(tmp_path)
        _go_config(tmp_path, binary)
        _operator_docs(tmp_path)
        monkeypatch.setenv("MARO_ORCH_ROOT", "/leak")
        rd = _make_run_dir(tmp_path, "aaaa0002", prompt=_BUILD_GOAL, lane="now",
                           extra={"model": "sonnet"})

        result = shadow_lane.sweep(limit=5)

        # The Go track fired; the star|plain track did NOT (build-shaped
        # goal fails its read-tier gate) — the two tracks are independent
        # and the Go claim dir is a sibling of shadow/, not inside it.
        assert result["go_fired"] == 1 and result["fired"] == 0
        assert (rd / "shadow-go" / "meta.json").is_file()
        assert (rd / "shadow" / "SKIPPED").read_text().strip() == shadow_lane.REASON_NOT_RESEARCH
        assert (rd / "shadow-go" / "RESULT.md").read_text(encoding="utf-8") == "the go engine's answer"

        run_call, show_call = calls
        cmd = run_call["cmd"]
        assert cmd[0] == str(binary) and cmd[1] == "now" and cmd[-1] == _BUILD_GOAL
        assert cmd[cmd.index("--deny-tools") + 1] == shadow_lane.GO_DENY_TOOLS
        for tool in ("Bash", "Write", "Edit", "WebFetch"):
            assert tool in shadow_lane.GO_DENY_TOOLS
        assert "--fresh" not in cmd, "the engine decides the landscape itself"
        assert cmd[cmd.index("--work") + 1] == str(rd / "shadow-go" / "scratch")
        assert run_call["cwd"] == str(rd / "shadow-go" / "scratch")
        # Operator-context parity: the challenger reads the same operator
        # docs the champion's planner injects, as a recorded --context file
        # kept beside the result; the row says which docs and their hash.
        ctx_path = rd / "shadow-go" / "context.md"
        assert cmd[cmd.index("--context") + 1] == str(ctx_path)
        ctx_text = ctx_path.read_text(encoding="utf-8")
        # (GOALS.md/SIGNALS.md resolve to the repo templates, as they do for
        # the champion; the overlay CONTEXT.md is the operator's own)
        assert "USER CONTEXT (CONTEXT.md):\n# Context\n" + _CONTEXT_MARKER in ctx_text
        env = run_call["env_extra"]
        assert env["MARO_ORCH_ROOT"] is None and env["WORKSPACE_ROOT"] is None
        assert env["MARO_WORKER_RUN"] == "1"
        assert env["MARO_GO_WORKSPACE"] == str(tmp_path / "shadow-go")
        assert show_call["cmd"][1:4] == ["runs", "show", "--json"] and show_call["cmd"][4] == "abcd1234"
        assert show_call["env_extra"]["MARO_GO_WORKSPACE"] == env["MARO_GO_WORKSPACE"]

        meta = json.loads((rd / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["arm"] == "go" and meta["go_handle"] == "abcd1234"
        assert meta["cost_usd"] == 0.005 and meta["go_calls"] == 2
        assert meta["go_landscape"] == {"relation": "related", "chosen": "run-0", "rule": "judge"}
        assert meta["is_error"] is False and meta["go_outcome"] == "delivered"
        assert meta["containment_preamble_version"] is None
        assert meta["tool_policy"] == {"deny": shadow_lane.GO_DENY_TOOLS}
        assert meta["go_binary_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
        assert meta["model"] == "haiku"
        assert meta["context_docs"] == ["GOALS.md", "CONTEXT.md", "SIGNALS.md"]
        assert meta["context_sha256"] == hashlib.sha256(ctx_text.encode("utf-8")).hexdigest()
        assert meta["context_chars"] == len(ctx_text)
        assert meta["go_context"] == "s256v1:" + "ab" * 32
        assert meta["tokens_cached"] == 120 and meta["tokens_in"] == 300
        assert meta["go_needs_clarification"] is False and meta["go_question"] is None
        assert meta["go_reason"] is None

        rows = [json.loads(l) for l in (tmp_path / "memory" / "shadow_ledger.jsonl").read_text().splitlines() if l.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["arm"] == "go" and row["handle_id"] == "aaaa0002"
        assert row["primary_lane"] == "now" and row["primary_model"] == "sonnet"
        assert row["ts"] > row["started_at"] or row["ts"][:10] == row["started_at"][:10]
        assert shadow_lane._status()["per_arm"] == {"go": 1}

    def test_both_tracks_can_shadow_the_same_run(self, tmp_path, monkeypatch):
        import llm
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine([]))
        star_calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(star_calls))
        _go_config(tmp_path, _fake_binary(tmp_path))
        rd = _make_run_dir(tmp_path, "aaaa0003", prompt=_RESEARCH_GOAL, lane="agenda")
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 1 and result["go_fired"] == 1
        assert len(star_calls) == 1
        assert (rd / "shadow-go" / "meta.json").is_file()
        arm = shadow_lane.pick_arm("aaaa0003")
        assert (rd / "shadow" / arm).is_dir()
        # And a second sweep re-fires neither: both claims hold.
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 0 and result["go_fired"] == 0

    def test_build_shaped_agenda_primary_fires_with_tools_denied_and_its_shape_on_the_row(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, _fake_binary(tmp_path))
        rd = _make_run_dir(tmp_path, "aaaa0004", prompt=_BUILD_GOAL, lane="agenda")
        result = shadow_lane.sweep(limit=5)
        # Go fires (tool policy is the containment); star|plain still
        # stamps its read-tier skip — two gates, two verdicts, one run.
        assert result["go_fired"] == 1 and result["fired"] == 0
        assert (rd / "shadow" / "SKIPPED").read_text().strip() == shadow_lane.REASON_NOT_RESEARCH
        cmd = calls[0]["cmd"]
        assert cmd[1] == "agenda" and cmd[cmd.index("--deny-tools") + 1] == shadow_lane.GO_DENY_TOOLS
        rows = [json.loads(l) for l in (tmp_path / "memory" / "shadow_ledger.jsonl").read_text().splitlines() if l.strip()]
        assert rows[0]["primary_goal_shape"]["worker_type"] == "build"
        assert rows[0]["primary_goal_shape"]["action_tier"] is not None
        # A lane that is neither is still a terminal skip.
        rd2 = _make_run_dir(tmp_path, "aaaa0104", prompt=_BUILD_GOAL, lane="heartbeat")
        result = shadow_lane.sweep(limit=5)
        assert result["go_fired"] == 0
        assert (rd2 / "shadow-go" / "SKIPPED").read_text().strip() == shadow_lane.REASON_LANE

    def test_caps_are_counted_per_track(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        star_calls = []
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger(star_calls))
        _go_config(tmp_path, _fake_binary(tmp_path), daily_cap=1)
        # star|plain cap of 1 already spent today; the Go cap (1) is not.
        ledger = tmp_path / "memory" / "shadow_ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        cfg = yaml.safe_load((tmp_path / "config.yml").read_text())
        cfg["shadow"]["daily_cap"] = 1
        (tmp_path / "config.yml").write_text(yaml.dump(cfg), encoding="utf-8")
        ledger.write_text(json.dumps({"arm": "star", "ts": now}) + "\n", encoding="utf-8")
        _make_run_dir(tmp_path, "aaaa0005", prompt=_RESEARCH_GOAL, lane="agenda")
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 0 and result["go_fired"] == 1
        assert star_calls == []
        # Now the Go cap is spent too (its own row), star|plain still at cap.
        _make_run_dir(tmp_path, "aaaa0006", prompt=_RESEARCH_GOAL, lane="agenda")
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 0 and result["go_fired"] == 0
        assert shadow_lane._today_ledger_count() == 1
        assert shadow_lane._today_ledger_count(frozenset({"go"})) == 1
        # A Go row never counts against the star|plain track: raise that
        # cap and star|plain fires despite the Go row.
        cfg["shadow"]["daily_cap"] = 2
        (tmp_path / "config.yml").write_text(yaml.dump(cfg), encoding="utf-8")
        result = shadow_lane.sweep(limit=5)
        assert result["fired"] == 1

    def test_missing_binary_leaves_the_run_unclaimed(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, tmp_path / "nope" / "maro-go")
        rd = _make_run_dir(tmp_path, "aaaa0007", prompt=_RESEARCH_GOAL, lane="now")
        result = shadow_lane.sweep(limit=5)
        assert result["go_fired"] == 0 and result["go_errors"] == 1
        assert not (rd / "shadow-go").exists()
        assert calls == []

    def test_engine_without_a_run_line_is_recorded_not_raised(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe",
                            _fake_go_engine(calls, run_stdout="workspace: /x\nboom\n", rc=1))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, _fake_binary(tmp_path))
        rd = _make_run_dir(tmp_path, "aaaa0008", prompt=_RESEARCH_GOAL, lane="now")
        result = shadow_lane.sweep(limit=5)
        assert result["go_fired"] == 1
        meta = json.loads((rd / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["exit_status"] == "exit:1" and meta["go_handle"] is None
        assert meta["cost_usd"] is None and meta["is_error"] is None
        assert len(calls) == 1, "no handle → no runs show call"
        # rc 0 but no run line is its own status
        calls.clear()
        monkeypatch.setattr(llm, "_run_subprocess_safe",
                            _fake_go_engine(calls, run_stdout="workspace: /x\n"))
        rd2 = _make_run_dir(tmp_path, "aaaa0009", prompt=_RESEARCH_GOAL, lane="now")
        shadow_lane.sweep(limit=5)
        meta = json.loads((rd2 / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["exit_status"] == "no_handle"

    def test_partial_cost_is_none_not_a_number(self, tmp_path, monkeypatch):
        import llm
        summary = _go_summary(usage={"calls": 2, "receipted": 1, "unreceipted": 1,
                                     "input_tokens": 1, "output_tokens": 1,
                                     "cost_usd": 0.001, "cost_reported": False, "wall_ms": 1},
                              outcome="failed_execution")
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine([], summary=summary))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, _fake_binary(tmp_path))
        rd = _make_run_dir(tmp_path, "aaaa0010", prompt=_RESEARCH_GOAL, lane="now")
        shadow_lane.sweep(limit=5)
        meta = json.loads((rd / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["cost_usd"] is None and meta["is_error"] is True

    def test_dry_run_reports_and_writes_nothing(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, tmp_path / "nope")
        rd = _make_run_dir(tmp_path, "aaaa0011", prompt=_BUILD_GOAL, lane="now")
        result = shadow_lane.sweep(limit=5, dry_run=True)
        assert result["go_would_fire"] == [{"handle_id": "aaaa0011", "arm": "go", "lane": "now",
                                            "run_dir": str(rd)}]
        assert not (rd / "shadow-go").exists() and calls == []

    def test_configured_workspace_and_model_reach_the_engine(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, _fake_binary(tmp_path), workspace=str(tmp_path / "elsewhere"),
                   model="sonnet")
        _make_run_dir(tmp_path, "aaaa0012", prompt=_RESEARCH_GOAL, lane="now")
        shadow_lane.sweep(limit=5)
        cmd = calls[0]["cmd"]
        assert cmd[cmd.index("--model") + 1] == "sonnet"
        assert calls[0]["env_extra"]["MARO_GO_WORKSPACE"] == str(tmp_path / "elsewhere")

    def test_stamp_from_a_retired_reason_is_rescanned_but_a_real_claim_is_not(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        _go_config(tmp_path, _fake_binary(tmp_path), daily_cap=9)
        # The first cron ticks (2026-09-06) stamped build-shaped AGENDA
        # runs `worker_type!=research` before the widening landed.
        stale = _make_run_dir(tmp_path, "aaaa0020", prompt=_BUILD_GOAL, lane="agenda")
        (stale / "shadow-go").mkdir()
        (stale / "shadow-go" / "SKIPPED").write_text("worker_type!=research\n")
        # A stamp the CURRENT gate produces stays terminal.
        real = _make_run_dir(tmp_path, "aaaa0021", prompt=_BUILD_GOAL, lane="agenda", dry_run=True)
        (real / "shadow-go").mkdir()
        (real / "shadow-go" / "SKIPPED").write_text(shadow_lane.REASON_DRY_RUN + "\n")
        # A dir with anything beyond the stamp is a real claim, whatever the stamp says.
        claimed = _make_run_dir(tmp_path, "aaaa0022", prompt=_BUILD_GOAL, lane="agenda")
        (claimed / "shadow-go" / "scratch").mkdir(parents=True)
        (claimed / "shadow-go" / "SKIPPED").write_text("worker_type!=research\n")
        assert shadow_lane._stale_go_stamp(stale / "shadow-go")
        assert not shadow_lane._stale_go_stamp(real / "shadow-go")
        assert not shadow_lane._stale_go_stamp(claimed / "shadow-go")

        dry = shadow_lane.sweep(limit=5, dry_run=True)
        assert dry["go_would_fire"] == [] and (stale / "shadow-go" / "SKIPPED").is_file(), "dry-run retires nothing"
        result = shadow_lane.sweep(limit=5)
        assert result["go_fired"] == 1
        assert (stale / "shadow-go" / "meta.json").is_file()
        assert not (stale / "shadow-go" / "SKIPPED").exists()
        assert (real / "shadow-go" / "SKIPPED").is_file()
        assert not (claimed / "shadow-go" / "meta.json").exists()
        assert calls[0]["cmd"][-1] == _BUILD_GOAL and len(calls) == 2

    def test_parse_go_summary_skips_the_announcement(self):
        text = 'workspace: /x (env)\n{"handle": "ab", "usage": {}}\n'
        assert shadow_lane._parse_go_summary(text)["handle"] == "ab"
        assert shadow_lane._parse_go_summary("workspace: /x\n") == {}
        assert shadow_lane._parse_go_summary('{"not": "it"}\n{"handle": "cd"}')["handle"] == "cd"


class TestGoArmReadout:
    """What the Go row records beyond the run: clarifications and context."""

    def test_clarification_is_recorded_not_acted_on(self, tmp_path, monkeypatch):
        import llm
        calls = []
        summary = _go_summary(outcome="mission_failed(execution)", closure="unknown",
                              reason="needs clarification: What is the maro box?", result="")
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls, summary=summary))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        binary = _fake_binary(tmp_path)
        _go_config(tmp_path, binary)
        _operator_docs(tmp_path)
        rd = _make_run_dir(tmp_path, "aaaa0003", prompt=_BUILD_GOAL, lane="now",
                           extra={"model": "sonnet"})

        result = shadow_lane.sweep(limit=5)

        assert result["go_fired"] == 1 and len(calls) == 2
        meta = json.loads((rd / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["is_error"] is True and meta["go_outcome"] == "mission_failed(execution)"
        assert meta["go_needs_clarification"] is True
        assert meta["go_question"] == "What is the maro box?"
        assert meta["go_reason"] == "needs clarification: What is the maro box?"
        rows = [json.loads(l) for l in (tmp_path / "memory" / "shadow_ledger.jsonl").read_text().splitlines() if l.strip()]
        assert len(rows) == 1 and rows[0]["go_needs_clarification"] is True
        assert rows[0]["go_question"] == "What is the maro box?"
        # nothing answered the question: the arm asks nobody
        assert (rd / "shadow-go" / "RESULT.md").read_text(encoding="utf-8") == ""

    def test_no_operator_docs_means_no_context_flag(self, tmp_path, monkeypatch):
        import config
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        monkeypatch.setattr(config, "user_file", lambda name: None)
        binary = _fake_binary(tmp_path)
        _go_config(tmp_path, binary)
        rd = _make_run_dir(tmp_path, "aaaa0004", prompt=_BUILD_GOAL, lane="now",
                           extra={"model": "sonnet"})

        result = shadow_lane.sweep(limit=5)

        assert result["go_fired"] == 1
        cmd = calls[0]["cmd"]
        assert "--context" not in cmd and cmd[-1] == _BUILD_GOAL
        assert not (rd / "shadow-go" / "context.md").exists()
        meta = json.loads((rd / "shadow-go" / "meta.json").read_text(encoding="utf-8"))
        assert meta["context_docs"] == [] and meta["context_sha256"] is None and meta["context_chars"] == 0

    def test_operator_context_mirrors_the_planner(self, tmp_path, monkeypatch):
        """Same docs, same order, same block shape, same breaker as planner.py."""
        import config
        user = tmp_path / "user"
        user.mkdir()
        (user / "GOALS.md").write_text("goals here\n", encoding="utf-8")
        (user / "SIGNALS.md").write_text("x" * 5000, encoding="utf-8")
        monkeypatch.setattr(config, "user_file",
                            lambda name: (user / name) if (user / name).exists() else None)
        text, docs = shadow_lane._operator_context()
        assert docs == ["GOALS.md", "SIGNALS.md"]
        assert text.startswith("USER CONTEXT (GOALS.md):\ngoals here\n\nUSER CONTEXT (SIGNALS.md):\n")
        from context_budget import clip
        assert text.endswith(clip("x" * 5000, shadow_lane.GO_CONTEXT_CAP))
        assert len(text) < 5000 + 200, "the breaker clipped the runaway doc"


def _go_row(**over):
    row = {
        "handle_id": "aaaa0009", "arm": "go", "primary_lane": "now", "primary_goal_achieved": True,
        "primary_goal_shape": {"worker_type": "build", "action_tier": "READ"},
        "primary_cost_usd": 2.0, "primary_wall_seconds": 400.0, "primary_model": "sonnet",
        "cost_usd": 0.02, "wall_seconds": 20.0, "tokens_in": 300, "tokens_out": 40, "tokens_cached": 120,
        "model": "haiku", "exit_status": "ok", "is_error": True,
        "go_outcome": "mission_failed(execution)", "go_needs_clarification": True,
        "go_question": "What is the maro box?", "go_landscape": {"relation": "fresh", "chosen": "", "rule": "no_candidates"},
        "context_docs": ["CONTEXT.md"], "go_binary_sha256": "5f900c6a" + "0" * 56,
        "ts": "2026-09-06T10:00:00+00:00",
    }
    row.update(over)
    return row


class TestPairs:
    """The adjudication's reader: one view per ledger row, the partition
    the pre-registered questions need, and no judgement of agreement."""

    def test_go_row_view_carries_both_sides_and_the_ratios(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        rd = _make_run_dir(tmp_path, "aaaa0009", prompt="what is the maro box", lane="now")
        (rd / "build").mkdir()
        (rd / "build" / "now-aaaa0009-report.html").write_text("<html/>")
        (rd / "shadow-go").mkdir()
        (rd / "shadow-go" / "RESULT.md").write_text("Which box do you mean?", encoding="utf-8")
        v = shadow_lane.pair_view(_go_row())
        assert v["handle_id"] == "aaaa0009" and v["arm"] == "go" and v["lane"] == "now"
        assert v["shape"] == "build/READ"
        assert v["primary"] == {"achieved": True, "cost_usd": 2.0, "wall_seconds": 400.0, "model": "sonnet"}
        c = v["challenger"]
        assert c["outcome"] == "mission_failed(execution)" and c["asked"] is True
        assert c["question"] == "What is the maro box?" and c["landscape"] == "fresh"
        assert c["tokens_cached"] == 120 and c["context_docs"] == ["CONTEXT.md"] and c["binary"] == "5f900c6a"
        assert v["cost_ratio"] == 0.01 and v["wall_ratio"] == 0.05
        assert v["run_dir"] == rd.name and v["report"] == f"{rd.name}/build/now-aaaa0009-report.html"
        assert v["result_excerpt"] == "Which box do you mean?"

    def test_star_row_view_and_missing_sources_are_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        row = {"handle_id": "bbbb0001", "arm": "star", "primary_lane": "agenda",
               "primary_goal_achieved": True, "primary_cost_usd": None, "primary_wall_seconds": 100.0,
               "cost_usd": 1.5, "wall_seconds": 50.0, "exit_status": "ok", "is_error": False,
               "ts": "2026-09-06T09:00:00+00:00"}
        v = shadow_lane.pair_view(row)
        assert v["shape"] == "?" and v["challenger"]["outcome"] == "ok" and v["challenger"]["asked"] is False
        assert v["cost_ratio"] is None and v["wall_ratio"] == 0.5
        assert v["run_dir"] is None and v["report"] is None and v["result_excerpt"] is None
        err = shadow_lane.pair_view(dict(row, is_error=True))
        assert err["challenger"]["outcome"] == "error"
        # a free primary is not a denominator: ratio None, never a crash
        free = shadow_lane.pair_view(dict(row, primary_cost_usd=0.0, primary_wall_seconds=0))
        assert free["cost_ratio"] is None and free["wall_ratio"] is None

    def test_summary_partitions_and_never_judges_agreement(self):
        rows = [_go_row(), _go_row(handle_id="aaaa0010", go_needs_clarification=False, go_question=None,
                                   go_outcome="delivered", is_error=False, cost_usd=0.5,
                                   primary_goal_shape={"worker_type": "research", "action_tier": "READ"}),
                {"handle_id": "bbbb0001", "arm": "star", "primary_goal_achieved": False, "cost_usd": 1.0,
                 "primary_cost_usd": 2.0, "exit_status": "ok", "is_error": False, "ts": "2026-09-05T00:00:00+00:00"}]
        views, summary = shadow_lane.pairs(rows)
        assert [v["handle_id"] for v in views] == ["bbbb0001", "aaaa0010", "aaaa0009"], "newest first"
        assert summary["rows"] == 3 and summary["per_arm"] == {"go": 2, "star": 1}
        assert summary["per_shape"] == {"build/READ": 1, "research/READ": 1, "?": 1}
        assert summary["challenger_outcomes"] == {"mission_failed(execution)": 1, "delivered": 1, "ok": 1}
        assert summary["asked"] == 1 and summary["primary_achieved"] == 2
        assert summary["cost"]["paired"] == 3 and summary["cost"]["median_ratio"] == 0.25
        assert summary["cost"]["challenger_usd"] == 1.52 and summary["cost"]["primary_usd"] == 6.0
        assert summary["wall"]["paired"] == 2 and summary["agreement"] is None
        only_go, s2 = shadow_lane.pairs(rows, arm="go")
        assert len(only_go) == 2 and s2["per_arm"] == {"go": 2}

    def test_cli_pairs_text_and_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        (tmp_path / "memory").mkdir()
        (tmp_path / "memory" / "shadow_ledger.jsonl").write_text(
            json.dumps(_go_row()) + "\n" + "not json\n" + json.dumps(_go_row(handle_id="aaaa0011")) + "\n")
        assert shadow_lane.main(["pairs"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("2 pair(s)") and "aaaa0011" in out and "ASKED: What is the maro box?" in out
        assert "agreement: (batch judge" in out
        assert shadow_lane.main(["pairs", "--json", "--arm", "go"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["summary"]["rows"] == 2 and data["pairs"][0]["handle_id"] == "aaaa0011"
        assert shadow_lane.main(["pairs", "--arm", "plain"]) == 0
        assert capsys.readouterr().out.startswith("0 pair(s)")

    def test_sweep_refreshes_the_pairs_page_after_a_row(self, tmp_path, monkeypatch):
        import llm
        calls = []
        monkeypatch.setattr(llm, "_run_subprocess_safe", _fake_go_engine(calls))
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger([]))
        refreshed = []
        monkeypatch.setattr(shadow_lane, "_refresh_pairs_page", lambda: refreshed.append(1))
        binary = _fake_binary(tmp_path)
        _go_config(tmp_path, binary)
        _make_run_dir(tmp_path, "aaaa0012", prompt=_BUILD_GOAL, lane="now", extra={"model": "sonnet"})
        assert shadow_lane.sweep(limit=5)["go_fired"] == 1
        assert refreshed == [1], "the page tracks the ledger: refreshed once per appended row"

    def test_refresh_never_raises(self, monkeypatch):
        import loop_report
        def _boom(root=None):
            raise RuntimeError("disk gone")
        monkeypatch.setattr(loop_report, "write_pairs_page", _boom)
        shadow_lane._refresh_pairs_page()  # a page is a view; the row is the record
