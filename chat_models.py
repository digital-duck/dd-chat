"""The models the Multi-Chats page offers: what each provider has, and which of them you chose on the Config LLM page.

The choice is kept in `chat_models.json` under the GraphLang home (`{"openrouter": ["meta-llama/..."], ...}`). A provider with no entry
there offers everything it has (Anthropic: the four names below; Ollama: every local chat model); OpenRouter has hundreds of models,
so it offers none until some are chosen.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import streamlit as st

ANTHROPIC = {"fable": "claude-fable-5-1", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
PROVIDERS = {"anthropic": "claude_cli", "openrouter": "openrouter", "ollama": "ollama"}   # shown name -> graflang adapter (Anthropic through the Claude CLI)
OLLAMA_URL = "http://localhost:11434"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
CONFIG = Path(os.environ.get("GRAFLANG_HOME") or Path.home() / ".graflang") / "chat_models.json"


def _get_json(url: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
        return json.load(r)


@st.cache_data(ttl=60, show_spinner=False)
def ollama_models() -> list[str]:
    """The local Ollama models that can chat (embedding models are left out); empty when Ollama is not running."""
    try:
        names = sorted(m["name"] for m in _get_json(OLLAMA_URL + "/api/tags", timeout=2).get("models", []))
    except OSError:
        return []
    chat = []
    for name in names:
        try:
            capabilities = _get_json(OLLAMA_URL + "/api/show", {"model": name}, timeout=2).get("capabilities")
        except OSError:
            capabilities = None
        if capabilities is None or "completion" in capabilities:      # an older Ollama does not say: keep the model
            chat.append(name)
    return chat


def ollama_context(model: str) -> int | None:
    """The context window (tokens) Ollama loaded `model` with, from its /api/ps; None when it does not say (an older Ollama) or is not running."""
    try:
        for m in _get_json(OLLAMA_URL + "/api/ps", timeout=2).get("models", []):
            if model in (m.get("name"), m.get("model")) and m.get("context_length"):
                return int(m["context_length"])
    except (OSError, ValueError):
        pass
    return None


@st.cache_data(ttl=3600, show_spinner="asking OpenRouter for its models")
def openrouter_models() -> list[dict]:
    """OpenRouter's catalogue (a public list, no key needed): id, name, context length, price in $ per million tokens."""
    def per_million(price) -> float | None:
        try:
            return round(float(price) * 1_000_000, 4)
        except (TypeError, ValueError):
            return None
    rows = [{"id": m["id"], "name": m.get("name", ""), "context": m.get("context_length"),
             "prompt $/M": per_million((m.get("pricing") or {}).get("prompt")),
             "completion $/M": per_million((m.get("pricing") or {}).get("completion"))} for m in _get_json(OPENROUTER_URL).get("data", [])]
    return sorted(rows, key=lambda r: r["id"])


def available(provider: str) -> list[dict]:
    """What the provider has, as rows with at least an `id`. Raises OSError when a service cannot be reached."""
    if provider == "anthropic":                  # the Claude CLI cannot list its models: the known names (add others by hand)
        return [{"id": mid, "name": name} for name, mid in ANTHROPIC.items()]
    if provider == "openrouter":
        return openrouter_models()
    return [{"id": m} for m in ollama_models()]


def _config() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def has_choice(provider: str) -> bool:
    return isinstance(_config().get(provider), list)


def selected(provider: str) -> list[str]:
    """The model ids the Multi-Chats dropdown offers for `provider`."""
    chosen = _config().get(provider)
    if isinstance(chosen, list):
        return [str(m) for m in chosen]
    if provider == "anthropic":
        return list(ANTHROPIC.values())
    if provider == "ollama":
        return ollama_models()
    return []


def save_selected(provider: str, ids: list[str]) -> None:
    config = _config()
    config[provider] = sorted(set(ids))
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG)


def short_name(provider: str, model: str) -> str:
    """The name shown in a column heading: `sonnet` for `claude-sonnet-5-5`."""
    return next((n for n, mid in ANTHROPIC.items() if mid == model), model) if provider == "anthropic" else model
