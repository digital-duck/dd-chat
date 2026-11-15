"""Multi-Chat: ask one question, let several LLMs answer side by side. A showcase of GraphLang.

    streamlit run src/app.py        (from the repo root, so .streamlit/config.toml applies)
"""
import streamlit as st

st.set_page_config(page_title="Multi-Chat · GraphLang", page_icon="💬", layout="wide")

pages = [
    st.Page("views/multi_chat.py", title="Chats", icon="💬", default=True),
    st.Page("views/review.py", title="Review", icon="🔍"),
    st.Page("views/notes.py", title="Notes", icon="📝"),
    st.Page("views/config_llm.py", title="Settings", icon="⚙️"),
    st.Page("views/about.py", title="About", icon="ℹ️"),
]
st.navigation(pages).run()
