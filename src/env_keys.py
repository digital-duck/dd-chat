"""Where the API keys come from. No Streamlit code: the model adapters (in worker threads) read the keys from `os.environ`, so every source ends up there.

Precedence, highest first (python-dotenv's convention: a real environment variable beats the `.env` file, which only supplies defaults):
  1. a key typed on the Settings page (kept in memory for this run of the app, never written to disk)
  2. the environment variables of the process
  3. a `.env` file: `./.env` (where the app was started), then `$DD_CHAT_HOME/.env` (default `~/.dd-chat/.env`); the first file that sets a variable wins
A `.env` line with an empty value (as in `example.env`) counts as not set.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

HOME = Path(os.environ.get("DD_CHAT_HOME") or Path.home() / ".dd-chat")
ENV_FILES = (Path(".env").resolve(), HOME / ".env")

_dotenv: dict[str, str] = {}        # variable -> value from the .env files
_process: dict[str, str] = {}       # variable -> value the process itself had (kept, so a typed key can be forgotten again)
_typed: dict[str, str] = {}         # variable -> key typed in the UI
_applied: set[str] = set()          # variables whose value in os.environ was written by this module


def _apply(var: str) -> None:
    """Put the winning value of `var` into `os.environ` (typed, else the process's own, else .env), or remove what this module put there."""
    value = _typed.get(var) or _process.get(var) or _dotenv.get(var)
    if value:
        os.environ[var] = value
        _applied.add(var)
    elif var in _applied:
        os.environ.pop(var, None)
        _applied.discard(var)


def load() -> None:
    """Read the .env files and the environment, and set `os.environ` by the precedence above."""
    _dotenv.clear()
    for path in reversed(ENV_FILES):                    # the later (lower-priority) file goes first, the earlier one overwrites it
        if path.is_file():
            _dotenv.update({k: v for k, v in dotenv_values(path).items() if v})
    _process.update({k: v for k, v in os.environ.items() if v and k not in _applied})
    for var in _dotenv.keys() | _typed.keys():
        _apply(var)


def source(var: str) -> str | None:
    """Where the value of `var` comes from: "typed here", "environment", ".env", or None when no source has it."""
    if _typed.get(var):
        return "typed here"
    return "environment" if _process.get(var) else ".env" if _dotenv.get(var) else None


def set_typed(var: str, value: str) -> None:
    """Use `value` for `var` (it outranks the environment and .env); an empty value forgets it."""
    if value.strip():
        _typed[var] = value.strip()
    else:
        _typed.pop(var, None)
    _apply(var)


load()
