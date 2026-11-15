"""The models the Chats page offers: what each provider has, and which of them you chose on the Settings page.

The choice is kept in the SQLite settings (key `chat_models`, `{"openrouter": ["meta-llama/..."], ...}`; see chat_db.py); an old `chat_models.json` is read once. A provider with no entry
there offers its known models (Anthropic: the four names below; Ollama: every local chat model; DeepSeek and Qwen: their main models);
OpenRouter and OpenAI have many models, so they offer none until some are chosen.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

import chat_db
import env_keys

ANTHROPIC = {"fable": "claude-fable-5-2", "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


@dataclass(frozen=True)
class Provider:
    adapter: str                    # the dd-llm adapter graflang calls
    key_env: str | None = None      # the environment variable with its API key, if it needs one
    images: str | None = None       # how it takes pictures: "openai" (image_url parts) or "anthropic" (image blocks); None: it cannot
    billed: bool = True
    known: tuple[str, ...] = ()     # the models offered while none are chosen on the Settings page
    note: str = ""


INFO = {
    "anthropic": Provider("claude_cli", known=tuple(ANTHROPIC.values()), note="through the Claude CLI (no images, no temperature)"),
    "anthropic-api": Provider("anthropic", "ANTHROPIC_API_KEY", "anthropic", known=tuple(ANTHROPIC.values()), note="the Anthropic API"),
    "openrouter": Provider("openrouter", "OPENROUTER_API_KEY", "openai", note="hundreds of hosted models"),
    "openai": Provider("openai", "OPENAI_API_KEY", "openai", note="the OpenAI API"),
    "deepseek": Provider("deepseek", "DEEPSEEK_API_KEY", known=("deepseek-chat", "deepseek-reasoner"), note="the DeepSeek API"),
    "qwen": Provider("qwen", "DASHSCOPE_API_KEY", "openai", known=("qwen-plus", "qwen-max", "qwen-turbo"), note="Alibaba DashScope"),
    "ollama": Provider("ollama", None, "openai", billed=False, note="local models, free"),
}
PROVIDERS = {name: p.adapter for name, p in INFO.items()}       # shown name -> graflang adapter (Anthropic through the Claude CLI)
ADAPTER_PROVIDER = {p.adapter: name for name, p in INFO.items()}
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
CONFIG = Path(os.environ.get("DD_CHAT_HOME") or Path.home() / ".dd-chat") / "chat_models.json"


def _get_json(url: str, body: dict | None = None, timeout: float = 10, headers: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}), timeout=timeout) as r:
        return json.load(r)


def missing_key(provider: str) -> str | None:
    """The name of the API-key variable `provider` needs and does not have, else None."""
    env = INFO[provider].key_env
    return env if env and not os.environ.get(env) else None


def key_source(provider: str) -> str | None:
    """Where `provider`'s API key comes from (".env", "environment", "typed here"), None when it has none; see env_keys.py."""
    env = INFO[provider].key_env
    return env_keys.source(env) if env else None


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
    """The context window (tokens) Ollama loaded `model` with, from its /api/ps (ask after a call: the model is loaded then); None when it
    does not say (an older Ollama) or is not running."""
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


@st.cache_data(ttl=3600, show_spinner="asking OpenAI for its models")
def openai_models() -> list[dict]:
    """The models the OpenAI key may use; empty without a key."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return []
    data = _get_json("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}).get("data", [])
    return sorted(({"id": m["id"], "name": m.get("owned_by", "")} for m in data), key=lambda r: r["id"])


def prices(provider: str, model: str) -> tuple[float, float] | None:
    """($ per million prompt tokens, $ per million completion tokens) when known: OpenRouter's catalogue, and 0 for local Ollama."""
    if provider == "ollama":
        return (0.0, 0.0)
    if provider == "openrouter":
        try:
            row = next((r for r in openrouter_models() if r["id"] == model), None)
        except OSError:
            return None
        if row and row["prompt $/M"] is not None and row["completion $/M"] is not None:
            return (row["prompt $/M"], row["completion $/M"])
    return None


def available(provider: str) -> list[dict]:
    """What the provider has, as rows with at least an `id`. Raises OSError when a service cannot be reached."""
    if provider == "openrouter":
        return openrouter_models()
    if provider == "ollama":
        return [{"id": m} for m in ollama_models()]
    if provider == "openai":
        return openai_models()
    names = {mid: name for name, mid in ANTHROPIC.items()}          # the others cannot be listed here: their known names (add others by hand)
    return [{"id": mid, "name": names.get(mid, "")} for mid in INFO[provider].known]


def can_refresh(provider: str) -> bool:
    return provider in ("openrouter", "ollama", "openai")


def _config() -> dict:
    data = chat_db.get_setting("chat_models")
    if data is None:                                   # first run with the database: take over the old JSON file
        try:
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict) and data:
            chat_db.set_setting("chat_models", data)
    return data if isinstance(data, dict) else {}


def selected(provider: str, config: dict | None = None) -> list[str]:
    """The model ids the Chats page dropdown offers for `provider` (`config`: the saved choice, when the caller already has it)."""
    chosen = (_config() if config is None else config).get(provider)
    if isinstance(chosen, list):
        return [str(m) for m in chosen]
    if provider == "ollama":
        return ollama_models()
    return list(INFO[provider].known)


def saved_choice() -> dict:
    """The saved choice for every provider, read once (the Chats page asks for each chatbot)."""
    return _config()


def save_selected(provider: str, ids: list[str]) -> None:
    config = _config()
    config[provider] = sorted(set(ids))
    chat_db.set_setting("chat_models", config)


def short_name(provider: str, model: str) -> str:
    """The name shown in a column heading: `sonnet` for `claude-sonnet-5-5`."""
    if provider in ("anthropic", "anthropic-api"):
        return next((n for n, mid in ANTHROPIC.items() if mid == model), model)
    return model


def provider_of(llm: str) -> tuple[str, str]:
    """(shown provider, model) for an `adapter:model` id."""
    adapter, _, model = llm.partition(":")
    return ADAPTER_PROVIDER.get(adapter, adapter), model
