"""Liveness tests for packaging_readout (next-leap slice 1, report-only).

Consumer-first discipline: the readout is the CONSUMER of four live stores
(persona-dispatch-log, outcomes, skills+skill-stats, persona-outcomes);
these tests seed real rows through the real writers where one exists and
assert the report actually reads them — no read-side mocks.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _seed(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)

    goal_a = "research polymarket edges for the weekend ledger"
    goal_b = "summarize the quarterly numbers"

    # Dispatch log: test-packager on goal_a twice (one row repeated) + one
    # fallback on goal_b; a second persona with a single unjoined goal.
    disp = [
        {"goal_preview": goal_a, "persona_name": "test-packager",
         "confidence": 0.9, "is_fallback": False,
         "dispatched_at": "2026-07-29T00:00:00+00:00"},
        {"goal_preview": goal_a, "persona_name": "test-packager",
         "confidence": 0.9, "is_fallback": False,
         "dispatched_at": "2026-07-29T00:01:00+00:00"},
        {"goal_preview": goal_b, "persona_name": "test-packager",
         "confidence": 0.4, "is_fallback": True,
         "dispatched_at": "2026-07-29T00:02:00+00:00"},
        {"goal_preview": "a goal that never ran", "persona_name":
         "test-other", "confidence": 0.8, "is_fallback": False,
         "dispatched_at": "2026-07-29T00:03:00+00:00"},
    ]
    (mem / "persona-dispatch-log.jsonl").write_text(
        "\n".join(json.dumps(r) for r in disp) + "\n")

    # Outcomes joinable by goal[:120]: verdict mix on goal_a, unverdicted
    # on goal_b.
    outs = [
        {"goal": goal_a, "task_type": "research", "status": "done",
         "goal_achieved": True, "loop_id": "loop-1"},
        {"goal": goal_a, "task_type": "research", "status": "done",
         "goal_achieved": False, "loop_id": "loop-2"},
        {"goal": goal_b, "task_type": "agenda", "status": "done",
         "loop_id": "loop-3"},
    ]
    (mem / "outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in outs) + "\n")

    # persona-outcomes with a loop_id that does NOT join (the live store's
    # measured state) — the coverage block must say so.
    (mem / "persona-outcomes.jsonl").write_text(json.dumps(
        {"persona": "researcher", "goal": goal_a, "status": "done",
         "loop_id": "loop-nowhere"}) + "\n")

    # Skills through the real writer (schema-safe), stats through the real
    # recorder: strong (include bar), broken (open circuit), thin (no
    # stats row).
    from skill_types import Skill
    from skills import save_skill, record_skill_outcome
    common = dict(description="d", steps_template=["s"],
                  source_loop_ids=[], created_at="2026-07-29T00:00:00+00:00")
    save_skill(Skill(id="sk-strong", name="polymarket research",
                     trigger_patterns=["polymarket"], **common))
    save_skill(Skill(id="sk-broken", name="broken polymarket probe",
                     trigger_patterns=["polymarket"],
                     circuit_state="open", **common))
    save_skill(Skill(id="sk-thin", name="fresh polymarket idea",
                     trigger_patterns=["polymarket"], **common))
    for ok in [True] * 9 + [False]:      # 10 uses @ 90% — clears the bar
        record_skill_outcome("sk-strong", ok)
    return mem


def test_readout_reads_all_four_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    mem = _seed(tmp_path)
    from packaging_readout import build_readout
    r = build_readout(mem)

    cov = r["coverage"]
    assert cov["dispatch_rows"] == 4
    assert cov["joined_goals"] == 2 and cov["unjoined_goals"] == 1
    assert cov["verdict_totals"] == {
        "true": 1, "false": 1, "unverdicted": 1}
    assert cov["persona_outcomes_loop_join_hits"] == 0
    assert cov["persona_outcomes_rows"] == 1
    assert cov["skills_selector_available"] is True
    # Repo personas never dispatched in this workspace are named, not
    # dropped.
    assert "data-analyst" in cov["personas_without_dispatch_evidence"]

    p = r["personas"]["test-packager"]
    assert p["dispatches"] == 3 and p["unique_goals"] == 2
    assert p["fallback_dispatches"] == 1
    cell = p["cells"]["research"]
    assert cell["goals"] == 1
    assert cell["achieved_true"] == 1 and cell["achieved_false"] == 1

    include = {row["skill_id"] for row in cell["would_include"]}
    exclude = {row["skill_id"] for row in cell["would_exclude"]}
    thin = {row["skill_id"] for row in cell["insufficient_evidence"]}
    assert "sk-strong" in include
    # Open circuit surfaces via the supplementary scan even though the
    # live selector filters it.
    assert "sk-broken" in exclude
    assert "sk-thin" in thin

    # The other persona's unjoined goal lands in the honesty cell.
    assert "(unjoined)" in r["personas"]["test-other"]["cells"]


def test_render_is_report_only_and_honest(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    mem = _seed(tmp_path)
    from packaging_readout import build_readout, render_markdown
    text = render_markdown(build_readout(mem))
    assert "REPORT-ONLY" in text
    assert "dead join" in text            # persona-outcomes 0/1
    assert "test-packager × research" in text
    assert "polymarket research" in text  # would_include row rendered
    assert "circuit open" in text         # exclusion reason visible
    assert "no stats row" in text         # thin-evidence reason visible


def test_empty_workspace_degrades_honestly(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir()
    from packaging_readout import build_readout, render_markdown
    r = build_readout(tmp_path / "memory")
    assert r["coverage"]["dispatch_rows"] == 0
    assert r["personas"] == {}
    # Renders without raising; still names the never-dispatched personas.
    assert "NO dispatch evidence" in render_markdown(r)
