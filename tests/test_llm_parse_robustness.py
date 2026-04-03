"""Tests for src/llm_parse.py — robust LLM output parsing.

Covers all the failure modes that have actually bitten us in production:
- None / empty content
- Markdown-fenced JSON (```json ... ```)
- Prose before/after the JSON
- Malformed JSON (trailing commas, unclosed, truncated)
- Type mismatches (list when dict expected, dict when list expected)
- float() conversion on non-numeric strings
- Nested braces confusing rfind-based extractors
- NaN / Infinity from LLM
- Wrapped lists ({"lessons": [...]})
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_parse import (
    content_or_empty,
    extract_json,
    safe_float,
    safe_list,
    safe_str,
    strip_markdown_fences,
)


# ===========================================================================
# strip_markdown_fences
# ===========================================================================

class TestStripMarkdownFences:
    def test_no_fence(self):
        assert strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        assert strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_bare_fence(self):
        assert strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_fence_no_newlines(self):
        assert strip_markdown_fences('```json{"a": 1}```') == '{"a": 1}'

    def test_leading_trailing_whitespace(self):
        result = strip_markdown_fences('  ```json\n{"x": 2}\n```  ')
        assert result == '{"x": 2}'

    def test_multiline_content(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        assert strip_markdown_fences(raw) == '{\n  "a": 1,\n  "b": 2\n}'

    def test_no_closing_fence_not_stripped(self):
        # Incomplete fence — should not mangle content
        result = strip_markdown_fences('```json\n{"a": 1}')
        assert '{"a": 1}' in result

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""

    def test_whitespace_only(self):
        assert strip_markdown_fences("   ") == ""


# ===========================================================================
# extract_json — dict expected
# ===========================================================================

class TestExtractJsonDict:
    def test_clean_json(self):
        result = extract_json('{"verdict": "PASS", "confidence": 0.9}', dict)
        assert result == {"verdict": "PASS", "confidence": 0.9}

    def test_prose_before_json(self):
        result = extract_json('Here is my analysis:\n{"verdict": "PASS"}', dict)
        assert result["verdict"] == "PASS"

    def test_prose_after_json(self):
        result = extract_json('{"verdict": "PASS"}\n\nHope that helps!', dict)
        assert result["verdict"] == "PASS"

    def test_markdown_fenced(self):
        raw = '```json\n{"verdict": "PASS", "reason": "looks good"}\n```'
        result = extract_json(raw, dict)
        assert result["verdict"] == "PASS"

    def test_nested_braces(self):
        # rfind-based extractors break on nested braces — depth counter handles it
        raw = '{"outer": {"inner": "value"}, "count": 3}'
        result = extract_json(raw, dict)
        assert result["count"] == 3
        assert result["outer"]["inner"] == "value"

    def test_nested_braces_in_string_value(self):
        # Brace inside a string value — should not confuse extractor
        raw = '{"code": "if (x) { return y; }", "status": "ok"}'
        result = extract_json(raw, dict)
        assert result["status"] == "ok"

    def test_none_content(self):
        result = extract_json(None, dict)
        assert result == {}

    def test_empty_string(self):
        result = extract_json("", dict)
        assert result == {}

    def test_whitespace_only(self):
        result = extract_json("   \n\t  ", dict)
        assert result == {}

    def test_malformed_json_trailing_comma(self):
        result = extract_json('{"a": 1,}', dict)
        assert result == {}

    def test_malformed_json_unclosed(self):
        result = extract_json('{"a": 1, "b": 2', dict)
        assert result == {}

    def test_malformed_json_truncated_mid_string(self):
        result = extract_json('{"result": "partial respon', dict)
        assert result == {}

    def test_no_json_at_all(self):
        result = extract_json("I cannot complete this request.", dict)
        assert result == {}

    def test_refusal_message(self):
        result = extract_json("Sorry, I don't have enough information to answer that.", dict)
        assert result == {}

    def test_custom_default(self):
        result = extract_json(None, dict, default={"fallback": True})
        assert result == {"fallback": True}

    def test_json_with_null_values(self):
        result = extract_json('{"a": null, "b": 1}', dict)
        assert result["a"] is None
        assert result["b"] == 1

    def test_multiple_json_objects_returns_first(self):
        # If LLM returns two JSON objects, we want the first complete one
        raw = '{"step": 1} {"step": 2}'
        result = extract_json(raw, dict)
        assert result.get("step") == 1


# ===========================================================================
# extract_json — list expected
# ===========================================================================

class TestExtractJsonList:
    def test_clean_list(self):
        result = extract_json('["step one", "step two", "step three"]', list)
        assert result == ["step one", "step two", "step three"]

    def test_prose_around_list(self):
        raw = 'Here are the lessons:\n["lesson A", "lesson B"]\nEnd.'
        result = extract_json(raw, list)
        assert result == ["lesson A", "lesson B"]

    def test_markdown_fenced_list(self):
        raw = '```json\n["a", "b", "c"]\n```'
        result = extract_json(raw, list)
        assert result == ["a", "b", "c"]

    def test_none_returns_empty_list(self):
        result = extract_json(None, list)
        assert result == []

    def test_empty_returns_empty_list(self):
        result = extract_json("", list)
        assert result == []

    def test_malformed_list(self):
        result = extract_json('["a", "b",]', list)
        assert result == []

    def test_wrapped_list_in_dict(self):
        # LLM returned {"lessons": [...]} when a bare list was expected
        raw = '{"lessons": ["lesson A", "lesson B"]}'
        result = extract_json(raw, list)
        assert result == ["lesson A", "lesson B"]

    def test_wrapped_list_steps_key(self):
        raw = '{"steps": ["do this", "do that"]}'
        result = extract_json(raw, list)
        assert result == ["do this", "do that"]

    def test_wrapped_list_results_key(self):
        raw = '{"results": ["item1", "item2"]}'
        result = extract_json(raw, list)
        assert result == ["item1", "item2"]

    def test_list_of_dicts(self):
        raw = '[{"task": "research"}, {"task": "build"}]'
        result = extract_json(raw, list)
        assert len(result) == 2
        assert result[0]["task"] == "research"

    def test_nested_list(self):
        raw = '[["a", "b"], ["c", "d"]]'
        result = extract_json(raw, list)
        assert result == [["a", "b"], ["c", "d"]]

    def test_empty_list(self):
        result = extract_json("[]", list)
        assert result == []

    def test_custom_default(self):
        result = extract_json(None, list, default=["fallback"])
        assert result == ["fallback"]


# ===========================================================================
# extract_json — type mismatch
# ===========================================================================

class TestExtractJsonTypeMismatch:
    def test_got_list_expected_dict(self):
        # LLM returned a list when we expected a dict — return default {}
        raw = '["item1", "item2"]'
        result = extract_json(raw, dict)
        assert result == {}

    def test_got_string_scalar(self):
        result = extract_json('"just a string"', dict)
        assert result == {}

    def test_got_number(self):
        result = extract_json("42", dict)
        assert result == {}


# ===========================================================================
# safe_float
# ===========================================================================

class TestSafeFloat:
    def test_numeric_string(self):
        assert safe_float("0.9") == pytest.approx(0.9)

    def test_int(self):
        assert safe_float(1) == 1.0

    def test_float(self):
        assert safe_float(0.75) == pytest.approx(0.75)

    def test_none_returns_default(self):
        assert safe_float(None) == 0.0
        assert safe_float(None, default=0.5) == 0.5

    def test_empty_string_returns_default(self):
        assert safe_float("") == 0.0

    def test_non_numeric_string(self):
        assert safe_float("high") == 0.0
        assert safe_float("very confident") == 0.0

    def test_string_with_units(self):
        # "0.8 approx" — not cleanly convertible
        assert safe_float("0.8 approx") == 0.0

    def test_nan_returns_default(self):
        assert safe_float(float("nan")) == 0.0

    def test_inf_returns_default(self):
        assert safe_float(float("inf")) == 0.0
        assert safe_float(float("-inf")) == 0.0

    def test_nan_string_returns_default(self):
        assert safe_float("NaN") == 0.0

    def test_clamp_min(self):
        assert safe_float(-0.5, min_val=0.0) == 0.0

    def test_clamp_max(self):
        assert safe_float(1.5, max_val=1.0) == 1.0

    def test_clamp_both(self):
        assert safe_float(2.0, min_val=0.0, max_val=1.0) == 1.0
        assert safe_float(-1.0, min_val=0.0, max_val=1.0) == 0.0

    def test_zero_is_valid(self):
        assert safe_float(0) == 0.0
        assert safe_float("0") == 0.0


# ===========================================================================
# safe_str
# ===========================================================================

class TestSafeStr:
    def test_string(self):
        assert safe_str("hello") == "hello"

    def test_none_returns_default(self):
        assert safe_str(None) == ""
        assert safe_str(None, default="fallback") == "fallback"

    def test_strips_whitespace(self):
        assert safe_str("  hello  ") == "hello"

    def test_int_coerced(self):
        assert safe_str(42) == "42"

    def test_max_len(self):
        assert safe_str("hello world", max_len=5) == "hello"

    def test_empty_string(self):
        assert safe_str("") == ""


# ===========================================================================
# safe_list
# ===========================================================================

class TestSafeList:
    def test_list_of_strings(self):
        assert safe_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_not_a_list_returns_empty(self):
        assert safe_list("a string") == []
        assert safe_list(42) == []
        assert safe_list(None) == []
        assert safe_list({"a": 1}) == []

    def test_filters_wrong_element_type(self):
        result = safe_list(["a", 1, "b", None, "c"], element_type=str)
        assert result == ["a", "b", "c"]

    def test_max_items(self):
        result = safe_list(["a", "b", "c", "d"], max_items=2)
        assert result == ["a", "b"]

    def test_list_of_dicts(self):
        items = [{"x": 1}, {"x": 2}]
        assert safe_list(items, element_type=dict) == items

    def test_empty_list(self):
        assert safe_list([]) == []


# ===========================================================================
# content_or_empty
# ===========================================================================

class TestContentOrEmpty:
    def test_normal_response(self):
        class Resp:
            content = "  hello  "
        assert content_or_empty(Resp()) == "hello"

    def test_none_content(self):
        class Resp:
            content = None
        assert content_or_empty(Resp()) == ""

    def test_empty_content(self):
        class Resp:
            content = ""
        assert content_or_empty(Resp()) == ""

    def test_no_content_attr(self):
        class Resp:
            pass
        assert content_or_empty(Resp()) == ""

    def test_whitespace_content(self):
        class Resp:
            content = "   \n\t  "
        assert content_or_empty(Resp()) == ""


# ===========================================================================
# Integration-style: parse patterns that appear in real modules
# ===========================================================================

class TestRealWorldPatterns:
    """Fixtures based on actual LLM response patterns that have caused issues."""

    def test_inspector_verdict_clean(self):
        raw = '{"verdict": "PROCEED", "concerns": [], "recommendation": "ok"}'
        data = extract_json(raw, dict)
        assert data["verdict"] == "PROCEED"

    def test_inspector_verdict_fenced(self):
        raw = "```json\n{\"verdict\": \"RETRY\", \"concerns\": [\"step unclear\"]}\n```"
        data = extract_json(raw, dict)
        assert data["verdict"] == "RETRY"

    def test_decompose_steps_clean(self):
        raw = '["Research the topic", "Analyze findings", "Write summary"]'
        steps = extract_json(raw, list)
        assert len(steps) == 3
        assert steps[0] == "Research the topic"

    def test_decompose_steps_wrapped_in_dict(self):
        # Some models return {"steps": [...]} instead of bare list
        raw = '{"steps": ["Step A", "Step B"]}'
        steps = extract_json(raw, list)
        assert steps == ["Step A", "Step B"]

    def test_lesson_extraction_clean(self):
        raw = '["Always verify outputs before writing", "Use Jina for web fetches"]'
        lessons = extract_json(raw, list)
        assert len(lessons) == 2

    def test_lesson_extraction_wrapped(self):
        raw = '{"lessons": ["Verify before write", "Use Jina"]}'
        lessons = extract_json(raw, list)
        assert lessons == ["Verify before write", "Use Jina"]

    def test_verify_step_confidence_numeric_string(self):
        raw = '{"verdict": "PASS", "reason": "looks good", "confidence": "0.85"}'
        data = extract_json(raw, dict)
        conf = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
        assert conf == pytest.approx(0.85)

    def test_verify_step_confidence_text(self):
        raw = '{"verdict": "PASS", "reason": "ok", "confidence": "high"}'
        data = extract_json(raw, dict)
        conf = safe_float(data.get("confidence"), default=0.5)
        assert conf == 0.5  # falls back to default

    def test_attribution_confidence_null(self):
        raw = '{"failure_mode": "tool_failure", "confidence": null}'
        data = extract_json(raw, dict)
        conf = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
        assert conf == 0.5

    def test_attribution_contributing_factors_not_list(self):
        raw = '{"failure_mode": "unknown", "contributing_factors": "network issue"}'
        data = extract_json(raw, dict)
        factors = safe_list(data.get("contributing_factors", []), element_type=str)
        assert factors == []  # string filtered out — not a list

    def test_director_tickets_raw_string_not_list(self):
        raw = '{"spec": "do something", "tickets": "just one ticket"}'
        data = extract_json(raw, dict)
        raw_tickets = data.get("tickets", [])
        # Caller should use safe_list to guard this
        guarded = safe_list(raw_tickets, element_type=dict)
        assert guarded == []

    def test_evolver_signals_not_list(self):
        raw = '{"signals": "none found"}'
        data = extract_json(raw, dict)
        signals = safe_list(data.get("signals", []), element_type=dict)
        assert signals == []

    def test_truncated_llm_response(self):
        # Simulates a max_tokens truncation mid-JSON
        raw = '{"result": "I found the following patterns: the system tends to fail when'
        data = extract_json(raw, dict)
        assert data == {}

    def test_llm_refusal_returns_default(self):
        raw = "I'm sorry, I cannot help with that request."
        data = extract_json(raw, dict)
        assert data == {}

    def test_json_with_trailing_explanation(self):
        raw = '{"verdict": "PASS"}\n\nNote: I also recommend reviewing the edge cases.'
        data = extract_json(raw, dict)
        assert data["verdict"] == "PASS"

    def test_deeply_nested_json(self):
        raw = '{"outer": {"middle": {"inner": "value"}}, "top": 1}'
        data = extract_json(raw, dict)
        assert data["top"] == 1
        assert data["outer"]["middle"]["inner"] == "value"

    def test_json_with_embedded_code_braces(self):
        # LLM includes code snippet with braces in a string field
        raw = '{"result": "call foo() { return x; }", "status": "done"}'
        data = extract_json(raw, dict)
        assert data["status"] == "done"
