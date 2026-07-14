import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parent.parent / "scripts" / "session-reuse-spike.py"
SPEC = importlib.util.spec_from_file_location("session_reuse_spike", SCRIPT)
spike = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(spike)


def test_arm_contexts_cannot_share_full_prompt_cache():
    resumed = spike._segment_state("resumed", 2)
    fresh = spike._segment_state("fresh", 2)

    assert "resumed-test reference line" in resumed
    assert "fresh-test reference line" in fresh
    assert resumed != fresh
    for value in spike.FACTS.values():
        assert value in resumed
        assert value in fresh


def test_correct_requires_exact_scalar_and_complete_json():
    assert spike._correct(1, "KESTREL", "KESTREL")
    assert not spike._correct(1, "The codename is KESTREL", "KESTREL")
    assert spike._correct(
        5,
        '{"codename":"KESTREL","port":4317,"budget":"$1.75",'
        '"policy":"keep evidence files"}',
        None,
    )
    assert spike._correct(
        5,
        '```json\n{"codename":"KESTREL","port":4317,"budget":"$1.75",'
        '"policy":"keep evidence files"}\n```',
        None,
    )
    assert not spike._correct(5, '{"codename":"KESTREL"}', None)
    assert not spike._correct(
        5,
        '{"codename":"KESTREL","port":4317,"budget":"$1.75",'
        '"policy":"keep evidence files","extra":"not allowed"}',
        None,
    )


def test_aggregate_sums_measured_fields():
    rows = [
        {
            "correct": True, "wall_ms": 10, "duration_api_ms": 8,
            "cost_usd": 0.01, "input_tokens": 2,
            "cache_creation_input_tokens": 3, "cache_read_input_tokens": 4,
            "output_tokens": 5,
        },
        {
            "correct": False, "wall_ms": 20, "duration_api_ms": 18,
            "cost_usd": 0.02, "input_tokens": 6,
            "cache_creation_input_tokens": 7, "cache_read_input_tokens": 8,
            "output_tokens": 9,
        },
    ]

    got = spike._aggregate(rows)

    assert got == {
        "steps": 2,
        "correct": 1,
        "wall_ms": 30,
        "duration_api_ms": 26,
        "cost_usd": 0.03,
        "input_tokens": 8,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 12,
        "output_tokens": 14,
    }


def test_output_reservation_refuses_overwriting_retained_evidence(tmp_path):
    output = tmp_path / "benchmark" / "run-1"
    spike._reserve_output_dir(output)
    (output / "summary.json").write_text("evidence", encoding="utf-8")

    with pytest.raises(FileExistsError):
        spike._reserve_output_dir(output)

    assert (output / "summary.json").read_text(encoding="utf-8") == "evidence"
