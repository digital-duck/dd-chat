import pytest
import yaml

from chat_export import export_markdown, export_yaml, parse_export


def sample():
    return {
        0: [{"role": "user", "content": "--- attached file: a.txt ---\nfile\n--- end of a.txt ---\n\nWhat?", "shown": "What?\n\n📎 `a.txt`", "q": 1},
            {"role": "assistant", "content": "Line 1\n\n  indented: yes\n- item\n", "model": "claude_cli:claude-opus-5-5", "seconds": 2.345,
             "feedback": 1, "best": 1, "input_tokens": 12, "output_tokens": 30, "temperature": 0.5, "instructions": "be brief",
             "links": {"https://x.org": "ok"}}],
        1: [{"role": "user", "content": "--- attached file: a.txt ---\nfile\n--- end of a.txt ---\n\nWhat?", "shown": "What?\n\n📎 `a.txt`", "q": 1},
            {"role": "assistant", "content": "Same\x07 text: with: colons", "model": "ollama:gemma4", "seconds": 1.0, "feedback": -1,
             "shared_from": 1, "cost_usd": 0.0}],
    }


def test_yaml_round_trip():
    judgements = {1: {"judge": "ollama:gemma4", "content": "**Agree**: all", "seconds": 3.0}}
    histories, judged = parse_export(export_yaml(sample(), judgements, {"id": 7}).encode())
    first = histories[0][1]
    assert histories[0][0]["content"].startswith("--- attached file") and histories[0][0]["shown"] == "What?\n\n📎 `a.txt`"
    assert first["content"] == "Line 1\n\n  indented: yes\n- item\n"
    assert first["model"] == "claude_cli:claude-opus-5-5" and first["best"] == 1 and first["feedback"] == 1
    assert first["links"] == {"https://x.org": "ok"} and first["temperature"] == 0.5 and first["instructions"] == "be brief"
    assert histories[1][1]["content"] == "Same text: with: colons"            # the control character is dropped
    assert histories[1][1]["shared_from"] == 1 and histories[1][1]["cost_usd"] == 0.0
    assert judged == {1: {"judge": "ollama:gemma4", "content": "**Agree**: all", "seconds": 3.0}}


def test_old_export_still_imports():
    old = b"""exported: 2026-11-13T03:37:58
questions:
  - number: 1
    question: |2-
      hello
    answers:
      - chatbot: 2
        llm: anthropic:claude-sonnet-5-5
        language: ""
        seconds: 1.2
        feedback: 1
        response: |2-
          hi there
"""
    histories, judged = parse_export(old)
    assert histories[1][1] == {"role": "assistant", "content": "hi there", "model": "claude_cli:claude-sonnet-5-5", "language": None,
                               "feedback": 1, "seconds": 1.2}
    assert judged == {}


def test_bad_values_do_not_crash():
    doc = {"questions": [{"question": "q", "answers": [{"chatbot": [1], "response": "r", "feedback": "x", "seconds": "slow"}, "junk"]}]}
    histories, _ = parse_export(yaml.safe_dump(doc).encode())
    assert histories[0][1]["content"] == "r" and histories[0][1]["feedback"] == 0 and "seconds" not in histories[0][1]


def test_not_an_export():
    with pytest.raises(ValueError):
        parse_export(b"- just\n- a list\n")
    with pytest.raises(ValueError):
        parse_export(b"key: [unclosed")


def test_markdown():
    text = export_markdown(sample(), {1: {"judge": "ollama:gemma4", "content": "fine"}})
    assert "## [1] What? 📎 `a.txt`" in text and "### Chatbot 1 · anthropic:claude-opus-5-5 · 2.3s · 🏆 best · 👍" in text
    assert "### Comparison · ollama:gemma4" in text
