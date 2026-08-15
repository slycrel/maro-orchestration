"""Pins for map_lens — the §9.1 lens-not-schema caveat implemented.

The contract under test: build_map() reconstructs the self-surveying map
from artifacts runs ALREADY write (pure reader, fail-soft per artifact),
and the renderers are pure functions of the RunMap. No new run artifact,
no persistence — the read-only pin at the bottom enforces the charter
structurally.
"""
import ast
import json
from pathlib import Path

import pytest

import map_lens
from map_lens import (STATE_FOG, STATE_GREY, STATE_LIVE, build_map,
                      render_json, render_mermaid, render_text)

SRC = Path(__file__).resolve().parent.parent / "src"


# ---------------------------------------------------------------------------
# Fixture: a run dir shaped like the real artifacts (census 2026-08-15)
# ---------------------------------------------------------------------------

def _write_run(tmp_path, *, meta=None, steps=None, closure=None, card=None,
               loop_stem="aaaa1111"):
    run_dir = tmp_path / "run-test-fixture"
    build = run_dir / "build"
    build.mkdir(parents=True)
    base_meta = {
        "handle_id": "cafe0001",
        "prompt": "Audit the widget spec against captured sources",
        "status": "done",
        "loops": [{"loop_id": loop_stem + "beef", "parent_loop_id": None,
                   "loop_reason": "initial", "continuation_depth": 0}],
    }
    if meta:
        base_meta.update(meta)
    (run_dir / "metadata.json").write_text(json.dumps(base_meta),
                                           encoding="utf-8")
    if steps is not None:
        rows = []
        for i, (text, status) in enumerate(steps, 1):
            rows.append({"index": i, "text": text, "status": status})
        log = {"loop_id": loop_stem, "goal": base_meta["prompt"],
               "status": "done", "steps": rows}
        (build / f"loop-{loop_stem}-log.json").write_text(
            json.dumps(log), encoding="utf-8")
    if closure is not None:
        lines = [json.dumps(row) for row in closure]
        (build / "closure_verdicts.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    if card is not None:
        (run_dir / "run_card.json").write_text(json.dumps(card),
                                               encoding="utf-8")
    return run_dir


_STEPS = [
    ("Survey the artifacts directory and save a listing", "done"),
    ("Read the listing and identify raw captures [recon: decides audit scope]",
     "done"),
    ("Grep captures for spec claims", "done"),
    ("Write the cited audit report [after:2,3]", "blocked"),
    ("Publish summary to project", "pending"),
]


# ---------------------------------------------------------------------------
# build_map
# ---------------------------------------------------------------------------

class TestBuildMap:
    def test_nodes_states_and_flavor(self, tmp_path):
        m = build_map(_write_run(tmp_path, steps=_STEPS))
        steps = [n for n in m.nodes if n.kind == "step"]
        assert [n.state for n in steps] == [
            STATE_LIVE, STATE_LIVE, STATE_LIVE, STATE_GREY, STATE_FOG]
        recon = steps[1]
        assert recon.flavor == "recon"
        assert recon.recon_decision == "decides audit scope"
        # commit steps carry no flavor marker
        assert steps[0].flavor == ""
        # labels are display-clean: tags stripped
        assert "[recon" not in recon.label
        assert "[after" not in steps[3].label

    def test_unknown_status_is_fog_with_note(self, tmp_path):
        m = build_map(_write_run(
            tmp_path, steps=[("Do the thing", "exploded")]))
        step = [n for n in m.nodes if n.kind == "step"][0]
        assert step.state == STATE_FOG
        assert any("unknown step status" in n for n in m.notes)

    def test_explicit_after_edges_vs_sequential_default(self, tmp_path):
        m = build_map(_write_run(tmp_path, steps=_STEPS))
        after = [(e.src, e.dst) for e in m.edges if e.kind == "after"]
        stem = "aaaa1111"
        assert (f"{stem}:2", f"{stem}:4") in after
        assert (f"{stem}:3", f"{stem}:4") in after
        # step 4 has explicit deps -> no sequential edge into it
        seq_into_4 = [e for e in m.edges
                      if e.kind == "seq" and e.dst == f"{stem}:4"]
        assert not seq_into_4
        # explicit flag distinguishes authored deps from the default
        assert all(e.explicit for e in m.edges if e.kind == "after")
        assert not any(e.explicit for e in m.edges if e.kind == "seq")

    def test_goal_connects_to_first_step(self, tmp_path):
        m = build_map(_write_run(tmp_path, steps=_STEPS))
        assert any(e.src == "goal" and e.dst == "aaaa1111:1"
                   for e in m.edges)

    def test_dangling_after_ref_noted_not_crashed(self, tmp_path):
        m = build_map(_write_run(
            tmp_path, steps=[("Synthesize everything [after:7]", "done")]))
        assert any("dangling [after:7]" in n for n in m.notes)

    def test_stop_verdict_carries_reopen_condition(self, tmp_path):
        m = build_map(_write_run(
            tmp_path,
            meta={"stop_verdict": "out-of-budget",
                  "stop_evidence": "daily cap hit at $10"},
            steps=_STEPS))
        assert m.stop["verdict"] == "out-of-budget"
        assert m.stop["evidence"] == "daily cap hit at $10"
        assert m.stop["reopen"] == "budget restored"
        assert any(n.kind == "stop" for n in m.nodes)

    def test_pause_reason_read_from_card(self, tmp_path):
        m = build_map(_write_run(
            tmp_path, steps=_STEPS,
            card={"pause_reason": "awaiting-clarification",
                  "pause_family": "operator"}))
        assert m.pause == {"reason": "awaiting-clarification",
                           "family": "operator"}

    def test_closure_stall_detected_on_repeated_fingerprint(self, tmp_path):
        closure = [
            {"verdict": "incomplete", "complete": False,
             "fingerprint": "fp-1", "failed_checks": ["pytest -q"]},
            {"verdict": "incomplete", "complete": False,
             "fingerprint": "fp-1", "failed_checks": ["pytest -q"]},
        ]
        m = build_map(_write_run(tmp_path, steps=_STEPS, closure=closure))
        assert m.stall is True
        assert len(m.closure) == 2

    def test_differing_fingerprints_not_a_stall(self, tmp_path):
        closure = [
            {"verdict": "incomplete", "fingerprint": "fp-1"},
            {"verdict": "done", "fingerprint": "fp-2"},
        ]
        m = build_map(_write_run(tmp_path, steps=_STEPS, closure=closure))
        assert m.stall is False

    def test_empty_fingerprints_never_stall(self, tmp_path):
        closure = [{"verdict": "a", "fingerprint": ""},
                   {"verdict": "b", "fingerprint": ""}]
        m = build_map(_write_run(tmp_path, steps=_STEPS, closure=closure))
        assert m.stall is False

    def test_missing_artifacts_render_partial_with_notes(self, tmp_path):
        run_dir = tmp_path / "bare-run"
        run_dir.mkdir()
        m = build_map(run_dir)
        assert any("run metadata" in n for n in m.notes)
        assert any("loop logs" in n for n in m.notes)
        # goal node still exists; nothing crashed
        assert m.nodes[0].kind == "goal"

    def test_truncated_recon_tag_noted(self, tmp_path):
        m = build_map(_write_run(
            tmp_path,
            steps=[("Read the article and save URLs [recon: dec…", "done")]))
        step = [n for n in m.nodes if n.kind == "step"][0]
        assert step.flavor == ""  # grammar honestly misses it
        assert any("unparsed [recon tag" in n for n in m.notes)

    def test_multi_loop_lineage_edge(self, tmp_path):
        run_dir = _write_run(
            tmp_path,
            meta={"loops": [
                {"loop_id": "aaaa1111beef", "parent_loop_id": None,
                 "loop_reason": "initial", "continuation_depth": 0},
                {"loop_id": "bbbb2222beef", "parent_loop_id": "aaaa1111beef",
                 "loop_reason": "restart", "continuation_depth": 1},
            ]},
            steps=[("First loop step", "done")])
        log2 = {"loop_id": "bbbb2222", "steps": [
            {"index": 1, "text": "Second loop step", "status": "done"}]}
        (run_dir / "build" / "loop-bbbb2222-log.json").write_text(
            json.dumps(log2), encoding="utf-8")
        m = build_map(run_dir)
        lineage = [e for e in m.edges if e.kind == "lineage"]
        assert len(lineage) == 1
        assert lineage[0].src == "aaaa1111:1"
        assert lineage[0].dst == "bbbb2222:1"
        assert len(m.loops) == 2

    def test_malformed_closure_lines_partial_note(self, tmp_path):
        run_dir = _write_run(tmp_path, steps=_STEPS)
        (run_dir / "build" / "closure_verdicts.jsonl").write_text(
            '{"verdict": "ok", "fingerprint": "x"}\nnot json\n',
            encoding="utf-8")
        m = build_map(run_dir)
        assert len(m.closure) == 1
        assert any("malformed" in n for n in m.notes)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

class TestRenderers:
    def test_text_render_carries_map_vocabulary(self, tmp_path):
        m = build_map(_write_run(
            tmp_path,
            meta={"stop_verdict": "thesis-refuted",
                  "stop_evidence": "no connection after 3 avenues"},
            steps=_STEPS))
        text = render_text(m)
        assert "MAP  cafe0001" in text
        assert "●" in text and "◐" in text and "○" in text
        assert "⌕ recon→ decides audit scope" in text
        assert "[after 2,3]" in text
        assert "thesis-refuted" in text
        assert "reopens when: new connection evidence" in text

    def test_text_render_stall_warning(self, tmp_path):
        closure = [{"verdict": "x", "fingerprint": "same"},
                   {"verdict": "x", "fingerprint": "same"}]
        m = build_map(_write_run(tmp_path, steps=_STEPS, closure=closure))
        assert "STALL" in render_text(m)

    def test_mermaid_is_syntactically_plausible(self, tmp_path):
        m = build_map(_write_run(
            tmp_path, meta={"stop_verdict": "out-of-budget"}, steps=_STEPS))
        mm = render_mermaid(m)
        assert mm.startswith("flowchart TD")
        # node ids must not carry colons (mermaid syntax)
        body = mm.splitlines()[1:]
        assert not any(":" in line.split("[")[0].split("(")[0].split("{")[0]
                       for line in body if "-->" not in line
                       and "classDef" not in line and "class " not in line
                       and "-.->" not in line)
        # labels were sanitized: no double quotes inside label text beyond
        # the delimiters themselves, no square brackets inside labels
        assert "[after" not in mm
        assert "STOP: out-of-budget" in mm

    def test_json_round_trips(self, tmp_path):
        m = build_map(_write_run(tmp_path, steps=_STEPS))
        data = json.loads(render_json(m))
        assert data["handle_id"] == "cafe0001"
        assert len([n for n in data["nodes"] if n["kind"] == "step"]) == 5
        assert {e["kind"] for e in data["edges"]} >= {"seq", "after"}


# ---------------------------------------------------------------------------
# Charter pins (structural)
# ---------------------------------------------------------------------------

def _write_offenders(tree: ast.AST) -> list:
    """THE write-detection logic — one implementation, used by the real pin
    AND its guard test (round-2 review, both lenses: a hand-duplicated
    detector in the guard test couldn't catch a regression in this one)."""
    banned = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir",
              "rename", "touch", "rmtree", "makedirs"}
    offenders = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in banned
    ]
    # open() in write mode — BOTH the builtin (`open(p, "w")`) and the
    # method form (`p.open("w")`). Round-1 review (Skeptic + Architect,
    # independently): a builtin-only check let a `path.open("w")` cache
    # sail through — the literal threat this pin exists for.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode_args = list(node.args[1:2])   # open(path, mode)
        elif (isinstance(node.func, ast.Attribute)
                and node.func.attr == "open"):
            mode_args = list(node.args[0:1])   # path.open(mode)
        else:
            continue
        mode_args += [kw.value for kw in node.keywords if kw.arg == "mode"]
        for arg in mode_args:
            if (isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and any(c in arg.value for c in "wax+")):
                offenders.append("open-write")
    return offenders


class TestCharterPins:
    def test_lens_is_read_only(self):
        """Pure reader: no write/mkdir/unlink calls anywhere in the module.

        The §9.1 decree is lens-NOT-schema; the moment this module writes a
        run artifact it has become a store. Enforced on the AST, so a future
        'just cache it' edit fails a test instead of a review.
        """
        tree = ast.parse((SRC / "map_lens.py").read_text(encoding="utf-8"))
        assert _write_offenders(tree) == []

    def test_read_only_pin_catches_method_open(self):
        """Guard-the-guard, against the REAL detector: Path.open('w') and
        builtin open(p,'w') must register; reads must not."""
        assert _write_offenders(ast.parse(
            '(p / "y").open("w")')) == ["open-write"]
        assert _write_offenders(ast.parse(
            'open("x.txt", "w")')) == ["open-write"]
        assert _write_offenders(ast.parse(
            'p.open(mode="a")')) == ["open-write"]
        # reads stay clean — incl. a filename containing 'x' (round-1's own
        # first cut of this pin would have false-positived on it)
        assert _write_offenders(ast.parse('open("x.txt")')) == []
        assert _write_offenders(ast.parse('p.open()')) == []
        assert _write_offenders(ast.parse('p.write_text("d")')) == ["write_text"]

    def test_lens_does_not_import_learning_or_loop_modules(self):
        """The lens reads artifacts, never live machinery: importing loop/
        learning modules would couple rendering to execution internals and
        invite the store-first rot §12 nudge 4 warns about. planner (step
        grammar), stop_verdicts (vocabulary), and runs (dir resolution) are
        the sanctioned seams."""
        tree = ast.parse((SRC / "map_lens.py").read_text(encoding="utf-8"))
        banned_prefixes = ("memory", "evolver", "skills", "agent_loop",
                           "loop_", "step_exec", "handle", "director",
                           "knowledge_web")
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        offenders = [m for m in imported
                     if m.startswith(banned_prefixes)]
        assert offenders == []

    def test_reopen_conditions_cover_all_stop_values(self):
        """stop_verdicts.REOPEN_CONDITIONS must cover every legal stop value
        — a verdict without a way back is the §13b rot (a dead end that
        stays dead)."""
        import stop_verdicts
        assert set(stop_verdicts.REOPEN_CONDITIONS) == set(
            stop_verdicts.VALID_STOP_VALUES)
        assert all(stop_verdicts.REOPEN_CONDITIONS.values())
        assert stop_verdicts.reopen_condition("nonsense") == ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_renders_run_dir_path(self, tmp_path, capsys):
        run_dir = _write_run(tmp_path, steps=_STEPS)
        rc = map_lens.main([str(run_dir)])
        assert rc == 0
        assert "MAP  cafe0001" in capsys.readouterr().out

    def test_cli_unknown_ref_errors(self, tmp_path, capsys, monkeypatch):
        import runs
        monkeypatch.setattr(runs, "resolve_run_dir", lambda ref: None)
        rc = map_lens.main(["does-not-exist"])
        assert rc == 2
        assert "no run found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Review round-1 pins (2026-08-15, sonnet 3-lens): each fix keeps its test.
# ---------------------------------------------------------------------------

class TestReviewRound1Pins:
    def test_after_edges_survive_non_dict_step_rows(self, tmp_path):
        """Skeptic HIGH: filtered texts vs unfiltered enumerate desynced
        [after:N] indices past any malformed row. Pin: a None row between
        steps must not shift dependency attribution."""
        run_dir = _write_run(tmp_path, steps=[("Step one", "done")])
        stem = "aaaa1111"
        log = {
            "loop_id": stem,
            "steps": [
                {"index": 1, "text": "Gather sources", "status": "done"},
                None,
                {"index": 3, "text": "Synthesize report [after:1]",
                 "status": "done"},
            ],
        }
        (run_dir / "build" / f"loop-{stem}-log.json").write_text(
            json.dumps(log), encoding="utf-8")
        m = build_map(run_dir)
        after = [(e.src, e.dst) for e in m.edges if e.kind == "after"]
        assert (f"{stem}:1", f"{stem}:3") in after
        assert any("step 2 row is not an object" in n for n in m.notes)

    def test_non_dict_metadata_degrades_with_note(self, tmp_path):
        run_dir = tmp_path / "weird-run"
        (run_dir / "build").mkdir(parents=True)
        (run_dir / "metadata.json").write_text('["not", "a", "dict"]',
                                               encoding="utf-8")
        m = build_map(run_dir)  # must not raise
        assert any("not a JSON object" in n for n in m.notes)
        assert m.handle_id  # dir-derived fallback engaged
        assert any("derived from directory name" in n for n in m.notes)

    def test_non_dict_loop_log_noted(self, tmp_path):
        run_dir = _write_run(tmp_path, steps=None)
        (run_dir / "build" / "loop-cccc3333-log.json").write_text(
            "[1, 2, 3]", encoding="utf-8")
        m = build_map(run_dir)
        assert any("loop log cccc3333 is not a JSON object" in n
                   for n in m.notes)

    def test_non_list_steps_field_noted(self, tmp_path):
        run_dir = _write_run(tmp_path, steps=None)
        (run_dir / "build" / "loop-dddd4444-log.json").write_text(
            json.dumps({"loop_id": "dddd4444", "steps": "oops"}),
            encoding="utf-8")
        m = build_map(run_dir)
        assert any("steps is not a list" in n for n in m.notes)

    def test_non_dict_jsonl_row_counted_as_malformed(self, tmp_path):
        run_dir = _write_run(tmp_path, steps=_STEPS)
        (run_dir / "build" / "closure_verdicts.jsonl").write_text(
            '{"verdict": "ok", "fingerprint": "x"}\n[1,2]\n',
            encoding="utf-8")
        m = build_map(run_dir)
        assert len(m.closure) == 1
        assert any("malformed" in n for n in m.notes)

    def test_guessed_loop_order_noted(self, tmp_path):
        """Both lenses: unmatched log stems sorted lexically LOOKED causal.
        Two logs with no loops[] metadata -> order-guess notes."""
        run_dir = _write_run(tmp_path, meta={"loops": []},
                             steps=[("Only step", "done")])
        log2 = {"loop_id": "zzzz9999", "steps": [
            {"index": 1, "text": "Later loop step", "status": "done"}]}
        (run_dir / "build" / "loop-zzzz9999-log.json").write_text(
            json.dumps(log2), encoding="utf-8")
        m = build_map(run_dir)
        assert any("loop order guessed" in n for n in m.notes)

    def test_single_log_never_notes_order_guess(self, tmp_path):
        """One log = no ordering question; the note would be noise."""
        m = build_map(_write_run(tmp_path, meta={"loops": []},
                                 steps=[("Only step", "done")]))
        assert not any("loop order guessed" in n for n in m.notes)

    def test_public_after_deps_grammar(self):
        """The private-regex coupling is gone: planner exposes the per-step
        reader and map_lens imports only public names."""
        from planner import after_deps, strip_after_tag
        assert after_deps("Synthesize [after:2,3]") == {2, 3}
        assert after_deps("No tag here") is None
        assert strip_after_tag("Synthesize [after:2,3]") == "Synthesize"
        import ast as _ast
        tree = _ast.parse((SRC / "map_lens.py").read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ImportFrom) and node.module == "planner":
                assert not any(a.name.startswith("_") for a in node.names)


# ---------------------------------------------------------------------------
# Review round-2 pins (2026-08-15, sonnet 2-lens on the fix delta): 7/7 real.
# ---------------------------------------------------------------------------

class TestReviewRound2Pins:
    def test_json_null_artifacts_are_noted(self, tmp_path):
        """Skeptic: literal `null` parses cleanly to None and dodged every
        corrupt-shape note — a whole loop could vanish untraceably."""
        run_dir = tmp_path / "null-run"
        (run_dir / "build").mkdir(parents=True)
        (run_dir / "metadata.json").write_text("null", encoding="utf-8")
        (run_dir / "build" / "loop-eeee5555-log.json").write_text(
            "null", encoding="utf-8")
        m = build_map(run_dir)
        assert any("run metadata is JSON null" in n for n in m.notes)
        assert any("loop log eeee5555 is JSON null" in n for n in m.notes)

    def test_non_dict_run_card_noted(self, tmp_path):
        run_dir = _write_run(tmp_path, steps=_STEPS)
        (run_dir / "run_card.json").write_text('"just a string"',
                                               encoding="utf-8")
        m = build_map(run_dir)
        assert any("run card is not a JSON object" in n for n in m.notes)

    def test_sequential_edge_bridges_malformed_row(self, tmp_path):
        """Architect: a no-tag step right after a malformed row floated —
        the sequential default now links to the nearest earlier node."""
        run_dir = _write_run(tmp_path, steps=None)
        stem = "aaaa1111"
        log = {"loop_id": stem, "steps": [
            {"index": 1, "text": "First real step", "status": "done"},
            "corrupt-row",
            {"index": 3, "text": "Untagged step after the gap",
             "status": "done"},
        ]}
        (run_dir / "build" / f"loop-{stem}-log.json").write_text(
            json.dumps(log), encoding="utf-8")
        m = build_map(run_dir)
        seq = [(e.src, e.dst) for e in m.edges if e.kind == "seq"]
        assert (f"{stem}:1", f"{stem}:3") in seq  # bridged, not floating
