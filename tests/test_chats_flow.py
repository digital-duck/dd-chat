"""The Chats page end to end, with the model calls replaced by a fake (no model is called)."""
import pytest
from streamlit.testing.v1 import AppTest

import chat_db
import chat_engine


@pytest.fixture
def calls(monkeypatch):
    """A fake chat_engine.ask: replies `<model> says: <question>`, and fails for questions containing "boom" (until `fixed`)."""
    log = {"asked": [], "fixed": False}

    def fake_ask(question, history, turn):
        log["asked"].append((turn.llm, question, len(history)))
        if "boom" in question and not log["fixed"] and turn.llm.endswith("sonnet-5-5"):
            raise RuntimeError("the model is down")
        return chat_engine.Answer(f"{turn.llm} says: {question} (#{len(log['asked'])})", 0.1, 50, 10, 5, 0.001)
    monkeypatch.setattr(chat_engine, "ask", fake_ask)
    return log


def start(bots=2):
    at = AppTest.from_file("../src/app.py", default_timeout=30)
    at.session_state["bots"] = bots
    at.session_state["provider_0"] = at.session_state["provider_1"] = "anthropic"
    at.session_state["model_anthropic_0"] = "claude-opus-5-5"
    at.session_state["model_anthropic_1"] = "claude-sonnet-5-5"
    return at.run()


def ask(at, text):
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception
    return at


def test_ask_saves_and_numbers(calls):
    at = ask(start(), "What is a graph?")
    h0, h1 = at.session_state["history_0"], at.session_state["history_1"]
    assert h0[0]["q"] == h1[0]["q"] == 1
    assert h0[1]["content"].startswith("claude_cli:claude-opus-5-5 says") and h0[1]["cost_usd"] == 0.001
    saved = chat_db.load_session(at.session_state["session_id"])
    assert len(saved[0]) == len(saved[1]) == 2


def test_twins_are_asked_once_and_stay_in_step(calls):
    at = start()
    at.session_state["model_anthropic_1"] = "claude-opus-5-5"         # both chatbots the same
    at.run()
    ask(at, "one")
    ask(at, "two")
    h0, h1 = at.session_state["history_0"], at.session_state["history_1"]
    assert len(calls["asked"]) == 2                                   # once per question, not twice
    assert h1[1]["shared_from"] == 1 and h1[3]["content"] == h0[3]["content"] and len(h1) == 4


def test_failure_then_retry(calls):
    at = ask(start(), "boom")
    assert len(at.session_state["history_0"]) == 2 and at.session_state["history_1"] == []
    assert "the model is down" in at.session_state["error_1"]
    calls["fixed"] = True
    at.button(key="retry_btn_1").click().run()
    assert not at.exception
    h1 = at.session_state["history_1"]
    assert len(h1) == 2 and h1[0]["q"] == 1 and "error_1" not in at.session_state


def test_redo_replaces_the_last_answer(calls):
    at = ask(start(), "hello")
    before = at.session_state["history_0"][1]["content"]
    at.button(key="redo_0").click().run()
    assert not at.exception
    h0 = at.session_state["history_0"]
    assert len(h0) == 2 and h0[1]["content"] != before and calls["asked"][-1][2] == 0     # asked without the replaced turn


def test_best_pick_is_one_per_question(calls):
    at = ask(start(), "pick one")
    at.button(key="best_0_1").click().run()
    at.button(key="best_1_1").click().run()
    assert not at.session_state["history_0"][1].get("best") and at.session_state["history_1"][1]["best"] == 1


def test_no_number_gap_when_nothing_is_asked(calls):
    at = start(bots=1)
    at.session_state["provider_0"] = "openai"                         # nothing chosen for OpenAI: no model
    at.run()
    ask(at, "nobody hears this")
    assert "qnum" not in at.session_state or at.session_state["qnum"] == 0
    assert any("No chatbot has a model" in e.value for e in at.error)
