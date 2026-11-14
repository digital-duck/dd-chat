"""Multi-Chat: ask one question, let several LLMs answer side by side. A showcase of GraphLang.

    streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Multi-Chat · GraphLang", page_icon="💬", layout="wide")

pages = [
    st.Page("views/multi_chat.py", title="Chats", icon="💬", default=True),
    st.Page("views/review.py", title="Review", icon="🔍"),
    st.Page("views/config_llm.py", title="Settings", icon="⚙️"),
    st.Page("views/about.py", title="About", icon="ℹ️"),
]
st.navigation(pages).run()
