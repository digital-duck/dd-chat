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
