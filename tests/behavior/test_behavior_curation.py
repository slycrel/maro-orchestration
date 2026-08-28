"""Behavior: run-card curation — docs/CONTRACTS.md B5.

The card is the derived, curated view over metadata.json (which stays
authoritative). Pins: identity echo, the success_class mapping for the
core status x verdict grid, and the writer-private `_curation` namespace.
"""

import json

from harness import SUCCESS_CLASSES, read_json, read_meta


def _close(hid: str, *, prompt: str, status: str, achieved=None) -> dict:
    from runs import close_run, create_run_dir, scoped_run_dir, stamp_run_verdict

    rd = create_run_dir(hid, prompt=prompt, lane="agenda")
    if achieved is not None:
        with scoped_run_dir(rd):
            stamp_run_verdict(
                goal_achieved=achieved, source="closure", confidence=0.9,
                summary="probe verdict", gaps=None,
            )
    close_run(hid, status=status)
    card = read_json(rd / "run_card.json")
    return {"rd": rd, "card": card}


def test_card_identity_echo_and_curation_namespace():
    out = _close("cab00001", prompt="curation echo probe", status="done",
                 achieved=True)
    card, rd = out["card"], out["rd"]
    meta = read_meta(rd)

    # Identity/echo group copied from metadata at curation (B5).
    assert card["handle_id"] == meta["handle_id"]
    assert card["goal"] == meta["prompt"]
    assert card["status"] == meta["status"] == "done"
    assert card.get("started_at") == meta["started_at"]

    # THE classification, from the registered vocabulary (B5).
    assert card["success_class"] in SUCCESS_CLASSES
    assert card["success_class"] == "success"

    # Verdict echo follows the judged metadata.
    assert card["goal_achieved"] is True

    # Writer-private namespace: the curator records its own execution
    # provenance under `_curation`. B5 registers the NAMESPACE as a card
    # fact but declares underscore-prefixed keys writer-private — no reader
    # (this suite included) may depend on their contents, so the only
    # cross-engine assertion is that the namespace, when present, is an
    # object. (An earlier draft pinned `completed`/`failed` inside it —
    # exactly the doc/code mismatch B5 forbids; review round 1 caught it.)
    cur = card.get("_curation")
    assert isinstance(cur, dict)


def test_success_class_grid():
    """B5: status x verdict → success_class, the four core cells."""
    cases = [
        ("cab00002", "done", True, "success"),
        ("cab00003", "done", False, "done-not-achieved"),
        ("cab00004", "done", None, "done-unverified"),
        ("cab00005", "stuck", True, "achieved-not-done"),
    ]
    for hid, status, achieved, expected in cases:
        out = _close(hid, prompt=f"grid probe {hid}", status=status,
                     achieved=achieved)
        assert out["card"]["success_class"] == expected, (
            f"{status=} {achieved=} must classify as {expected} (B5)"
        )


def test_card_is_derived_metadata_stays_authoritative():
    """B5: cards are regenerated post-hoc; refresh rebuilds pure fields
    from metadata, so a stale card converges back to the evidence."""
    out = _close("cab00006", prompt="derived-view probe", status="done",
                 achieved=None)
    rd, card = out["rd"], out["card"]
    assert card["success_class"] == "done-unverified"

    # The verdict lands after close (async closure) — a refresh must pick
    # it up from metadata.
    from run_curation import refresh_run_card_classification
    from runs import scoped_run_dir, stamp_run_verdict

    with scoped_run_dir(rd):
        stamp_run_verdict(
            goal_achieved=True, source="closure", confidence=0.9,
            summary="post-hoc verdict", gaps=None,
        )
    refresh_run_card_classification("cab00006")
    card2 = read_json(rd / "run_card.json")
    assert card2["success_class"] == "success", (
        "regenerated card must follow metadata — metadata wins (B5)"
    )
