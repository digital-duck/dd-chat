"""The Notes page: keep notes beside the chats, with files attached (documents, images), in the app's SQLite database (see chat_db.py).

At the top the notes in a table, searchable; below it the selected note in a form, or an empty one for a new note. Create saves the form
as a new note (also from a selected note: a copy), Update rewrites the selected note and adds the files picked, Delete hides it (soft:
the note and its files are kept, and can be restored with "Show deleted notes"). Nothing is sent to any model here.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder

import chat_db
from chat_text import FONT_SIZE

TABLE_FONT = FONT_SIZE                      # px, the table's text: the app's text size
TABLE_ROW = 46                              # px, a table row
DESC_HEIGHT = 230                           # px, the Description box
NOTE_HEIGHT = 190                           # px, the Note box: with Attach files below it, the right column ends where Description does
PREVIEW_WIDTH = 240                         # px, an attached image's preview
state = st.session_state


def size_text(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.1f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"


def start_over(current: int | None) -> None:
    """Show `current` in the form (None: a new note), with the table cleared of its selection and the form's widgets fresh from the
    database: a row still selected from before could not be clicked to switch back to it, and the uploader would keep its files."""
    state["notes_current"] = current
    state["notes_seen_row"] = None
    state["notes_grid_n"] = state.get("notes_grid_n", 0) + 1
    state["notes_form_n"] = state.get("notes_form_n", 0) + 1


def clear_search() -> None:
    state["notes_search"] = ""                 # in a callback: a widget's value can only be set before the widget is drawn


def downloader(file_id: int):
    return lambda: chat_db.note_file_data(file_id)      # read only when the button is clicked, not on every rerun


st.header("📝 Notes")

# ---- sidebar: search
st.sidebar.header("Notes")
with st.sidebar.container(horizontal=True, vertical_alignment="bottom"):
    search = st.text_input("Search", key="notes_search", placeholder="a word in a title, description, note or file name")
    st.button("Clear", key="notes_clear_search", icon=":material/close:", on_click=clear_search, disabled=not state.get("notes_search"),
              help="clear the search and show every note")
show_disabled = st.sidebar.checkbox("Show deleted notes", key="notes_show_disabled",
                                    help="deleted notes are hidden from the table, not removed; show them to see or restore one")

# ---- main: the table of notes
notes = chat_db.list_notes(include_disabled=show_disabled, search=search)
selected_id = None
if notes:
    frame = pd.DataFrame(notes)
    frame["status"] = ["deleted" if d else "" for d in frame["disabled"]]
    frame = frame[["id", "title", "description", "files", "updated", "created", "status"]]
    builder = GridOptionsBuilder.from_dataframe(frame)
    builder.configure_selection("single", use_checkbox=False)
    builder.configure_column("id", width=70)
    builder.configure_column("title", flex=2, minWidth=200)
    builder.configure_column("description", flex=3, minWidth=240)
    builder.configure_column("files", width=80)
    builder.configure_column("updated", width=170)
    builder.configure_column("created", width=170, hide=True)
    builder.configure_column("status", width=100, hide=not show_disabled)
    builder.configure_grid_options(domLayout="normal", rowHeight=TABLE_ROW, headerHeight=TABLE_ROW)
    grid = AgGrid(frame, gridOptions=builder.build(), height=TABLE_ROW * 7, update_on=["selectionChanged"],
                  key=f"notes_grid_{state.get('notes_grid_n', 0)}", allow_unsafe_jscode=False,
                  custom_css={".ag-cell": {"font-size": f"{TABLE_FONT}px !important", "line-height": f"{TABLE_ROW - 2}px !important"},
                              ".ag-header-cell-text": {"font-size": f"{TABLE_FONT - 1}px !important", "font-weight": "600"}})
    rows = grid.selected_rows
    rows = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else (rows or [])
    selected_id = int(rows[0]["id"]) if rows else None
elif search:
    st.info(f"No note contains {search!r}.")
else:
    st.info("No notes yet: write one in the form below and press **Create**.")

with st.container(horizontal=True, vertical_alignment="center"):
    if notes:
        st.caption(f"{len(notes)} note(s) in `{chat_db.DB}`" + (f" containing {search!r}" if search else "") + ". Click a row to edit it.")
    if st.button("New note", key="notes_new", icon=":material/add:", help="clear the form to write a new note"):
        start_over(None)
        st.rerun()

# ---- which note the form shows: a newly clicked row, else the note shown before (or the one just created), else a new note
if selected_id is not None and state.get("notes_seen_row") != selected_id:
    state["notes_current"] = selected_id
    state["notes_form_n"] = state.get("notes_form_n", 0) + 1
state["notes_seen_row"] = selected_id
current = chat_db.get_note(state["notes_current"]) if state.get("notes_current") else None
if current is None:
    state["notes_current"] = None

# ---- the detail form
st.subheader(f"Note {current['id']}" + (" · deleted" if current["disabled"] else "") if current else "New note", divider="gray")
if current:
    st.caption(f"created {current['created']} · updated {current['updated']}")
suffix = f"{current['id'] if current else 'new'}_{state.get('notes_form_n', 0)}"       # fresh widgets for each note and after each save
with st.form(f"notes_form_{suffix}", border=True, enter_to_submit=False):
    left, right = st.columns([2, 3], gap="large")
    with left:
        title = st.text_input("Title", value=current["title"] if current else "", key=f"notes_title_{suffix}", max_chars=200)
        description = st.text_area("Description", value=current["description"] if current else "", key=f"notes_desc_{suffix}",
                                   height=DESC_HEIGHT, help="a short summary, shown in the table")
    with right:
        text = st.text_area("Note", value=current["note"] if current else "", key=f"notes_text_{suffix}", height=NOTE_HEIGHT,
                            help="Markdown; see it rendered below the form")
        uploads = st.file_uploader("Attach files", accept_multiple_files=True, key=f"notes_files_{suffix}",
                                   help="documents or images; they are added to the note on Create or Update")
    with st.container(horizontal=True):
        create = st.form_submit_button("Create", icon=":material/add_circle:", type="primary" if not current else "secondary",
                                       help="save the form as a new note" + (" (a copy of this one, with the files picked)" if current else ""))
        update = st.form_submit_button("Update", icon=":material/save:", type="primary" if current else "secondary",
                                       disabled=not current or bool(current["disabled"]), help="save the changes to this note")
        st.space("stretch")                         # Delete sits at the far right, away from Create and Update
        delete = st.form_submit_button("Delete", icon=":material/delete:", disabled=not current or bool(current["disabled"]),
                                       help="hide this note; it is kept, and can be restored with Show deleted notes in the sidebar")

files = [(f.name, f.type, f.getvalue()) for f in uploads or []]
if create or update:
    if not title.strip():
        st.error("A note needs a title.")
    elif create:
        note_id = chat_db.create_note(title, description, text, files)
        start_over(note_id)
        st.toast(f"note {note_id} created")
        st.rerun()
    elif current and chat_db.update_note(current["id"], title, description, text, files):
        start_over(current["id"])
        st.toast(f"note {current['id']} updated")
        st.rerun()
if delete and current:
    chat_db.set_note_disabled(current["id"], True)
    start_over(None)
    st.toast(f"note {current['id']} deleted")
    st.rerun()

if not current:
    st.stop()

if current["disabled"] and st.button("Restore", key="notes_restore", icon=":material/restore:", help="bring this note back into the table"):
    chat_db.set_note_disabled(current["id"], False)
    start_over(current["id"])
    st.rerun()

# ---- the selected note's attachments and rendered text (as saved)
attached = chat_db.note_files(current["id"])
st.markdown(f"**Attachments** ({len(attached)})")
if not attached:
    st.caption("none: pick files in the form and press Update")
images = [f for f in attached if (f["mime"] or "").startswith("image/")]
show_images = bool(images) and st.toggle("Show image previews", key="notes_show_images")
for f in attached:
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f":material/{'image' if f in images else 'description'}: {f['name']} · {size_text(f['size'])} · {f['created']}")
        st.download_button("Download", data=downloader(f["id"]), file_name=f["name"], mime=f["mime"] or "application/octet-stream",
                           key=f"notes_dl_{f['id']}", icon=":material/download:", on_click="ignore")
        with st.popover("Remove", icon=":material/close:"):
            st.write(f"Remove {f['name']} from this note for good?")
            if st.button("Remove it", key=f"notes_rm_{f['id']}", type="primary"):
                chat_db.delete_note_file(f["id"])
                st.rerun()
    if show_images and f in images:
        st.image(chat_db.note_file_data(f["id"]), width=PREVIEW_WIDTH, caption=f["name"], alt=f["name"])

if current["note"].strip():
    with st.container(border=True):
        st.markdown(current["note"])
