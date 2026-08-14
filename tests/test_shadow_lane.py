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
                    "MARO_ORCH_ROOT", "MARO_MEMORY_DIR", "MARO_FUTURE_UNKNOWN_VAR",
                    "MARO_WORKER_RUN"):
            assert key in env_extra and env_extra[key] is None, key

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

        def _boom(run_dir, arm, goal, *, timeout):
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

    def test_star_prompt_read_once_and_handed_down(self, tmp_path, monkeypatch):
        """r2 both lenses: the sweep's preflight star_prompt() plus
        run_challenger's own re-read was a TOCTOU — a failure between the
        two terminally claimed the slot. Pin: exactly ONE read per fire,
        and the challenger uses the handed-down payload."""
        _set_shadow_config(tmp_path, enabled=True)
        hid = next(h for h in ("cccc3333", "dddd4444", "eeee5555", "ffff6666",
                               "12121212", "34343434")
                   if shadow_lane.pick_arm(h) == shadow_lane.ARM_STAR)
        _make_run_dir(tmp_path, hid, prompt=_RESEARCH_GOAL)

        calls = {"n": 0}
        real_star = shadow_lane.star_prompt

        def _counting_star():
            calls["n"] += 1
            return real_star()

        received = {}

        def _fake_challenger(run_dir, arm, goal, *, timeout, star=None):
            received["star"] = star
            return {"arm": arm, "started_at": "2026-08-14T00:00:00+00:00",
                    "wall_seconds": 1.0, "exit_status": "ok"}

        monkeypatch.setattr(shadow_lane, "star_prompt", _counting_star)
        monkeypatch.setattr(shadow_lane, "run_challenger", _fake_challenger)
        result = shadow_lane.sweep(limit=1)
        assert result["fired"] == 1
        assert calls["n"] == 1
        assert received["star"] is not None
        text, meta = received["star"]
        assert "star" in text and meta["star_version"]
