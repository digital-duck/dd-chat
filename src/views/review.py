"""The Review page: look back at chats, and see how the models compare.

Sessions view: the saved chat sessions (SQLite, see chat_db.py) in a table, searchable; below it, the chat of the selected row, or of an
imported export file, as a static copy of the Chats page (one column per chatbot). A session can be opened on the Chats page to go on
with it, disabled (hidden, kept) or deleted; an imported file can be saved as a session. Leaderboard view: per model, the answers,
thumbs and 🏆 picks across the saved sessions, with time and cost. Nothing is sent to any model here.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

import chat_db
from chat_export import parse_export
from chat_render import question_line, show_reply
from chat_text import FONT_SIZE

PANE_HEIGHT = 800
TABLE_FONT = FONT_SIZE                      # px, the session table's text: the app's text size
TABLE_ROW = 46                              # px, a table row
CHAT_FONT = FONT_SIZE                       # px, the chat text when the Chats page has no saved font size
state = st.session_state


def show_chat(histories: dict[int, list[dict]], judgements: dict[int, dict] | None = None) -> None:
    """A static Multi-Chat: a column per chatbot, its conversation in a scrolling pane; the comparisons below."""
    bots = sorted(i for i, h in histories.items() if h)
    if not bots:
        st.info("This chat has no messages.")
        return
    for column, i in zip(st.columns(len(bots)), bots):
        history = histories[i]
        model = next((m["model"] for m in history if m.get("model")), "no model")
        language = next((m["language"] for m in history if m.get("language")), None)
        with column:
            st.subheader(f"{i + 1} · {model}" + (f" · {language}" if language else ""), divider="gray")
            with st.container(height=PANE_HEIGHT, border=False):
                asked = 0
                for m in history:
                    with st.chat_message(m["role"]):
                        if m["role"] == "user":
                            asked += 1
                            st.markdown(question_line(m, asked))
                        else:
                            show_reply(m, show_feedback=True)
    if judgements:
        with st.expander("Comparisons", icon=":material/compare_arrows:", expanded=False):
            for q, j in sorted(judgements.items()):
                st.markdown(f"**[{q}]**")
                st.markdown(j["content"])
                st.caption(f"compared by {j.get('judge')}")


def open_in_chats(session_id: int) -> None:
    state["open_session_id"] = session_id
    st.switch_page("views/multi_chat.py")


st.header("🔍 Review")
chat_font = int(state.get("font_size") or chat_db.get_setting("multi_chat_settings", {}).get("font_size") or CHAT_FONT)     # the size set on the Chats page
st.markdown(f"<style>[data-testid='stChatMessage'] :is(p, li, code, td, th) {{ font-size: {chat_font}px; }}</style>", unsafe_allow_html=True)

view_mode = st.segmented_control("View", ["Sessions", "Leaderboard"], default="Sessions", key="review_mode", label_visibility="collapsed")

if view_mode == "Leaderboard":
    rows = chat_db.leaderboard()
    if not rows:
        st.info("No answers saved yet: ask something on the Chats page.")
        st.stop()
    frame = pd.DataFrame(rows)[["model", "answers", "wins", "contests", "win_rate", "up", "down", "avg_seconds", "avg_output_tokens",
                                "avg_cost_usd", "total_cost_usd"]]
    st.dataframe(frame, hide_index=True, alt="Per-model leaderboard: answers, best-answer wins, thumbs, time and cost", column_config={
        "model": st.column_config.TextColumn("Model", width="large"),
        "answers": st.column_config.NumberColumn("Answers"),
        "wins": st.column_config.NumberColumn("🏆 wins", help="answers picked as the best of their question"),
        "contests": st.column_config.NumberColumn("Contests", help="its answers to questions where a best answer was picked"),
        "win_rate": st.column_config.ProgressColumn("Win rate", min_value=0, max_value=1, format="percent"),
        "up": st.column_config.NumberColumn("👍"),
        "down": st.column_config.NumberColumn("👎"),
        "avg_seconds": st.column_config.NumberColumn("Avg s", format="%.1f"),
        "avg_output_tokens": st.column_config.NumberColumn("Avg out tokens", format="%d"),
        "avg_cost_usd": st.column_config.NumberColumn("Avg $", format="$%.5f"),
        "total_cost_usd": st.column_config.NumberColumn("Total $", format="$%.4f"),
    })
    st.caption("Over the enabled sessions; an answer copied from a twin chatbot is not counted. Pick the best answer to a question with 🏆 "
               "on the Chats page (blind mode hides the models until you do). Costs are what the provider reported, or OpenRouter's prices "
               "times the tokens; the Claude CLI's cost is not known.")
    st.stop()

# ---- sidebar: search and import
st.sidebar.header("Review")
search = st.sidebar.text_input("Search", key="review_search", placeholder="a word in a question or answer")
show_disabled = st.sidebar.checkbox("Show disabled sessions", key="review_show_disabled",
                                    help="disabled sessions are hidden from the table, not deleted; show them to restore or delete one")
if show_disabled:
    with st.sidebar.popover("Delete all disabled", icon=":material/delete_forever:", width="stretch"):
        st.write("Delete every disabled session for good? This cannot be undone.")
        if st.button("Delete them", key="review_purge", type="primary"):
            count = chat_db.delete_disabled()
            state["review_grid_n"] = state.get("review_grid_n", 0) + 1
            st.toast(f"{count} session(s) deleted")
            st.rerun()
uploaded = st.sidebar.file_uploader("Import", type=["yaml", "yml"], key="review_file",
                                    help="an exported GraphLang-*.chat.yaml file; it is shown below, and saved only if you ask")

# ---- a newly imported file is shown at once, and the table starts over with no row selected: whatever row is clicked next is then a new
# choice (a row that stayed selected from before the import could not be clicked to switch the view back)
if uploaded is not None and state.get("review_seen_file") != uploaded.file_id:
    state["review_seen_file"] = uploaded.file_id
    state["review_view"] = "import"
    state["review_seen_row"] = None
    state["review_grid_n"] = state.get("review_grid_n", 0) + 1
elif uploaded is None:
    state["review_seen_file"] = None

# ---- main: the table of saved sessions
sessions = chat_db.list_sessions(include_disabled=show_disabled, search=search)
selected_id = None
if sessions:
    frame = pd.DataFrame(sessions).rename(columns={"title": "first question", "cost_usd": "cost $"})
    frame["status"] = ["disabled" if d else "" for d in frame["disabled"]]
    frame = frame[["id", "updated", "created", "first question", "questions", "chatbots", "models", "cost $", "status"]]
    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_selection("single", use_checkbox=False)
    builder.configure_column("id", width=70)
    builder.configure_column("updated", width=170)
    builder.configure_column("created", width=170, hide=True)
    builder.configure_column("first question", flex=3, minWidth=260)
    builder.configure_column("questions", width=100)
    builder.configure_column("chatbots", width=100)
    builder.configure_column("models", flex=2, minWidth=200)
    builder.configure_column("cost $", width=90)
    builder.configure_column("status", width=110, hide=not show_disabled)
    builder.configure_grid_options(domLayout="normal", rowHeight=TABLE_ROW, headerHeight=TABLE_ROW)
    grid = AgGrid(frame, gridOptions=builder.build(), height=TABLE_ROW * 6, update_on=["selectionChanged"], key=f"review_grid_{state.get('review_grid_n', 0)}",
                  allow_unsafe_jscode=False,
                  custom_css={".ag-cell": {"font-size": f"{TABLE_FONT}px !important", "line-height": f"{TABLE_ROW - 2}px !important"},
                              ".ag-header-cell-text": {"font-size": f"{TABLE_FONT - 1}px !important", "font-weight": "600"}})
    rows = grid.selected_rows
    rows = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else (rows or [])
    selected_id = int(rows[0]["id"]) if rows else None
    st.caption(f"{len(sessions)} chat session(s) in `{chat_db.DB}`" + (f" containing {search!r}" if search else "") + ". Click a row to see its chat.")
    chosen = next((s for s in sessions if s["id"] == selected_id), None)
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Open in Chats", key="review_open", icon=":material/chat:", disabled=chosen is None or bool(chosen and chosen["disabled"]),
                     help="go on with this chat on the Chats page") and chosen:
            open_in_chats(chosen["id"])
        if chosen and chosen["disabled"]:
            if st.button("Restore", key="review_restore", help="bring the selected session back into the list"):
                chat_db.set_session_disabled(chosen["id"], False)
                st.rerun()
            with st.popover("Delete", icon=":material/delete_forever:"):
                st.write(f"Delete session {chosen['id']} for good? This cannot be undone.")
                if st.button("Delete it", key="review_delete", type="primary"):
                    chat_db.delete_session(chosen["id"])
                    state["review_grid_n"] = state.get("review_grid_n", 0) + 1
                    state["review_seen_row"] = None
                    st.rerun()
        elif st.button("Disable", key="review_disable", type="primary", disabled=chosen is None,
                       help="hide the selected session (a test, or a question of poor quality) from this list, the leaderboard and the Chats page; its data is kept") and chosen:
            chat_db.set_session_disabled(chosen["id"], True)
            state["review_grid_n"] = state.get("review_grid_n", 0) + 1        # the table starts over with no row selected
            state["review_seen_row"] = None
            st.rerun()
elif search:
    st.info(f"No saved chat contains {search!r}.")
elif show_disabled or chat_db.list_sessions(include_disabled=True):
    st.info("All saved chats are disabled: tick **Show disabled sessions** in the sidebar to see or restore them.")
else:
    st.info("No saved chats yet: ask something on the Chats page, and it is saved here.")

# ---- which chat to show: whatever changed last, the imported file or the selected row
if selected_id is not None and state.get("review_seen_row") != selected_id:
    state["review_view"] = "session"
elif uploaded is None and state.get("review_view") == "import":
    state["review_view"] = "session"
state["review_seen_row"] = selected_id

view = state.get("review_view") or ("session" if sessions else None)
if view == "import" and uploaded is not None:
    try:
        histories, judgements = parse_export(uploaded.getvalue())
    except ValueError as e:
        st.error(f"Cannot read {uploaded.name}: {e}")
    else:
        with st.container(horizontal=True, vertical_alignment="center"):
            st.subheader(f"📥 {uploaded.name}")
            if st.button("Save as a chat session", key="review_import_save", icon=":material/save:",
                         help="keep this chat in the database: it then appears in the table and can be opened on the Chats page"):
                session_id, _, _ = chat_db.save_session(None, histories, judgements=judgements)
                state["review_grid_n"] = state.get("review_grid_n", 0) + 1
                st.toast(f"saved as session {session_id}")
                st.rerun()
        show_chat(histories, judgements)
elif view == "session" and sessions:
    session_id = selected_id or sessions[0]["id"]
    info = next(s for s in sessions if s["id"] == session_id)
    st.subheader(f"💬 {info['title']}")
    st.caption(f"session {session_id} · {info['created']} to {info['updated']}" + (" · disabled" if info["disabled"] else ""))
    show_chat(chat_db.load_session(session_id), chat_db.load_judgements(session_id))
