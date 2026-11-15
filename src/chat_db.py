"""SQLite persistence for the app: chat sessions, and settings (Chats-page widgets, Settings-page model choices, presets).

The file is `multi_chat.db` under the GraphLang home (`~/.graflang`, or $GRAFLANG_HOME). Tables:
  settings(key, value)                one JSON value per key
  sessions(id, title, created, updated, disabled, rev)    disabled = 1: soft-deleted, hidden from the lists but kept;
                                      rev: bumped by every save, so two browser tabs cannot overwrite each other's chat (see save_session)
  messages(session_id, bot, pos, role, content, ...)      one row per message; MSG_FIELDS lists the columns
  judgements(session_id, q, judge, content, seconds)      a model's comparison of the answers to question q
`bot` is the chatbot number minus one; `pos` the message's place in that chatbot's history.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

DB = Path(os.environ.get("GRAFLANG_HOME") or Path.home() / ".graflang") / "multi_chat.db"
COLUMNS = {             # message field -> column type; every field but role and content may be missing from a message
    "role": "TEXT NOT NULL", "content": "TEXT NOT NULL", "shown": "TEXT", "q": "INTEGER", "model": "TEXT", "seconds": "REAL",
    "feedback": "INTEGER DEFAULT 0", "est_tokens": "INTEGER", "ctx_tokens": "INTEGER", "language": "TEXT", "links": "TEXT",
    "input_tokens": "INTEGER", "output_tokens": "INTEGER", "cost_usd": "REAL", "best": "INTEGER DEFAULT 0", "shared_from": "INTEGER",
    "temperature": "REAL", "instructions": "TEXT",
}
MSG_FIELDS = tuple(COLUMNS)
JSON_FIELDS = ("links",)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0, rev INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS messages (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, bot INTEGER NOT NULL, pos INTEGER NOT NULL,
  {", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())}, PRIMARY KEY (session_id, bot, pos));
CREATE TABLE IF NOT EXISTS judgements (
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE, q INTEGER NOT NULL, judge TEXT, content TEXT NOT NULL, seconds REAL,
  PRIMARY KEY (session_id, q));
"""
_ready: set[str] = set()                # databases whose schema this process has already checked


def _migrate(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(sessions)")}
    for column, kind in (("disabled", "INTEGER NOT NULL DEFAULT 0"), ("rev", "INTEGER NOT NULL DEFAULT 0")):   # a database from before these
        if column not in have:
            con.execute(f"ALTER TABLE sessions ADD COLUMN {column} {kind}")
    have = {r["name"] for r in con.execute("PRAGMA table_info(messages)")}
    for column, kind in COLUMNS.items():
        if column not in have:
            con.execute(f"ALTER TABLE messages ADD COLUMN {column} {kind.replace(' NOT NULL', '')}")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A connection in one transaction (committed on success, rolled back on an error), closed afterwards."""
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        if str(DB) not in _ready:
            with con:
                _migrate(con)
            _ready.add(str(DB))
        with con:
            yield con
    finally:
        con.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---- settings

def get_setting(key: str, default: Any = None) -> Any:
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    try:
        return json.loads(row["value"]) if row else default
    except ValueError:
        return default


def set_setting(key: str, value) -> None:
    with connect() as con:
        con.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def delete_setting(key: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM settings WHERE key = ?", (key,))


# ---- chat sessions

def _title(histories: dict[int, list[dict]]) -> str:
    first = next((m.get("shown") or m["content"] for _, h in sorted(histories.items()) for m in h if m["role"] == "user"), "chat")
    return " ".join(first.split())[:80]


def _row(m: dict) -> tuple:
    return tuple(json.dumps(m[f]) if f in JSON_FIELDS and m.get(f) else m.get(f) for f in MSG_FIELDS)


def _write(con: sqlite3.Connection, session_id: int, histories: dict[int, list[dict]], judgements: dict[int, dict] | None) -> None:
    con.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
    con.executemany(
        f"INSERT INTO messages(session_id, bot, pos, {', '.join(MSG_FIELDS)}) VALUES(?, ?, ?, {', '.join('?' * len(MSG_FIELDS))})",
        [(session_id, bot, pos, *_row(m)) for bot, history in histories.items() for pos, m in enumerate(history)])
    if judgements is not None:
        con.execute("DELETE FROM judgements WHERE session_id = ?", (session_id,))
        con.executemany("INSERT INTO judgements(session_id, q, judge, content, seconds) VALUES(?, ?, ?, ?, ?)",
                        [(session_id, int(q), j.get("judge"), j["content"], j.get("seconds")) for q, j in judgements.items()])


def save_session(session_id: int | None, histories: dict[int, list[dict]], rev: int = 0,
                 judgements: dict[int, dict] | None = None) -> tuple[int | None, int, bool]:
    """Write every chatbot's history (and the judgements) as one session; returns (session id, its new rev, forked).

    `rev` is the revision this browser session last loaded or wrote. The write goes into `session_id` only when nobody else has written
    to it since and it is not disabled; otherwise the chat is saved as a new session (forked = True), so no tab overwrites another's
    messages and a chat disabled on the Review page does not keep growing out of sight. Nothing to save: (session_id, rev, False)."""
    if not any(histories.values()):
        return session_id, rev, False
    title = _title(histories)
    with connect() as con:
        if session_id is not None:
            moved = con.execute("UPDATE sessions SET title = ?, updated = ?, rev = rev + 1 WHERE id = ? AND rev = ? AND disabled = 0",
                                (title, _now(), session_id, rev)).rowcount
            if moved:
                _write(con, session_id, histories, judgements)
                return session_id, rev + 1, False
        forked = session_id is not None
        new_id = int(con.execute("INSERT INTO sessions(title, created, updated, rev) VALUES(?, ?, ?, 1)", (title, _now(), _now())).lastrowid or 0)
        _write(con, new_id, histories, judgements or {})
        return new_id, 1, forked


def _message(r: sqlite3.Row) -> dict:
    m = {f: r[f] for f in MSG_FIELDS if r[f] is not None}
    for f in JSON_FIELDS:
        if f in m:
            try:
                m[f] = json.loads(m[f])
            except ValueError:
                del m[f]
    m["feedback"] = r["feedback"] or 0
    if not m.get("best"):
        m.pop("best", None)
    return m


def load_session(session_id: int) -> dict[int, list[dict]]:
    """{bot index: history} for a session (messages as the Chats page keeps them)."""
    out: dict[int, list[dict]] = {}
    with connect() as con:
        for r in con.execute("SELECT * FROM messages WHERE session_id = ? ORDER BY bot, pos", (session_id,)):
            out.setdefault(r["bot"], []).append(_message(r))
    return out


def load_judgements(session_id: int) -> dict[int, dict]:
    with connect() as con:
        return {r["q"]: {"judge": r["judge"], "content": r["content"], "seconds": r["seconds"]}
                for r in con.execute("SELECT * FROM judgements WHERE session_id = ? ORDER BY q", (session_id,))}


def session_rev(session_id: int) -> int:
    with connect() as con:
        row = con.execute("SELECT rev FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["rev"] if row else 0


def latest_session_id() -> int | None:
    with connect() as con:
        row = con.execute("SELECT id FROM sessions WHERE disabled = 0 ORDER BY updated DESC, id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_sessions(include_disabled: bool = False, search: str = "") -> list[dict]:
    """Newest first: id, title, created, updated, disabled, questions, chatbots, models. Disabled sessions only when asked for;
    with `search`, only the sessions whose title or any message contains it (any case)."""
    where, args = "(? OR s.disabled = 0)", [int(include_disabled)]
    if search.strip():
        where += (" AND (s.title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id"
                  " AND (m.content LIKE ? ESCAPE '\\' OR m.shown LIKE ? ESCAPE '\\')))")
        args += [_like(search.strip())] * 3
    with connect() as con:
        rows = con.execute(f"""
            SELECT s.id, s.title, s.created, s.updated, s.disabled,
                   (SELECT COUNT(DISTINCT q) FROM messages m WHERE m.session_id = s.id AND m.role = 'user') AS questions,
                   (SELECT COUNT(DISTINCT bot) FROM messages m WHERE m.session_id = s.id) AS chatbots,
                   (SELECT GROUP_CONCAT(model, ', ') FROM (SELECT DISTINCT model FROM messages m WHERE m.session_id = s.id AND model IS NOT NULL)) AS models,
                   (SELECT ROUND(SUM(cost_usd), 4) FROM messages m WHERE m.session_id = s.id) AS cost_usd
            FROM sessions s WHERE {where} ORDER BY s.updated DESC, s.id DESC""", args).fetchall()
    return [dict(r) for r in rows]


def set_session_disabled(session_id: int, disabled: bool) -> None:
    """Soft delete: a disabled session is hidden from the Review table (unless asked for) and is not restored by the Chats page; its data stays."""
    with connect() as con:
        con.execute("UPDATE sessions SET disabled = ? WHERE id = ?", (int(disabled), session_id))


def delete_session(session_id: int) -> None:
    """Hard delete: the session, its messages and judgements are gone."""
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def delete_disabled() -> int:
    """Hard-delete every disabled session; returns how many."""
    with connect() as con:
        return con.execute("DELETE FROM sessions WHERE disabled = 1").rowcount


def leaderboard() -> list[dict]:
    """Per model, over the enabled sessions: answers, 👍, 👎, wins (🏆 picks), contests (questions it answered that got a 🏆 pick),
    average seconds, output tokens and cost per answer, and the total cost. An answer copied from a twin chatbot is not counted."""
    with connect() as con:
        rows = con.execute("""
            WITH answers AS (
              SELECT m.session_id, u.q, m.model, COALESCE(m.feedback, 0) AS feedback, COALESCE(m.best, 0) AS best, m.seconds, m.output_tokens, m.cost_usd
              FROM messages m JOIN sessions s ON s.id = m.session_id
              JOIN messages u ON u.session_id = m.session_id AND u.bot = m.bot AND u.pos = m.pos - 1      -- the question it answers
              WHERE s.disabled = 0 AND m.role = 'assistant' AND m.model IS NOT NULL AND m.shared_from IS NULL),
            picked AS (SELECT DISTINCT session_id, q FROM answers WHERE best = 1)
            SELECT a.model AS model, COUNT(*) AS answers,
                   SUM(a.feedback = 1) AS up, SUM(a.feedback = -1) AS down, SUM(a.best = 1) AS wins,
                   SUM(p.q IS NOT NULL) AS contests,
                   ROUND(AVG(a.seconds), 1) AS avg_seconds, ROUND(AVG(a.output_tokens)) AS avg_output_tokens,
                   ROUND(AVG(a.cost_usd), 5) AS avg_cost_usd, ROUND(SUM(a.cost_usd), 4) AS total_cost_usd
            FROM answers a LEFT JOIN picked p ON p.session_id = a.session_id AND p.q = a.q
            GROUP BY a.model ORDER BY wins DESC, up - down DESC, answers DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["win_rate"] = round(d["wins"] / d["contests"], 2) if d["contests"] else None
        out.append(d)
    return out
