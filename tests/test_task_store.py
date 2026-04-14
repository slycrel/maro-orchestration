"""Tests for task_store.py — file-per-task JSON store with locking."""

import json
import os
import pytest

import task_store


@pytest.fixture(autouse=True)
def _use_tmp_workspace(tmp_path, monkeypatch):
    """Point task_store at a temp directory for every test."""
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_utc_now_format(self):
        ts = task_store.utc_now()
        assert ts.endswith("Z")
        assert "T" in ts

    def test_new_job_id_format(self):
        jid = task_store.new_job_id()
        assert jid.startswith("task-")
        assert "T" in jid  # timestamp

    def test_new_job_id_unique(self):
        ids = {task_store.new_job_id() for _ in range(100)}
        assert len(ids) == 100

    def test_make_task_defaults(self):
        t = task_store.make_task("j1")
        assert t["job_id"] == "j1"
        assert t["status"] == "queued"
        assert t["lane"] == "now"
        assert t["attempt"] == 0
        assert t["blocked_by"] == []
        assert t["continuation_depth"] == 0

    def test_make_task_with_args(self):
        t = task_store.make_task("j2", lane="agenda", source="test",
                                 reason="because", blocked_by=["j1"],
                                 continuation_depth=3)
        assert t["lane"] == "agenda"
        assert t["source"] == "test"
        assert t["reason"] == "because"
        assert t["blocked_by"] == ["j1"]
        assert t["continuation_depth"] == 3


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:
    def test_enqueue_creates_file(self):
        t = task_store.enqueue(reason="test task")
        path = task_store.task_path(t["job_id"])
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["status"] == "queued"
        assert data["reason"] == "test task"

    def test_enqueue_custom_job_id(self):
        t = task_store.enqueue(job_id="custom-123", reason="x")
        assert t["job_id"] == "custom-123"
        assert task_store.task_path("custom-123").exists()

    def test_enqueue_with_blocked_by(self):
        dep = task_store.enqueue(job_id="dep-1")
        t = task_store.enqueue(job_id="child-1", blocked_by=["dep-1"])
        assert t["blocked_by"] == ["dep-1"]

    def test_enqueue_continuation_depth(self):
        t = task_store.enqueue(continuation_depth=2)
        assert t["continuation_depth"] == 2


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------

class TestClaim:
    def test_claim_queued_task(self):
        t = task_store.enqueue(job_id="c1")
        claimed = task_store.claim("c1")
        assert claimed["status"] == "claimed"
        assert claimed["attempt"] == 1
        assert claimed["claimed_by_pid"] == os.getpid()
        assert claimed["timestamps"]["claimed_at_utc"]

    def test_claim_already_claimed_raises(self):
        task_store.enqueue(job_id="c2")
        task_store.claim("c2")
        with pytest.raises(RuntimeError, match="already claimed"):
            task_store.claim("c2")

    def test_claim_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            task_store.claim("nonexistent")

    def test_claim_done_task_raises(self):
        task_store.enqueue(job_id="c3")
        task_store.claim("c3")
        task_store.complete("c3")
        with pytest.raises(RuntimeError, match="expected 'queued'"):
            task_store.claim("c3")

    def test_claim_blocked_task_raises(self):
        task_store.enqueue(job_id="blocker")
        task_store.enqueue(job_id="blocked", blocked_by=["blocker"])
        with pytest.raises(RuntimeError, match="blocked by blocker"):
            task_store.claim("blocked")

    def test_claim_unblocked_after_dep_done(self):
        task_store.enqueue(job_id="dep")
        task_store.enqueue(job_id="waiter", blocked_by=["dep"])
        task_store.claim("dep")
        task_store.complete("dep")
        # Now waiter should be claimable
        claimed = task_store.claim("waiter")
        assert claimed["status"] == "claimed"

    def test_claim_increments_attempt(self):
        task_store.enqueue(job_id="retry")
        task_store.claim("retry")
        task_store.fail("retry")
        # Re-enqueue to queued status manually for retry
        path = task_store.task_path("retry")
        data = json.loads(path.read_text())
        data["status"] = "queued"
        data["claimed_by_pid"] = None
        path.write_text(json.dumps(data))
        claimed = task_store.claim("retry")
        assert claimed["attempt"] == 2


# ---------------------------------------------------------------------------
# Stale claim recovery
# ---------------------------------------------------------------------------

class TestStaleClaims:
    def test_stale_claim_recovered_on_claim(self):
        """If a claimed task's PID is dead, claiming it should work (recovery)."""
        task_store.enqueue(job_id="stale")
        # Manually set claimed by a dead PID
        path = task_store.task_path("stale")
        data = json.loads(path.read_text())
        data["status"] = "claimed"
        data["claimed_by_pid"] = 999999999  # Very unlikely to be alive
        path.write_text(json.dumps(data))
        # Should auto-recover the stale claim and re-claim
        claimed = task_store.claim("stale")
        assert claimed["status"] == "claimed"
        assert claimed["claimed_by_pid"] == os.getpid()

    def test_recover_stale_claims_batch(self):
        task_store.enqueue(job_id="s1")
        task_store.enqueue(job_id="s2")
        # Mark both as claimed by dead PIDs
        for jid in ("s1", "s2"):
            path = task_store.task_path(jid)
            data = json.loads(path.read_text())
            data["status"] = "claimed"
            data["claimed_by_pid"] = 999999999
            path.write_text(json.dumps(data))
        recovered = task_store.recover_stale_claims()
        assert set(recovered) == {"s1", "s2"}
        # Both should be queued now
        for jid in ("s1", "s2"):
            data = json.loads(task_store.task_path(jid).read_text())
            assert data["status"] == "queued"

    def test_live_claim_not_recovered(self):
        task_store.enqueue(job_id="live")
        task_store.claim("live")  # claimed by current PID
        recovered = task_store.recover_stale_claims()
        assert "live" not in recovered


# ---------------------------------------------------------------------------
# Complete / Fail
# ---------------------------------------------------------------------------

class TestComplete:
    def test_complete_claimed_task(self):
        task_store.enqueue(job_id="d1")
        task_store.claim("d1")
        t = task_store.complete("d1")
        assert t["status"] == "done"
        assert t["timestamps"]["finished_at_utc"]
        assert t["claimed_by_pid"] is None

    def test_complete_with_artifacts(self):
        task_store.enqueue(job_id="d2")
        task_store.claim("d2")
        t = task_store.complete("d2", artifact_paths={"report": "/tmp/report.md"})
        assert t["artifact_paths"]["report"] == "/tmp/report.md"

    def test_complete_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            task_store.complete("ghost")

    def test_complete_already_done_raises(self):
        task_store.enqueue(job_id="d3")
        task_store.claim("d3")
        task_store.complete("d3")
        with pytest.raises(RuntimeError, match="cannot complete"):
            task_store.complete("d3")


class TestFail:
    def test_fail_task(self):
        task_store.enqueue(job_id="f1")
        task_store.claim("f1")
        t = task_store.fail("f1", error="boom")
        assert t["status"] == "failed"
        assert t["error"] == "boom"

    def test_fail_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            task_store.fail("ghost")


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------

class TestDependencies:
    def test_resolve_dependents_on_complete(self):
        task_store.enqueue(job_id="parent")
        task_store.enqueue(job_id="child", blocked_by=["parent"])
        # Child is blocked
        data = json.loads(task_store.task_path("child").read_text())
        assert data["blocked_by"] == ["parent"]
        # Complete parent
        task_store.claim("parent")
        task_store.complete("parent")
        # Child should have blocked_by cleared
        data = json.loads(task_store.task_path("child").read_text())
        assert data["blocked_by"] == []

    def test_multi_dependency_partial_resolve(self):
        task_store.enqueue(job_id="a")
        task_store.enqueue(job_id="b")
        task_store.enqueue(job_id="c", blocked_by=["a", "b"])
        # Complete only a
        task_store.claim("a")
        task_store.complete("a")
        data = json.loads(task_store.task_path("c").read_text())
        assert data["blocked_by"] == ["b"]
        # c still blocked
        with pytest.raises(RuntimeError, match="blocked by b"):
            task_store.claim("c")
        # Complete b
        task_store.claim("b")
        task_store.complete("b")
        # Now c is claimable
        claimed = task_store.claim("c")
        assert claimed["status"] == "claimed"

    def test_cycle_detection(self):
        task_store.enqueue(job_id="x")
        task_store.enqueue(job_id="y", blocked_by=["x"])
        # x2→y→x is a linear chain, NOT a cycle — should succeed
        task_store.enqueue(job_id="x2", blocked_by=["y"])

        # Real cycle: ca→cb→ca (manually created)
        task_store.enqueue(job_id="ca")
        task_store.enqueue(job_id="cb", blocked_by=["ca"])
        # Manually make ca depend on cb to create cycle
        path = task_store.task_path("ca")
        data = json.loads(path.read_text())
        data["blocked_by"] = ["cb"]
        path.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="cycle"):
            task_store.enqueue(job_id="cc", blocked_by=["ca"])

    def test_sequential_chain_is_not_cycle(self):
        """A→B→C sequential chain must not trip cycle detection."""
        task_store.enqueue(job_id="chain_a")
        task_store.enqueue(job_id="chain_b", blocked_by=["chain_a"])
        task_store.enqueue(job_id="chain_c", blocked_by=["chain_b"])
        # All three should exist without error
        tasks = {t["job_id"] for t in task_store.list_tasks()}
        assert {"chain_a", "chain_b", "chain_c"}.issubset(tasks)


# ---------------------------------------------------------------------------
# List / Status / Archive
# ---------------------------------------------------------------------------

class TestListAndArchive:
    def test_list_all_tasks(self):
        task_store.enqueue(job_id="l1")
        task_store.enqueue(job_id="l2")
        tasks = task_store.list_tasks()
        ids = {t["job_id"] for t in tasks}
        assert "l1" in ids
        assert "l2" in ids

    def test_list_filter_by_status(self):
        task_store.enqueue(job_id="lf1")
        task_store.enqueue(job_id="lf2")
        task_store.claim("lf1")
        queued = task_store.list_tasks(status_filter="queued")
        claimed = task_store.list_tasks(status_filter="claimed")
        assert any(t["job_id"] == "lf2" for t in queued)
        assert any(t["job_id"] == "lf1" for t in claimed)
        assert not any(t["job_id"] == "lf1" for t in queued)

    def test_status_summary(self):
        task_store.enqueue(job_id="ss1")
        task_store.enqueue(job_id="ss2")
        task_store.claim("ss1")
        counts = task_store.status_summary()
        assert counts.get("queued", 0) >= 1
        assert counts.get("claimed", 0) >= 1

    def test_archive_done_task(self):
        task_store.enqueue(job_id="a1")
        task_store.claim("a1")
        task_store.complete("a1")
        t = task_store.archive("a1")
        assert t["status"] == "archived"
        # Original file should be gone
        assert not task_store.task_path("a1").exists()

    def test_archive_queued_raises(self):
        task_store.enqueue(job_id="a2")
        with pytest.raises(RuntimeError, match="can only archive"):
            task_store.archive("a2")

    def test_archive_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            task_store.archive("ghost")


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_atomic_write_creates_file(self, tmp_path):
        p = tmp_path / "sub" / "test.json"
        task_store._atomic_write(p, {"key": "value"})
        assert p.exists()
        assert json.loads(p.read_text()) == {"key": "value"}

    def test_atomic_write_overwrites(self, tmp_path):
        p = tmp_path / "test.json"
        task_store._atomic_write(p, {"v": 1})
        task_store._atomic_write(p, {"v": 2})
        assert json.loads(p.read_text()) == {"v": 2}


# ---------------------------------------------------------------------------
# Concurrency — fcntl locking correctness
# ---------------------------------------------------------------------------
#
# Session 20 adversarial review finding 3.7: fcntl locking had no concurrent
# tests. These exercise the lock under both threaded and multiprocess racing
# to make sure `claim()` is linearizable and `enqueue()` is safe under
# concurrent writes to different tasks.

import multiprocessing
import threading


def _worker_claim(workspace: str, job_id: str, result_list, hold_secs: float = 0.0) -> None:
    """Subprocess entry: set POE_WORKSPACE, attempt to claim, append result.

    If hold_secs > 0, sleep that long after a successful claim — keeps the
    worker PID alive so the peer's stale-claim-recovery path doesn't trigger.
    """
    import os as _os
    _os.environ["POE_WORKSPACE"] = workspace
    # Force re-import under the new env so tasks_dir() resolves correctly.
    import importlib
    import time as _time
    import task_store as _ts
    importlib.reload(_ts)
    try:
        t = _ts.claim(job_id)
        result_list.append(("claimed", t.get("claimed_by_pid")))
        if hold_secs > 0:
            _time.sleep(hold_secs)
    except RuntimeError as exc:
        result_list.append(("rejected", str(exc)))
    except Exception as exc:  # unexpected
        result_list.append(("error", repr(exc)))


def _worker_enqueue(workspace: str, job_id: str, result_list) -> None:
    import os as _os
    _os.environ["POE_WORKSPACE"] = workspace
    import importlib
    import task_store as _ts
    importlib.reload(_ts)
    try:
        _ts.enqueue(job_id=job_id)
        result_list.append(("enqueued", job_id))
    except Exception as exc:
        result_list.append(("error", repr(exc)))


class TestConcurrency:
    def test_threaded_claim_race_only_one_winner(self, tmp_path, monkeypatch):
        """10 threads race to claim the same task — exactly one wins.

        All threads share a process id, so after the first claim succeeds the
        `claim()` stale-recovery check sees a live PID and raises on the others.
        This validates the exclusive-lock path is actually serializing claims.
        """
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
        task_store.enqueue(job_id="race-1")

        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(10)

        def _thread_entry():
            barrier.wait()  # maximize contention by releasing all threads at once
            try:
                t = task_store.claim("race-1")
                with results_lock:
                    results.append(("claimed", t["claimed_by_pid"]))
            except RuntimeError as exc:
                with results_lock:
                    results.append(("rejected", str(exc)))

        threads = [threading.Thread(target=_thread_entry) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        claimed = [r for r in results if r[0] == "claimed"]
        rejected = [r for r in results if r[0] == "rejected"]
        assert len(claimed) == 1, f"expected 1 winner, got {len(claimed)}: {results}"
        assert len(rejected) == 9, f"expected 9 rejections, got {len(rejected)}"

        # Verify the final on-disk state matches the winner
        task = task_store._read_task(task_store.task_path("race-1"))
        assert task["status"] == "claimed"
        assert task["claimed_by_pid"] == claimed[0][1]

    def test_multiprocess_claim_race_only_one_winner(self, tmp_path):
        """Two real subprocesses race to claim. Only one succeeds.

        fcntl is process-level — this is the honest cross-process test. Both
        workers hold for 2s after a successful claim so the peer's claim
        attempt hits while the winner is still alive (otherwise stale-claim
        recovery correctly hands the task to the second worker).
        """
        import os as _os
        orig_ws = _os.environ.get("POE_WORKSPACE")
        _os.environ["POE_WORKSPACE"] = str(tmp_path)
        try:
            import importlib
            importlib.reload(task_store)
            task_store.enqueue(job_id="mp-race")

            mgr = multiprocessing.Manager()
            results = mgr.list()
            p1 = multiprocessing.Process(target=_worker_claim,
                                         args=(str(tmp_path), "mp-race", results, 2.0))
            p2 = multiprocessing.Process(target=_worker_claim,
                                         args=(str(tmp_path), "mp-race", results, 2.0))
            p1.start(); p2.start()
            p1.join(timeout=30); p2.join(timeout=30)
            assert p1.exitcode == 0 and p2.exitcode == 0, f"subprocess crashed: {list(results)}"

            outcomes = list(results)
            claimed = [r for r in outcomes if r[0] == "claimed"]
            rejected = [r for r in outcomes if r[0] == "rejected"]
            errors = [r for r in outcomes if r[0] == "error"]
            assert errors == [], f"unexpected errors: {errors}"
            assert len(claimed) == 1, f"expected 1 winner, got {len(claimed)}: {outcomes}"
            assert len(rejected) == 1
        finally:
            if orig_ws is None:
                _os.environ.pop("POE_WORKSPACE", None)
            else:
                _os.environ["POE_WORKSPACE"] = orig_ws

    def test_multiprocess_stale_claim_recovery(self, tmp_path):
        """When a prior claimer exits without completing, the next claimer
        recovers the task. This is the intentional stale-recovery path — it
        should succeed, not be treated as a double-claim bug.
        """
        import os as _os
        orig_ws = _os.environ.get("POE_WORKSPACE")
        _os.environ["POE_WORKSPACE"] = str(tmp_path)
        try:
            import importlib
            importlib.reload(task_store)
            task_store.enqueue(job_id="stale-1")

            mgr = multiprocessing.Manager()
            results = mgr.list()
            # First worker claims and exits immediately (no hold).
            p1 = multiprocessing.Process(target=_worker_claim,
                                         args=(str(tmp_path), "stale-1", results, 0.0))
            p1.start(); p1.join(timeout=30)
            assert p1.exitcode == 0

            # Second worker should see the dead PID and recover.
            p2 = multiprocessing.Process(target=_worker_claim,
                                         args=(str(tmp_path), "stale-1", results, 0.0))
            p2.start(); p2.join(timeout=30)
            assert p2.exitcode == 0

            outcomes = list(results)
            claimed = [r for r in outcomes if r[0] == "claimed"]
            assert len(claimed) == 2, f"expected both workers to claim (stale recovery): {outcomes}"
            # The two claims should have different PIDs
            pids = [r[1] for r in claimed]
            assert pids[0] != pids[1]
        finally:
            if orig_ws is None:
                _os.environ.pop("POE_WORKSPACE", None)
            else:
                _os.environ["POE_WORKSPACE"] = orig_ws

    def test_concurrent_enqueue_of_different_tasks_all_succeed(self, tmp_path, monkeypatch):
        """Writing to 20 different task files concurrently must produce 20
        valid JSON files with no corruption. Per-file locks mean there's no
        contention between different job_ids — this validates we aren't
        accidentally sharing a lock path.
        """
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        job_ids = [f"concurrent-{i:03d}" for i in range(20)]
        results: list = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(len(job_ids))

        def _thread_entry(jid: str):
            barrier.wait()
            try:
                task_store.enqueue(job_id=jid)
                with results_lock:
                    results.append(("ok", jid))
            except Exception as exc:
                with results_lock:
                    results.append(("error", repr(exc)))

        threads = [threading.Thread(target=_thread_entry, args=(j,)) for j in job_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        errors = [r for r in results if r[0] == "error"]
        assert errors == [], f"concurrent enqueue errors: {errors}"
        assert len(results) == len(job_ids)

        # Every file exists and is parseable JSON with the expected job_id
        for jid in job_ids:
            task = task_store._read_task(task_store.task_path(jid))
            assert task is not None, f"missing task {jid}"
            assert task["job_id"] == jid
            assert task["status"] == "queued"

    def test_serialized_claim_then_complete_is_consistent(self, tmp_path, monkeypatch):
        """Interleaved claim + complete operations on the same task must
        serialize correctly — no half-written state visible between them.
        """
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
        task_store.enqueue(job_id="serial-1")
        task_store.claim("serial-1")

        # Concurrent complete + list must produce consistent state:
        # list() never sees a torn task file.
        results: list = []
        results_lock = threading.Lock()

        def _completer():
            try:
                task_store.complete("serial-1", artifact_paths={"out": "/tmp/x"})
                with results_lock:
                    results.append(("complete", True))
            except Exception as exc:
                with results_lock:
                    results.append(("complete", repr(exc)))

        def _reader():
            # Read 50 times in a tight loop while complete runs.
            tear_detected = False
            for _ in range(50):
                try:
                    t = task_store._read_task(task_store.task_path("serial-1"))
                    if t is None:
                        continue
                    # Every read must produce a coherent status — no partial writes.
                    assert t["status"] in ("claimed", "done"), f"torn status: {t['status']}"
                except (json.JSONDecodeError, AssertionError):
                    tear_detected = True
                    break
            with results_lock:
                results.append(("read", tear_detected))

        t_complete = threading.Thread(target=_completer)
        t_reader = threading.Thread(target=_reader)
        t_complete.start(); t_reader.start()
        t_complete.join(); t_reader.join()

        assert ("complete", True) in results
        read_results = [r for r in results if r[0] == "read"]
        assert read_results and read_results[0][1] is False, "reader saw torn write"
