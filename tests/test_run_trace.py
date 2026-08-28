from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import run_trace
import runs
from loop_types import LoopPhase, LoopStateMachine


@pytest.fixture()
def rd(tmp_path):
    (tmp_path / "build").mkdir()
    return tmp_path


def _rows(rd: Path):
    return run_trace.read_trace(rd)


def test_edge_is_recorded_with_loop_id_and_attrs(rd):
    assert run_trace.record_edge("exec.blocked", "exec.retry",
                                 loop_id="L1", run_dir=rd, step_idx=4)
    rows = _rows(rd)
    assert len(rows) == 1
    assert rows[0]["from"] == "exec.blocked"
    assert rows[0]["to"] == "exec.retry"
    assert rows[0]["loop_id"] == "L1"
    assert rows[0]["attrs"]["step_idx"] == 4
    assert rows[0]["ts"]


def test_append_order_is_the_record(rd):
    """There is deliberately no sequence number: file order is the ordering,
    because the answer-first split runs close-out before closure and a trace
    written in source order would record a false sequence."""
    run_trace.record_edge("close.curate", "close.terminal", run_dir=rd)
    run_trace.record_edge("verify.plan", "verify.closure", run_dir=rd)
    assert [(r["from"], r["to"]) for r in _rows(rd)] == [
        ("close.curate", "close.terminal"),
        ("verify.plan", "verify.closure"),
    ]


def test_record_path_writes_consecutive_edges(rd):
    n = run_trace.record_path(
        ["phase.init", "phase.decompose", "phase.pre_flight"], run_dir=rd)
    assert n == 2
    assert [(r["from"], r["to"]) for r in _rows(rd)] == [
        ("phase.init", "phase.decompose"),
        ("phase.decompose", "phase.pre_flight"),
    ]


def test_unknown_node_is_recorded_but_flagged(rd):
    """Losing the row would be worse than recording an unrecognised id, but an
    unflagged typo would invent a phantom node downstream."""
    run_trace.record_edge("exec.step", "bogus.node", run_dir=rd)
    row = _rows(rd)[0]
    assert row["to"] == "bogus.node"
    assert row["unknown_node"] == ["bogus.node"]


def test_every_declared_node_is_namespaced_by_phase():
    """The vocabulary is a contract with the atlas; a bare id would not map."""
    for node in run_trace.NODES:
        assert "." in node, node


def test_missing_run_context_is_counted_not_raised(monkeypatch, rd):
    monkeypatch.setattr(runs, "current_run_dir", lambda: None)
    before = run_trace.dropped_count()
    assert run_trace.record_edge("exec.step", "fin.result") is False
    assert run_trace.dropped_count() > before


def test_write_failure_leaves_a_degraded_marker(rd, monkeypatch):
    """A silently dropped edge reads downstream as 'not traveled' -- a false
    negative that looks like a fact. The trace must say it is incomplete."""
    calls = {"n": 0}
    real_append = None
    import file_lock

    def flaky(path, line, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_append(path, line, **kw)

    real_append = file_lock.locked_append
    monkeypatch.setattr(file_lock, "locked_append", flaky)

    assert run_trace.record_edge("exec.step", "fin.result", run_dir=rd) is False
    rows = _rows(rd)
    assert any(r["from"] == "trace.degraded" for r in rows), rows
    assert run_trace.dropped_count(rd) >= 1


def test_recording_never_raises_even_when_everything_fails(rd, monkeypatch):
    import file_lock
    monkeypatch.setattr(file_lock, "locked_append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    # must not raise, including the degraded-marker attempt
    assert run_trace.record_edge("exec.step", "fin.result", run_dir=rd) is False


def test_disabled_by_config_writes_nothing(rd, monkeypatch):
    import config
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: False if key == "trace.enabled" else default)
    assert run_trace.record_edge("exec.step", "fin.result", run_dir=rd) is False
    assert _rows(rd) == []


def test_byte_tainted_row_is_skipped_not_laundered(rd):
    """loads_clean doctrine: a byte-tainted line stays announced-as-lost rather
    than being re-serialized into legitimate-looking content."""
    p = rd / "build" / run_trace.TRACE_FILENAME
    run_trace.record_edge("exec.step", "fin.result", run_dir=rd)
    with p.open("ab") as fh:
        fh.write(b'{"from": "exec.step", "to": "\xff\xfe bad"}\n')
    run_trace.record_edge("fin.result", "verify.plan", run_dir=rd)

    rows = _rows(rd)
    assert [(r["from"], r["to"]) for r in rows] == [
        ("exec.step", "fin.result"),
        ("fin.result", "verify.plan"),
    ]


def test_read_trace_on_missing_file_is_empty(tmp_path):
    assert run_trace.read_trace(tmp_path) == []


# ---------------------------------------------------------------- phase track

def test_phase_transitions_are_persisted(rd):
    """The whole point: LoopPhase was in-memory only, so the phase track could
    never be replayed from a run dir."""
    with runs.scoped_run_dir(rd):
        ctx = LoopStateMachine(loop_id="L9", goal="g", project="p")
        ctx.set_phase(LoopPhase.DECOMPOSE)
        ctx.set_phase(LoopPhase.PRE_FLIGHT)
        ctx.set_phase(LoopPhase.PREPARE)
        ctx.set_phase(LoopPhase.EXECUTE)
        ctx.set_phase(LoopPhase.FINALIZE)

    assert [(r["from"], r["to"]) for r in _rows(rd)] == [
        ("phase.init", "phase.decompose"),
        ("phase.decompose", "phase.pre_flight"),
        ("phase.pre_flight", "phase.prepare"),
        ("phase.prepare", "phase.execute"),
        ("phase.execute", "phase.finalize"),
    ]
    assert all(r["loop_id"] == "L9" for r in _rows(rd))


def test_phase_node_ids_cover_every_loop_phase():
    """A new LoopPhase constant must not silently become an unknown node."""
    declared = {
        v for k, v in vars(LoopPhase).items()
        if not k.startswith("_") and isinstance(v, str)
    }
    assert {f"phase.{p}" for p in declared} <= run_trace.PHASE_NODES


def test_term_vocabulary_covers_every_success_class():
    """run_curation.classify_outcome's ladder is the source of truth for
    terminals; a class it can emit but the vocabulary lacks would show up as an
    unknown node in every consumer."""
    import inspect
    import run_curation
    src = inspect.getsource(run_curation.classify_outcome)
    emitted = set(re.findall(r'success_class\s*=\s*["\']([a-z-]+)["\']', src))
    emitted |= set(re.findall(r'return\s+["\']([a-z-]+)["\']', src))
    missing = {c for c in emitted if f"term.{c}" not in run_trace.TERM_NODES}
    assert not missing, f"success_class values with no term node: {missing}"


def test_recorder_and_atlas_share_one_vocabulary():
    """The node ids are a contract between src/run_trace.py and the atlas
    topology. Drift either way is silent: a recorded node the map lacks never
    renders, and a mapped node the recorder cannot emit reads as a path no run
    ever takes."""
    root = Path(__file__).resolve().parents[1]
    html = (root / "scripts" / "run_atlas" / "template.html").read_text()
    nodes_block = html.split("const PHASES")[1].split("const EDGES")[0]
    declared = set(re.findall(r'\["([a-z_]+\.[a-z_\-]+)"', nodes_block))
    edges_block = html.split("const EDGES")[1].split("const NODEMETA")[0]
    edge_ids = set(re.findall(r'"([a-z_]+\.[a-z_\-]+)"', edges_block))

    assert not (edge_ids - declared), \
        f"edges reference undeclared nodes: {sorted(edge_ids - declared)}"
    # term.* and trace.* are recorder-side only (the atlas builds terminals
    # from run_card values and never draws its own bookkeeping).
    recorder = {n for n in run_trace.NODES
                if not n.startswith(("term.", "trace."))}
    assert not (recorder - declared), \
        f"recorder can emit nodes the atlas cannot draw: {sorted(recorder - declared)}"
    assert not (declared - run_trace.NODES), \
        f"atlas draws nodes the recorder never emits: {sorted(declared - run_trace.NODES)}"


def test_early_exit_to_finalize_is_recorded(rd):
    """Every phase may jump straight to FINALIZE; those early exits are edges
    too and were previously invisible."""
    with runs.scoped_run_dir(rd):
        ctx = LoopStateMachine(loop_id="L1", goal="g", project="p")
        ctx.set_phase(LoopPhase.FINALIZE)
    assert [(r["from"], r["to"]) for r in _rows(rd)] == [
        ("phase.init", "phase.finalize"),
    ]


def test_invalid_transition_still_raises_and_records_nothing(rd):
    with runs.scoped_run_dir(rd):
        ctx = LoopStateMachine(loop_id="L1", goal="g", project="p")
        ctx.set_phase(LoopPhase.DECOMPOSE)
        with pytest.raises(Exception):
            ctx.set_phase(LoopPhase.EXECUTE)   # decompose -> execute is illegal
    assert [(r["from"], r["to"]) for r in _rows(rd)] == [
        ("phase.init", "phase.decompose"),
    ]


def test_trace_disabled_by_config_string_false(rd, monkeypatch):
    """R2-7 (C0.6 class): a quoted `trace.enabled: "false"` must actually
    stop persisting step traces — bool("false") kept them on."""
    import config
    monkeypatch.setattr(
        config, "get",
        lambda key, default=None: ("false" if key == "trace.enabled"
                                   else default))
    assert run_trace._enabled() is False
    assert run_trace.record_edge("a", "b", loop_id="L1", run_dir=rd) is False
    assert _rows(rd) == []
