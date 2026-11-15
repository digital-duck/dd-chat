"""The pages render without errors (no model is called)."""
from streamlit.testing.v1 import AppTest

import chat_db


def page():
    at = AppTest.from_file("../src/app.py", default_timeout=30)
    at.run()
    return at


def test_chats_page_renders():
    at = page()
    assert not at.exception
    assert at.header[0].value == "💬 Chats"


def test_review_and_leaderboard_render():
    chat_db.save_session(None, {0: [{"role": "user", "content": "q", "q": 1},
                                    {"role": "assistant", "content": "a https://x.org/(b)", "model": "ollama:x", "seconds": 1.0, "best": 1}]})
    at = page()
    at.switch_page("views/review.py").run()
    assert not at.exception
    at.segmented_control(key="review_mode").set_value("Leaderboard").run()
    assert not at.exception and len(at.dataframe) == 1


def test_settings_and_about_render():
    at = page()
    for path in ("views/config_llm.py", "views/about.py"):
        at.switch_page(path).run()
        assert not at.exception, path


def test_notes_page_create_update_delete():
    at = page()
    at.switch_page("views/notes.py").run()
    assert not at.exception and at.header[0].value == "📝 Notes"
    at.text_input(key="notes_title_new_0").set_value("First note")
    at.text_area(key="notes_text_new_0").set_value("some **text**")
    next(b for b in at.button if b.label == "Create").click().run()
    assert not at.exception
    [note] = chat_db.list_notes()
    assert note["title"] == "First note"
    suffix = f"{note['id']}_{at.session_state['notes_form_n']}"
    at.text_input(key=f"notes_title_{suffix}").set_value("Renamed")
    next(b for b in at.button if b.label == "Update").click().run()
    assert not at.exception and chat_db.get_note(note["id"])["title"] == "Renamed"
    next(b for b in at.button if b.label == "Delete").click().run()
    assert not at.exception and not chat_db.list_notes() and chat_db.get_note(note["id"])["disabled"] == 1

    at.text_input(key="notes_search").set_value("nothing like it").run()
    at.checkbox(key="notes_show_disabled").check().run()
    next(b for b in at.button if b.label == "Clear").click().run()
    assert not at.exception and at.text_input(key="notes_search").value == ""
