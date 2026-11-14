"""Settings: choose which models the Chats page offers, for each provider."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import chat_db
import chat_models as cm

st.title("⚙️ Settings")
st.caption(f"Tick the models each provider may use; only those are options in the Model dropdown of the Chats page. Saved in the SQLite database `{chat_db.DB}`.")


def panel(provider: str) -> None:
    adapter = cm.PROVIDERS[provider]
    top, _ = st.columns([1, 3])
    st.caption(cm.INFO[provider].note + (" · billed" if cm.INFO[provider].billed else "")
               + (" · reads images" if cm.INFO[provider].images else ""))
    if top.button("Refresh the list", key=f"refresh_{provider}", disabled=not cm.can_refresh(provider),
                  help="ask the provider again for its models"):
        cm.openrouter_models.clear()
        cm.ollama_models.clear()
        cm.openai_models.clear()
    if cm.missing_key(provider):
        st.warning(f"`{cm.missing_key(provider)}` is not set: " + ("the list loads, but " if provider == "openrouter" else "")
                   + "a chatbot cannot call these models. Set it in the shell that starts Streamlit.")
    if not cm.can_refresh(provider):
        st.caption("This provider's models cannot be listed here: these are the known ones; add others by hand below.")
    try:
        rows = cm.available(provider)
    except OSError as e:
        st.error(f"could not get the model list for {adapter}: {e}")
        rows = []
    if provider == "ollama" and not rows:
        st.warning(f"No Ollama models found at {cm.OLLAMA_URL}: start Ollama (`ollama serve`) and pull a model.")
    chosen = cm.selected(provider)
    known = {r["id"] for r in rows}
    rows = rows + [{"id": m, "name": "(added by hand, or no longer listed)"} for m in chosen if m not in known]
    frame = pd.DataFrame(rows, columns=["id", "name", "context", "prompt $/M", "completion $/M"] if provider == "openrouter" else ["id", "name"])
    frame.insert(0, "use", frame["id"].isin(chosen))

    needle = st.text_input("Filter", key=f"filter_{provider}", placeholder="part of a model id or name").strip().lower()
    if needle:
        frame = frame[frame["id"].str.lower().str.contains(needle, regex=False) | frame["name"].fillna("").str.lower().str.contains(needle, regex=False)]
    st.caption(f"{len(chosen)} selected · {len(frame)} shown. Ticks count only after Save; saving keeps the selected models the filter hides.")
    edited = st.data_editor(frame, hide_index=True, width="stretch", height=min(600, 38 + 35 * max(len(frame), 1)), key=f"editor_{provider}_{needle}",
                            disabled=[c for c in frame.columns if c != "use"], column_config={"use": st.column_config.CheckboxColumn("use")})
    if st.button("Save the selection", key=f"save_{provider}", type="primary"):
        shown = set(edited["id"])
        cm.save_selected(provider, [m for m in chosen if m not in shown] + list(edited.loc[edited["use"], "id"]))
        st.rerun()

    add, button = st.columns([3, 1], vertical_alignment="bottom")
    new = add.text_input("Add a model id by hand", key=f"add_{provider}", placeholder="e.g. claude-sonnet-5-5" if provider.startswith("anthropic") else "model id").strip()
    if button.button("Add", key=f"add_go_{provider}", disabled=not new):
        cm.save_selected(provider, chosen + [new])
        st.rerun()


for tab, provider in zip(st.tabs([f"{p} ({a})" for p, a in cm.PROVIDERS.items()]), cm.PROVIDERS):
    with tab:
        panel(provider)
