"""Data-first scenario table for the goal-driven behavior scenarios.

Each row is: goal text + scripted adapter responses + expected workspace
artifacts. The table is deliberately plain data (dataclasses of strings,
dicts, lists) so a future Go conformance harness can consume the same rows
with its own driver. Store-level scenarios (playbook grammar, ledger
ingress, rotation, ...) live as plain tests in their subsystem modules —
they pin writer APIs a goal cannot cheaply reach.

Contract citations refer to docs/CONTRACTS.md entries (B1-B12).

The scripted-adapter protocol is PART of each row's meaning (a non-Python
harness must reproduce it, not just the rows): responses are consumed in
order; when the table is exhausted, tool-bearing requests replay the last
tool-bearing response and plain requests replay the last plain response;
a "steps" row renders as a JSON array string; a "tool" row is honored only
when the request offers tools; the plain-call default body is
'{"passed": true}'. See ScriptedAdapter in harness.py — the one normative
implementation until the protocol is extracted to neutral data (Phase 2).
"""

from harness import GoalScenario

# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

GOAL_SCENARIOS = [
    # (1) NOW-lane one-shot: trivial goal in → intake row (B11), run dir +
    # metadata (B3), answer artifact in artifact/, outcome row task_type
    # "now" left UNJUDGED (no judge configured → goal_achieved key absent,
    # rule A6), run card (B5), captains-log + events rows (B8/B9).
    GoalScenario(
        id="now-one-shot",
        goal="What is the capital of France?",
        lane="now",
        responses=[{"content": "The capital of France is Paris."}],
        expect_status="done",
        expect_outcome={"task_type": "now", "status": "done"},
        expect_outcome_unjudged=True,
        expect_success_class="done-unverified",
        contracts="B11, B3, B5, B6, B8, B9",
    ),

    # (2) AGENDA happy path: clarity → plan → steps → done. Run dir
    # skeleton source/build/artifact, metadata lifecycle fields incl. the
    # loops lineage list (B3), status from the registered vocabulary, run
    # card curated (B5), outcome row recorded (B6).
    #
    # The FIRST agenda call is the goal-clarity assessor (it runs unless
    # yolo is configured) — the original table omitted it, every later row
    # shifted one call, the scripted plan never reached decompose, and the
    # engine silently fell back to a single-step plan. Found when
    # test_agenda_flow_reaches_durable_evidence started asserting the FLOW
    # (scripted results must reach durable evidence), not just the object.
    GoalScenario(
        id="agenda-happy-path",
        goal="Summarize the quarterly numbers into a short report",
        lane="agenda",
        project="behavior-happy",
        responses=[
            {"content": '{"clear": true}'},
            {"steps": ["Collect the numbers", "Write the summary"]},
            {"tool": "complete_step", "result": "Collected 12 rows of numbers"},
            {"tool": "complete_step", "result": "Summary written: revenue flat"},
            {"content": '{"lessons": []}'},
        ],
        expect_status="done",
        expect_meta_keys=["loops"],
        expect_outcome={"status": "done"},
        expect_success_class="done-unverified",
        contracts="B3, B5, B6",
    ),

    # (3) Call records under record mode: driven through the FailoverAdapter
    # seam (the one production recording seam), so build/calls/call-NNNNN.json
    # files land with the B4 shape. Extra per-file assertions live in
    # test_behavior_run_lifecycle.py::test_call_records_shape.
    GoalScenario(
        id="now-call-records",
        goal="Name one prime number",
        lane="now",
        responses=[{"content": "2 is a prime number."}],
        record_calls=True,
        expect_status="done",
        contracts="B4, B3",
    ),

    # (5, goal-driven half) Verdict stamping via the deterministic
    # provenance guard: a NOW goal naming an input file that is not on disk
    # is judged NOT achieved with zero LLM — the verdict tuple lands in run
    # metadata (B3 verdict block), the outcome row (B6, judged bool +
    # source), and the card classifies done-not-achieved (B5). stop_verdict
    # rides the closed vocabulary (B6).
    GoalScenario(
        id="now-provenance-verdict",
        goal="Read the file /nonexistent/behavior-probe/input-data.csv and summarize it",
        lane="now",
        responses=[{"content": "Summary: the file held 10 rows."}],
        expect_status="incomplete",
        expect_meta={
            "goal_achieved": False,
            "goal_verdict_source": "provenance",
            "stop_verdict": "lost-the-plot",
        },
        expect_outcome={
            "status": "incomplete",
            "goal_achieved": False,
            "goal_verdict_source": "provenance",
            "stop_verdict": "lost-the-plot",
        },
        expect_success_class="done-not-achieved",
        contracts="B3, B5, B6",
    ),

    # (6) Blocked/stuck path: every step flags stuck → terminal status
    # "stuck" (B3 vocabulary), failure recorded, verdict NOT fabricated
    # (no goal_achieved=True can appear out of thin air; extra assertions in
    # test_behavior_run_lifecycle.py::test_stuck_run_not_fabricated).
    GoalScenario(
        id="agenda-stuck",
        goal="Access the resource that does not exist",
        lane="agenda",
        project="behavior-stuck",
        responses=[
            {"content": '{"clear": true}'},
            {"steps": ["Attempt the impossible fetch"]},
            {"tool": "flag_stuck", "reason": "resource is permanently unavailable"},
        ],
        expect_status="stuck",
        expect_outcome={"status": "stuck"},
        expect_success_class="failed",
        contracts="B3, B5, B6",
    ),
]

# (11) Re-run identity rides the same table shape but needs two dispatches
# of one goal — driven explicitly in test_behavior_now_lane.py.
RERUN_GOAL = GoalScenario(
    id="now-rerun",
    goal="List the three primary colors",
    lane="now",
    responses=[{"content": "Red, yellow, and blue."}],
    expect_status="done",
    contracts="B11",
)


def by_id(sid: str) -> GoalScenario:
    for sc in GOAL_SCENARIOS:
        if sc.id == sid:
            return sc
    raise KeyError(sid)
