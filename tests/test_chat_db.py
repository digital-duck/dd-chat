import sqlite3

import chat_db


def chat(*pairs, bot_models=("claude_cli:a", "ollama:b")):
    """Two chatbots asked the same questions; `pairs` are (question, [reply per chatbot])."""
    histories = {i: [] for i in range(len(bot_models))}
    for q, (question, replies) in enumerate(pairs, 1):
        for i, reply in enumerate(replies):
            histories[i] += [{"role": "user", "content": question, "q": q},
                             {"role": "assistant", "content": reply, "model": bot_models[i], "seconds": 1.5, "feedback": 0}]
    return histories


def test_round_trip_with_new_fields():
    h = chat(("hi?", ["a", "b"]))
    h[0][1].update(links={"https://x.org": "ok"}, cost_usd=0.01, input_tokens=10, output_tokens=5, best=1, temperature=0.3, instructions="brief")
    sid, rev, forked = chat_db.save_session(None, h, judgements={1: {"judge": "ollama:b", "content": "same", "seconds": 2.0}})
    assert (rev, forked) == (1, False)
    loaded = chat_db.load_session(sid)
    assert loaded[0][1]["links"] == {"https://x.org": "ok"} and loaded[0][1]["best"] == 1 and loaded[0][1]["cost_usd"] == 0.01
    assert "best" not in loaded[1][1]
    assert chat_db.load_judgements(sid)[1]["content"] == "same"


def test_save_in_place_bumps_rev():
    sid, rev, _ = chat_db.save_session(None, chat(("q", ["a", "b"])))
    sid2, rev2, forked = chat_db.save_session(sid, chat(("q", ["a", "b"]), ("q2", ["c", "d"])), rev)
    assert (sid2, rev2, forked) == (sid, 2, False)


def test_second_tab_does_not_overwrite_first():
    sid, rev, _ = chat_db.save_session(None, chat(("q", ["a", "b"])))
    chat_db.save_session(sid, chat(("q", ["a", "b"]), ("tab A", ["1", "2"])), rev)          # tab A writes first
    other, _, forked = chat_db.save_session(sid, chat(("q", ["a", "b"]), ("tab B", ["3", "4"])), rev)     # tab B still has the old rev
    assert forked and other != sid
    assert chat_db.load_session(sid)[0][2]["content"] == "tab A"
    assert chat_db.load_session(other)[0][2]["content"] == "tab B"


def test_disabled_session_is_not_written():
    sid, rev, _ = chat_db.save_session(None, chat(("q", ["a", "b"])))
    chat_db.set_session_disabled(sid, True)
    other, _, forked = chat_db.save_session(sid, chat(("q", ["a", "b"]), ("more", ["c", "d"])), rev)
    assert forked and other != sid and len(chat_db.load_session(sid)[0]) == 2


def test_search_and_lists():
    chat_db.save_session(None, chat(("about graphs", ["nodes and edges", "x"])))
    second, _, _ = chat_db.save_session(None, chat(("about cats", ["meow 100%", "y"])))
    assert [s["title"] for s in chat_db.list_sessions(search="EDGES")] == ["about graphs"]
    assert [s["title"] for s in chat_db.list_sessions(search="100%")] == ["about cats"]
    assert chat_db.list_sessions(search="_") == []                     # LIKE wildcards are matched literally
    chat_db.set_session_disabled(second, True)
    assert len(chat_db.list_sessions()) == 1 and len(chat_db.list_sessions(include_disabled=True)) == 2
    assert chat_db.delete_disabled() == 1 and len(chat_db.list_sessions(include_disabled=True)) == 1


def test_leaderboard_counts_wins_and_skips_shared():
    h = chat(("q1", ["a", "b"]), ("q2", ["c", "d"]))
    h[0][1]["best"] = 1
    h[1][3]["best"] = 1
    h[1][1]["feedback"] = 1
    h[1][3]["shared_from"] = 1          # copied from chatbot 1: not counted
    chat_db.save_session(None, h)
    board = {r["model"]: r for r in chat_db.leaderboard()}
    assert board["claude_cli:a"]["wins"] == 1 and board["claude_cli:a"]["contests"] == 1
    assert board["ollama:b"]["answers"] == 1 and board["ollama:b"]["up"] == 1 and board["ollama:b"]["win_rate"] == 0


def test_old_database_is_migrated(temp_db):
    con = sqlite3.connect(temp_db)
    con.executescript("""
        CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE messages (session_id INTEGER NOT NULL, bot INTEGER NOT NULL, pos INTEGER NOT NULL, role TEXT NOT NULL,
          content TEXT NOT NULL, shown TEXT, q INTEGER, model TEXT, seconds REAL, feedback INTEGER DEFAULT 0, PRIMARY KEY (session_id, bot, pos));
        INSERT INTO sessions VALUES (1, 'old', '2026-01-01', '2026-01-01');
        INSERT INTO messages VALUES (1, 0, 0, 'user', 'q', NULL, 1, NULL, NULL, 0);""")
    con.commit()
    con.close()
    chat_db._ready.clear()
    assert chat_db.load_session(1) == {0: [{"role": "user", "content": "q", "q": 1, "feedback": 0}]}
    assert chat_db.session_rev(1) == 0
    _, _, forked = chat_db.save_session(1, {0: [{"role": "user", "content": "q2", "q": 1}]}, 0)
    assert not forked


def test_settings():
    chat_db.set_setting("k", {"a": [1]})
    assert chat_db.get_setting("k") == {"a": [1]}
    chat_db.delete_setting("k")
    assert chat_db.get_setting("k", "none") == "none"


def test_notes_create_update_soft_delete():
    note_id = chat_db.create_note("Title", "one line", "# text", [("a.png", "image/png", b"\x89PNG"), ("b.txt", None, b"hello")])
    assert [n["files"] for n in chat_db.list_notes()] == [2]
    assert chat_db.update_note(note_id, "New title", "d", "body", [("c.pdf", "application/pdf", b"%PDF")])
    note = chat_db.get_note(note_id)
    assert (note["title"], note["note"]) == ("New title", "body")
    files = chat_db.note_files(note_id)
    assert [f["name"] for f in files] == ["a.png", "b.txt", "c.pdf"] and files[1]["size"] == 5
    assert chat_db.note_file_data(files[1]["id"]) == b"hello"
    chat_db.delete_note_file(files[0]["id"])
    assert len(chat_db.note_files(note_id)) == 2
    assert chat_db.list_notes(search="c.pdf")[0]["id"] == note_id and not chat_db.list_notes(search="nowhere")
    chat_db.set_note_disabled(note_id, True)
    assert not chat_db.list_notes() and chat_db.list_notes(include_disabled=True)[0]["disabled"] == 1
    assert len(chat_db.note_files(note_id)) == 2            # soft delete keeps the files
    assert not chat_db.update_note(999, "x", "", "")


def test_note_needs_a_title():
    import pytest
    with pytest.raises(ValueError):
        chat_db.create_note("  ")
