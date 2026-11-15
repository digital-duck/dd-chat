"""How a message reads on the page, shared by the Chats and Review pages: the question line, the reply, and the facts under it."""
from __future__ import annotations

import streamlit as st

from chat_text import tidy_markdown
from link_check import flag_dead, summary

FEEDBACK = {1: "👍", -1: "👎"}
OLLAMA_CTX_DEFAULT = 4096


def question_line(m: dict, number: int) -> str:
    return f"**[{m.get('q', number)}]** {m.get('shown') or m['content']}"


def reply_body(m: dict) -> str:
    return flag_dead(tidy_markdown(m["content"]), m.get("links"))


def money(cost: float | None) -> str:
    if cost is None:
        return ""
    return "free" if cost == 0 else f"${cost:.4f}" if cost < 1 else f"${cost:.2f}"


def facts(m: dict, hide_model: bool = False, show_feedback: bool = False) -> str:
    """The caption under a reply: model, seconds, tokens, cost, language (and the thumbs, on the read-only Review page)."""
    parts = []
    if m.get("model") and not hide_model:
        parts.append(m["model"])
    if isinstance(m.get("seconds"), (int, float)):
        parts.append(f"{m['seconds']:.1f}s")
    if m.get("input_tokens") or m.get("output_tokens"):
        rough = "~" if str(m.get("model", "")).startswith("claude_cli:") else ""          # the Claude CLI's counts are estimates
        parts.append(f"{rough}{m.get('input_tokens', 0):,}→{rough}{m.get('output_tokens', 0):,} tokens")
    if money(m.get("cost_usd")):
        parts.append(money(m.get("cost_usd")))
    if m.get("temperature") is not None:
        parts.append(f"t={m['temperature']:g}")
    if m.get("language"):
        parts.append(m["language"])
    if m.get("shared_from"):
        parts.append(f"same as chatbot {m['shared_from']}")
    if m.get("best"):
        parts.append("🏆 best")
    if show_feedback and m.get("feedback") in FEEDBACK:
        parts.append(FEEDBACK[m["feedback"]])
    return " · ".join(parts)


def overflow_warning(m: dict) -> str | None:
    """A warning when an Ollama prompt was probably bigger than the model's context window (Ollama then drops the start of it)."""
    if not str(m.get("model", "")).startswith("ollama:"):
        return None
    ctx = m.get("ctx_tokens") or OLLAMA_CTX_DEFAULT
    if (m.get("est_tokens") or 0) <= ctx:
        return None
    return (f"⚠️ this prompt is about {m['est_tokens']:,} tokens but Ollama's context window is {ctx:,}: the start of it was probably dropped. "
            "Start `ollama serve` with a bigger `OLLAMA_CONTEXT_LENGTH`, lower this chatbot's turns, or click New to start a fresh chat.")


def show_reply(m: dict, hide_model: bool = False, show_feedback: bool = False) -> None:
    """The reply with its caption, link summary and context warning (inside the caller's `st.chat_message`)."""
    st.markdown(reply_body(m))
    caption = facts(m, hide_model, show_feedback)
    if caption:
        st.caption(caption)
    if m.get("links"):
        st.caption(summary(m["links"]))
    warning = overflow_warning(m)
    if warning:
        st.warning(warning)
