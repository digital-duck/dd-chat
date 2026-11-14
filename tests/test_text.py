from chat_text import tidy_markdown


def test_blank_line_before_list():
    assert tidy_markdown("**Steps**\n1. a\n2. b") == "**Steps**\n\n1. a\n2. b"


def test_code_and_continuations_untouched():
    text = "```\nintro\n- y\n```\n\n- a\n  continued\n  - nested"
    assert tidy_markdown(text) == text
