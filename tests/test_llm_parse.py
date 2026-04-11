"""Tests for llm_parse.py — LLM output parsing utilities.

17 modules import from llm_parse. These tests ensure regressions
are caught before they cascade across the codebase.
"""

import pytest
from llm_parse import (
    extract_json,
    safe_float,
    safe_str,
    safe_list,
    content_or_empty,
    strip_markdown_fences,
    _find_json_bounds,
)


# ---------------------------------------------------------------------------
# strip_markdown_fences
# ---------------------------------------------------------------------------

class TestStripMarkdownFences:
    def test_json_fence(self):
        assert strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_bare_fence(self):
        assert strip_markdown_fences('```\nhello\n```') == "hello"

    def test_python_fence(self):
        assert strip_markdown_fences('```python\nprint("hi")\n```') == 'print("hi")'

    def test_no_fence(self):
        assert strip_markdown_fences('{"a": 1}') == '{"a": 1}'

    def test_whitespace_stripped(self):
        assert strip_markdown_fences('  ```json\n{"a":1}\n```  ') == '{"a":1}'

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""


# ---------------------------------------------------------------------------
# _find_json_bounds
# ---------------------------------------------------------------------------

class TestFindJsonBounds:
    def test_simple_object(self):
        assert _find_json_bounds('{"a": 1}', "{", "}") == (0, 8)

    def test_nested_object(self):
        s = '{"a": {"b": 1}}'
        start, end = _find_json_bounds(s, "{", "}")
        assert s[start:end] == s

    def test_array(self):
        assert _find_json_bounds("[1, 2, 3]", "[", "]") == (0, 9)

    def test_leading_prose(self):
        s = 'Here is the result: {"key": "val"}'
        start, end = _find_json_bounds(s, "{", "}")
        assert s[start:end] == '{"key": "val"}'

    def test_no_brackets(self):
        assert _find_json_bounds("no json here", "{", "}") == (-1, -1)

    def test_unbalanced(self):
        assert _find_json_bounds("{unclosed", "{", "}") == (-1, -1)

    def test_nested_arrays(self):
        s = "[[1, 2], [3, 4]]"
        start, end = _find_json_bounds(s, "[", "]")
        assert s[start:end] == s


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_simple_dict(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_simple_list(self):
        assert extract_json('[1, 2, 3]', list) == [1, 2, 3]

    def test_none_returns_default_dict(self):
        assert extract_json(None) == {}

    def test_none_returns_default_list(self):
        assert extract_json(None, list) == []

    def test_empty_returns_default(self):
        assert extract_json("") == {}

    def test_fenced_json(self):
        assert extract_json('```json\n{"key": "val"}\n```') == {"key": "val"}

    def test_prose_around_json(self):
        result = extract_json('Here is the answer: {"x": 42} Hope that helps!')
        assert result == {"x": 42}

    def test_type_mismatch_dict_expected_returns_default(self):
        # Got a list but expected dict
        assert extract_json("[1, 2]", dict) == {}

    def test_unwrap_dict_with_steps_key(self):
        """When expecting list but got dict, unwrap common keys."""
        raw = '{"steps": ["a", "b"]}'
        assert extract_json(raw, list) == ["a", "b"]

    def test_unwrap_dict_with_items_key(self):
        raw = '{"items": [1, 2, 3]}'
        assert extract_json(raw, list) == [1, 2, 3]

    def test_unwrap_dict_with_lessons_key(self):
        raw = '{"lessons": ["lesson1"]}'
        assert extract_json(raw, list) == ["lesson1"]

    def test_unwrap_ignores_non_list_values(self):
        raw = '{"steps": "not a list"}'
        assert extract_json(raw, list) == []

    def test_invalid_json_returns_default(self):
        assert extract_json("{not valid json}") == {}

    def test_nested_json(self):
        raw = '{"outer": {"inner": [1, 2]}}'
        result = extract_json(raw)
        assert result["outer"]["inner"] == [1, 2]

    def test_custom_default(self):
        assert extract_json("garbage", dict, default={"fallback": True}) == {"fallback": True}

    def test_log_tag_doesnt_crash(self):
        # Just ensure log_tag param doesn't cause errors
        assert extract_json('{"a": 1}', log_tag="test") == {"a": 1}
        assert extract_json(None, log_tag="test") == {}

    def test_alt_bracket_type(self):
        """When expected dict but content has array, try array."""
        result = extract_json("[1, 2]", dict)
        # Should return default since types still don't match after parse
        assert result == {}

    def test_dict_expected_found_array_with_steps(self):
        """Edge: expected dict, found array — no unwrapping possible."""
        result = extract_json("[1, 2, 3]", dict)
        assert result == {}


# ---------------------------------------------------------------------------
# safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_int(self):
        assert safe_float(42) == 42.0

    def test_float(self):
        assert safe_float(3.14) == 3.14

    def test_string_number(self):
        assert safe_float("0.8") == 0.8

    def test_none(self):
        assert safe_float(None) == 0.0

    def test_none_custom_default(self):
        assert safe_float(None, default=0.5) == 0.5

    def test_non_numeric_string(self):
        assert safe_float("high") == 0.0

    def test_empty_string(self):
        assert safe_float("") == 0.0

    def test_nan(self):
        assert safe_float(float("nan")) == 0.0

    def test_infinity(self):
        assert safe_float(float("inf")) == 0.0

    def test_neg_infinity(self):
        assert safe_float(float("-inf")) == 0.0

    def test_min_val(self):
        assert safe_float(0.1, min_val=0.5) == 0.5

    def test_max_val(self):
        assert safe_float(0.9, max_val=0.5) == 0.5

    def test_min_max_clamping(self):
        assert safe_float(5.0, min_val=1.0, max_val=3.0) == 3.0
        assert safe_float(-1.0, min_val=0.0, max_val=1.0) == 0.0

    def test_bool_true(self):
        assert safe_float(True) == 1.0

    def test_bool_false(self):
        assert safe_float(False) == 0.0


# ---------------------------------------------------------------------------
# safe_str
# ---------------------------------------------------------------------------

class TestSafeStr:
    def test_string(self):
        assert safe_str("hello") == "hello"

    def test_none(self):
        assert safe_str(None) == ""

    def test_none_custom_default(self):
        assert safe_str(None, default="fallback") == "fallback"

    def test_int_converted(self):
        assert safe_str(42) == "42"

    def test_strips_whitespace(self):
        assert safe_str("  hello  ") == "hello"

    def test_max_len(self):
        assert safe_str("hello world", max_len=5) == "hello"

    def test_max_len_no_truncation_needed(self):
        assert safe_str("hi", max_len=10) == "hi"

    def test_list_converted(self):
        assert safe_str([1, 2]) == "[1, 2]"


# ---------------------------------------------------------------------------
# safe_list
# ---------------------------------------------------------------------------

class TestSafeList:
    def test_valid_list(self):
        assert safe_list(["a", "b"]) == ["a", "b"]

    def test_not_a_list(self):
        assert safe_list("not a list") == []

    def test_none(self):
        assert safe_list(None) == []

    def test_filters_wrong_types(self):
        assert safe_list(["a", 1, "b"], element_type=str) == ["a", "b"]

    def test_int_element_type(self):
        assert safe_list([1, "two", 3], element_type=int) == [1, 3]

    def test_max_items(self):
        assert safe_list([1, 2, 3, 4, 5], element_type=int, max_items=3) == [1, 2, 3]

    def test_empty_list(self):
        assert safe_list([]) == []

    def test_dict_is_not_list(self):
        assert safe_list({"a": 1}) == []


# ---------------------------------------------------------------------------
# content_or_empty
# ---------------------------------------------------------------------------

class TestContentOrEmpty:
    def test_with_content(self):
        class FakeResp:
            content = "hello world"
        assert content_or_empty(FakeResp()) == "hello world"

    def test_strips_whitespace(self):
        class FakeResp:
            content = "  padded  "
        assert content_or_empty(FakeResp()) == "padded"

    def test_none_content(self):
        class FakeResp:
            content = None
        assert content_or_empty(FakeResp()) == ""

    def test_missing_content_attr(self):
        assert content_or_empty(object()) == ""

    def test_empty_string_content(self):
        class FakeResp:
            content = ""
        assert content_or_empty(FakeResp()) == ""

    def test_int_content(self):
        class FakeResp:
            content = 42
        assert content_or_empty(FakeResp()) == "42"
