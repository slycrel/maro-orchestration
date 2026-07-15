"""Behavioral matrices for the shared LLM-output parsing chokepoint.

These cases are intentionally grouped by public behavior.  The old suite had
two files with the same utility classes and dozens of duplicate one-assertion
tests; the matrices retain their distinct malformed/model-output cases without
making every scalar example a separate test item.
"""

from types import SimpleNamespace

import pytest

from llm_parse import (
    _find_json_bounds,
    content_or_empty,
    extract_json,
    safe_float,
    safe_list,
    safe_str,
    strip_markdown_fences,
    strip_think_blocks,
)


def test_strip_think_blocks_matrix():
    cases = [
        ('{"a": 1}', '{"a": 1}'),
        ('<think>reason</think>\n{"passed": true}', '{"passed": true}'),
        (
            '<think>maybe {"passed": false}? no</think>\n{"passed": true}',
            '{"passed": true}',
        ),
        ('<think>reasoning forever...', ""),
        ('<think type="reasoning">x</think>{"a": 1}', '{"a": 1}'),
        ('<THINK>x</THINK>{"a": 1}', '{"a": 1}'),
        ("", ""),
    ]
    for raw, expected in cases:
        assert strip_think_blocks(raw) == expected, f"case={raw!r}"


def test_extract_json_drops_reasoning_decoys_before_parsing():
    raw = (
        '<think>{"passed": false, "confidence": 0.0}</think>\n'
        '```json\n{"passed": true, "confidence": 0.95}\n```'
    )
    assert extract_json(raw, dict) == {"passed": True, "confidence": 0.95}


def test_strip_markdown_fences_matrix():
    cases = [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\nhello\n```', "hello"),
        ('```python\nprint("hi")\n```', 'print("hi")'),
        ('```json{"a": 1}```', '{"a": 1}'),
        ('  ```json\n{"x": 2}\n```  ', '{"x": 2}'),
        ('```json\n{\n  "a": 1,\n  "b": 2\n}\n```', '{\n  "a": 1,\n  "b": 2\n}'),
        ('{"a": 1}', '{"a": 1}'),
        ("", ""),
        ("   ", ""),
    ]
    for raw, expected in cases:
        assert strip_markdown_fences(raw) == expected, f"case={raw!r}"

    incomplete = strip_markdown_fences('```json\n{"a": 1}')
    assert '{"a": 1}' in incomplete


def test_find_json_bounds_matrix():
    cases = [
        ('{"a": 1}', "{", "}", '{"a": 1}'),
        ('{"a": {"b": 1}}', "{", "}", '{"a": {"b": 1}}'),
        ("[1, 2, 3]", "[", "]", "[1, 2, 3]"),
        ('Here: {"key": "val"}', "{", "}", '{"key": "val"}'),
        ("[[1, 2], [3, 4]]", "[", "]", "[[1, 2], [3, 4]]"),
    ]
    for raw, opening, closing, expected in cases:
        start, end = _find_json_bounds(raw, opening, closing)
        assert raw[start:end] == expected, f"case={raw!r}"

    assert _find_json_bounds("no json", "{", "}") == (-1, -1)
    assert _find_json_bounds("{unclosed", "{", "}") == (-1, -1)


def test_extract_json_dict_matrix():
    cases = [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"key": "val"}\n```', {"key": "val"}),
        ('Before {"x": 42} after', {"x": 42}),
        ('{"outer": {"inner": [1, 2]}}', {"outer": {"inner": [1, 2]}}),
        ('{"code": "if (x) { return y; }", "status": "ok"}',
         {"code": "if (x) { return y; }", "status": "ok"}),
        ('{"a": null, "b": 1}', {"a": None, "b": 1}),
        ('{"step": 1} {"step": 2}', {"step": 1}),
        (None, {}),
        ("", {}),
        ("   \n\t", {}),
        ('{"a": 1,}', {}),
        ('{"a": 1, "b": 2', {}),
        ('{"result": "partial', {}),
        ("no json here", {}),
        ("Sorry, not enough information.", {}),
        ('["item1", "item2"]', {}),
        ('"just a string"', {}),
        ("42", {}),
    ]
    for raw, expected in cases:
        assert extract_json(raw, dict) == expected, f"case={raw!r}"

    assert extract_json(None, dict, default={"fallback": True}) == {"fallback": True}
    assert extract_json('{"a": 1}', log_tag="matrix") == {"a": 1}


def test_extract_json_list_matrix():
    cases = [
        ('["a", "b"]', ["a", "b"]),
        ('Before ["a", "b"] after', ["a", "b"]),
        ('```json\n["a", "b"]\n```', ["a", "b"]),
        ('{"lessons": ["lesson A"]}', ["lesson A"]),
        ('{"steps": ["do this", "do that"]}', ["do this", "do that"]),
        ('{"items": [1, 2]}', [1, 2]),
        ('{"results": ["one", "two"]}', ["one", "two"]),
        ('{"steps": "not a list"}', []),
        ('[{"task": "research"}, {"task": "build"}]',
         [{"task": "research"}, {"task": "build"}]),
        ('[["a", "b"], ["c", "d"]]', [["a", "b"], ["c", "d"]]),
        (None, []),
        ("", []),
        ("[]", []),
        ('["a", "b",]', []),
    ]
    for raw, expected in cases:
        assert extract_json(raw, list) == expected, f"case={raw!r}"

    assert extract_json(None, list, default=["fallback"]) == ["fallback"]


def test_safe_float_matrix():
    cases = [
        (42, 42.0),
        (3.14, 3.14),
        ("0.8", 0.8),
        (None, 0.0),
        ("", 0.0),
        ("high", 0.0),
        ("0.8 approx", 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
        (float("-inf"), 0.0),
        ("NaN", 0.0),
        (True, 1.0),
        (False, 0.0),
        (0, 0.0),
        ("0", 0.0),
    ]
    for raw, expected in cases:
        assert safe_float(raw) == pytest.approx(expected), f"case={raw!r}"

    assert safe_float(None, default=0.5) == 0.5
    assert safe_float("high", default=0.5) == 0.5
    assert safe_float(-1, min_val=0) == 0
    assert safe_float(2, max_val=1) == 1
    assert safe_float(5, min_val=1, max_val=3) == 3


def test_safe_str_matrix():
    cases = [
        ("hello", {}, "hello"),
        (None, {}, ""),
        (None, {"default": "fallback"}, "fallback"),
        (42, {}, "42"),
        ("  hello  ", {}, "hello"),
        ("hello world", {"max_len": 5}, "hello"),
        ("hi", {"max_len": 10}, "hi"),
        ([1, 2], {}, "[1, 2]"),
        ("", {}, ""),
    ]
    for raw, kwargs, expected in cases:
        assert safe_str(raw, **kwargs) == expected, f"case={raw!r}"


def test_safe_list_matrix():
    cases = [
        (["a", "b"], {}, ["a", "b"]),
        ("not a list", {}, []),
        (42, {}, []),
        (None, {}, []),
        ({"a": 1}, {}, []),
        (["a", 1, "b"], {"element_type": str}, ["a", "b"]),
        ([1, "two", 3], {"element_type": int}, [1, 3]),
        ([1, 2, 3, 4], {"element_type": int, "max_items": 2}, [1, 2]),
        ([{"x": 1}], {"element_type": dict}, [{"x": 1}]),
        ([], {}, []),
    ]
    for raw, kwargs, expected in cases:
        assert safe_list(raw, **kwargs) == expected, f"case={raw!r}"


def test_content_or_empty_matrix():
    cases = [
        (SimpleNamespace(content="hello"), "hello"),
        (SimpleNamespace(content="  padded  "), "padded"),
        (SimpleNamespace(content=None), ""),
        (SimpleNamespace(content=""), ""),
        (SimpleNamespace(content="  \n\t"), ""),
        (SimpleNamespace(content=42), "42"),
        (object(), ""),
    ]
    for response, expected in cases:
        assert content_or_empty(response) == expected


def test_real_world_verdict_and_decompose_shapes():
    verdict = extract_json(
        '{"verdict": "PASS", "reason": "ok", "confidence": "0.85"}',
        dict,
    )
    assert safe_float(
        verdict.get("confidence"), default=0.5, min_val=0.0, max_val=1.0
    ) == pytest.approx(0.85)
    assert extract_json('{"steps": ["Step A", "Step B"]}', list) == [
        "Step A",
        "Step B",
    ]
