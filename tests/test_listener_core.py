from listener_core import parse_slash_command, is_chat_allowed, intent_label


def test_parse_slash_command_basic():
    assert parse_slash_command("/status") == ("status", "")
    assert parse_slash_command("/research some question") == ("research", "some question")


def test_parse_slash_command_not_a_command():
    assert parse_slash_command("hello there") == (None, "hello there")


def test_parse_slash_command_strips_at_suffix_only_when_asked():
    assert parse_slash_command("/status@my_bot", strip_at_suffix=True) == ("status", "")
    assert parse_slash_command("/status@my_bot", strip_at_suffix=False) == ("status@my_bot", "")


def test_is_chat_allowed_empty_allowlist_allows_all():
    assert is_chat_allowed(123, set()) is True
    assert is_chat_allowed(123, None) is True


def test_is_chat_allowed_respects_allowlist():
    assert is_chat_allowed(123, {123, 456}) is True
    assert is_chat_allowed(999, {123, 456}) is False


def test_intent_label_known_and_unknown():
    assert intent_label("additive") == "added to plan"
    assert intent_label("mystery") == "mystery"
