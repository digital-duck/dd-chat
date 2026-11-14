"""Tests run against a temporary database, never ~/.graflang/multi_chat.db."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chat_db  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_db, "DB", tmp_path / "multi_chat.db")
    yield tmp_path / "multi_chat.db"
