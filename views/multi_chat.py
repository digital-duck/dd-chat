"""The Multi-Chat page: one question, up to five chatbots answering side by side.

The sidebar sets how many chatbots there are and, for each, its provider, model, how many recent turns it sees, its language, temperature
and instructions. A question goes to every chatbot at once (in parallel); each keeps its own conversation and shows it in its own column,
so the models can be compared on the same questions. Each answer appears in its column as soon as it arrives. A chatbot's text turn runs
`chat_turn.graph.yaml` once (see chat_engine.py); every question is one model call per chatbot, capped at one per run.

Below the columns, "Compare answers" asks a model to compare the answers to one question (`compare_answers.graph.yaml`). Blind mode
hides the model names until you pick the best answer (🏆) to a question; the picks feed the leaderboard on the Review page.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from datetime import datetime
from typing import Any

import streamlit as st
import yaml

import chat_db
import chat_engine as ce
import chat_models as cm
from chat_export import by_question, export_markdown, export_yaml
from chat_render import money, question_line, reply_body, show_reply
from chat_text import FONT_SIZE as DEFAULT_FONT_SIZE
from link_check import check_links

MAX_BOTS = 5
DEFAULT_BOTS = 3
DEFAULT_TURNS = 5
DEFAULT_TIMEOUT = 300                       # seconds a question waits for the chatbots before the slow ones are given up
TEXT_TYPES = ["txt", "md", "py", "json", "csv", "yaml", "yml", "xml", "html", "css", "js", "ts", "sql", "log", "toml", "ini", "sh"]
IMAGE_TYPES = ["png", "jpg", "jpeg", "gif", "webp"]
FILE_TYPES = TEXT_TYPES + ["pdf"] + IMAGE_TYPES
OLLAMA_CTX_TOKENS = int(os.environ.get("OLLAMA_CONTEXT_LENGTH") or 4096)      # Ollama's context window (its default is 4096): set the same variable here if you raised it for `ollama serve`
MAX_FILE_CHARS = 100_000                    # per attached file: a bigger one is cut, so it fits the model's context
MAX_BATCH = 100                             # questions in one batch
LANGUAGES = {"Same as the question": "", "English": "English", "Chinese (简体)": "Simplified Chinese", "Chinese (繁體)": "Traditional Chinese",
             "Japanese": "Japanese", "Korean": "Korean", "Spanish": "Spanish", "French": "French", "German": "German", "Portuguese": "Portuguese",
             "Russian": "Russian", "Arabic": "Arabic", "Hindi": "Hindi", "Vietnamese": "Vietnamese"}     # shown name -> the language named to the model ("" = leave it to the model)
DEFAULT_MODELS = [("anthropic", "opus"), ("anthropic", "sonnet"),  ("ollama", "lfm3-omni"), ("anthropic", "haiku"), ("ollama", "gemma4")]      # chatbots 1 to 5: provider, model (ollama: name prefix)

SETTING_KEYS = ("bots", "provider_", "model_", "last_n_", "language_", "temp_", "instr_", "pane_height", "font_size", "keep_files",
                "blind", "timeout", "compare_llm")          # widget values saved in the database (by key prefix)
PRESET_KEYS = ("bots", "provider_", "model_", "last_n_", "language_", "temp_", "instr_")     # what a preset (a named line-up) keeps
CHAT_KEYS = ("history_", "error_", "warn_", "retry_", "qnum", "session_id", "session_rev", "judgements", "notice", "_saved_chats")

PANE_HEIGHT = 1000                          # default pixels of conversation shown per chatbot before it scrolls
FONT_SIZE = DEFAULT_FONT_SIZE               # default pixels of the conversation text (the app's text size everywhere)
state = st.session_state


# ---- attachments

def read_attachment(f) -> str:
    """The text of an uploaded file: a PDF's extracted text, any other file decoded as UTF-8 (cut at MAX_FILE_CHARS)."""
    data = f.getvalue()
    if f.name.lower().endswith(".pdf"):
        from pypdf import PdfReader
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    else:
        text = data.decode("utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + f"\n[... cut: the file has {len(text):,} characters, the first {MAX_FILE_CHARS:,} are shown]"
    return text


def is_image(f) -> bool:
    return f.name.rsplit(".", 1)[-1].lower() in IMAGE_TYPES


def with_attachments(question: str, files: list) -> tuple[str, str, list[tuple[str, bytes]], str]:
    """(what the models get, what the page shows, the images, what the history keeps if the files are not kept): file contents go in front of the question; the page lists just the file names.
    An image is sent only with this question (a later turn's history just notes that it was attached)."""
    if not files:
        return question, question, [], question
    images: list[tuple[str, bytes]] = [(f.type or "image/png", f.getvalue()) for f in files if is_image(f)]
    parts = [f"--- attached file: {f.name} ---\n{read_attachment(f)}\n--- end of {f.name} ---" for f in files if not is_image(f)]
    parts += [f"[image attached: {f.name}]" for f in files if is_image(f)]
    notes = "\n".join(f"[file attached to this question only: {f.name}]" for f in files)
    return "\n\n".join(parts) + "\n\n" + question, question + "\n\n" + " ".join(f"📎 `{f.name}`" for f in files), images, notes + "\n\n" + question


def batch_questions(name: str, data: bytes) -> list[str]:
    """The questions in a batch file: a YAML list (or a Multi-Chat export), a CSV's first column, else one question per non-empty line."""
    text = data.decode("utf-8", errors="replace")
    if name.lower().endswith((".yaml", ".yml")):
        doc = yaml.safe_load(text)
        items = doc.get("questions") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            raise ValueError("the YAML file is not a list of questions")
        out = [str(q.get("question", "")) if isinstance(q, dict) else str(q) for q in items]
    elif name.lower().endswith(".csv"):
        import csv
        rows = [r for r in csv.reader(io.StringIO(text)) if r and r[0].strip()]
        out = [r[0] for r in rows[1:]] if rows and rows[0][0].strip().lower() in ("question", "questions", "prompt") else [r[0] for r in rows]
    else:
        out = text.splitlines()
    return [q.strip() for q in out if q.strip()][:MAX_BATCH]


# ---- the browser's own copy and voice buttons

def copy_button(text: str) -> None:
    """📋 puts `text` on the clipboard. Streamlit has no clipboard call, so a small page of its own does it (the textarea route works
    where `navigator.clipboard` is not allowed in an iframe)."""
    st.iframe(
        """<style>html,body{margin:0;padding:0;overflow:hidden;background:transparent}</style><button id="b" title="Copy markdown" style="font-size:16px;padding:2px 10px;border:1px solid #ccc;border-radius:8px;
        background:transparent;cursor:pointer">📋</button><script>
        const text = %s, b = document.getElementById("b");
        b.onclick = () => {
          const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
          let ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
          t.remove(); b.textContent = ok ? "✅" : "⚠️"; setTimeout(() => b.textContent = "📋", 1200);
        };</script>""" % json.dumps(text).replace("</", "<\\/"), height=40, width=56)


LANG_CODES = {"English": "en-US", "Simplified Chinese": "zh-CN", "Traditional Chinese": "zh-TW", "Japanese": "ja-JP", "Korean": "ko-KR", "Spanish": "es-ES",
              "French": "fr-FR", "German": "de-DE", "Portuguese": "pt-PT", "Russian": "ru-RU", "Arabic": "ar-SA", "Hindi": "hi-IN", "Vietnamese": "vi-VN"}


def speakable(text: str) -> str:
    """`text` as plain prose for a voice: links keep their words, URLs, code blocks and Markdown marks are dropped."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s*\|?[-:| ]{3,}\|?\s*$", " ", text, flags=re.M)
    text = re.sub(r"[`*_#>|~]", " ", text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def speak_lang(text: str, language: str | None) -> str:
    """The voice language: the chatbot's language when it has one, else Chinese when the text is mostly Chinese, else the browser's default."""
    code = LANG_CODES.get(LANGUAGES.get(language or "", ""))
    if code:
        return code
    cjk = sum("一" <= c <= "鿿" for c in text)
    return "zh-CN" if text and cjk / len(text) > 0.2 else ""


def speak_button(text: str, language: str | None = None) -> None:
    """🔊 reads the reply aloud with the browser's own voices (Web Speech API); click again to stop. Nothing is sent anywhere."""
    plain = speakable(text)
    st.iframe(
        """<style>html,body{margin:0;padding:0;overflow:hidden;background:transparent}</style><button id="b" title="Read aloud" style="font-size:16px;padding:2px 10px;border:1px solid #ccc;border-radius:8px;
        background:transparent;cursor:pointer">🔊</button><script>
        const text = %s, lang = %s, b = document.getElementById("b"), synth = window.speechSynthesis;
        const reset = () => b.textContent = "🔊";
        b.onclick = () => {
          if (!synth) { b.textContent = "⚠️"; return; }
          if (synth.speaking || synth.pending) { synth.cancel(); reset(); return; }
          const parts = text.match(/[^.!?。！？\\n]+[.!?。！？\\n]*/g) || [text], chunks = [""];
          for (const p of parts) { if ((chunks[chunks.length - 1] + p).length > 180) chunks.push(""); chunks[chunks.length - 1] += p; }
          chunks.filter(c => c.trim()).forEach((c, k, all) => {          // short pieces: some browsers cut a long utterance off
            const u = new SpeechSynthesisUtterance(c);
            if (lang) u.lang = lang;
            if (k === all.length - 1) u.onend = reset;
            u.onerror = reset;
            synth.speak(u);
          });
          b.textContent = "⏹";
        };</script>""" % (json.dumps(plain).replace("</", "<\\/"), json.dumps(speak_lang(plain, language))), height=40, width=56)


# ---- settings, presets and saving

def forget(prefixes: tuple[str, ...]) -> None:
    """Drop the session values whose keys start with one of `prefixes` (a button's callback, so it runs before the widgets)."""
    for key in [k for k in state if str(k).startswith(prefixes)]:
        del state[key]


def reset_settings() -> None:
    """Reset's callback: forget the widget values, and the saved copy, so the defaults come back."""
    forget(SETTING_KEYS)
    chat_db.delete_setting("multi_chat_settings")


def save_preset() -> None:
    name = state.get("preset_name", "").strip()
    if not name:
        return
    presets = chat_db.get_setting("presets", {})
    presets[name] = {k: v for k, v in state.items() if str(k).startswith(PRESET_KEYS)}
    chat_db.set_setting("presets", presets)
    state["preset_pick"] = name
    state["preset_name"] = ""


def load_preset() -> None:
    preset = chat_db.get_setting("presets", {}).get(state.get("preset_pick") or "")
    if preset:
        forget(PRESET_KEYS)
        state.update(preset)


def delete_preset() -> None:
    presets = chat_db.get_setting("presets", {})
    presets.pop(state.get("preset_pick") or "", None)
    chat_db.set_setting("presets", presets)
    state.pop("preset_pick", None)


def visible(n: int) -> dict[int, list[dict]]:
    """The histories of the chatbots shown (a chatbot hidden by lowering their number is not saved or exported)."""
    return {i: state[f"history_{i}"] for i in range(n) if state.get(f"history_{i}")}


def signature(n: int) -> str:
    return json.dumps([visible(n), state.get("judgements", {})], sort_keys=True, default=str)


def load_into_page(session_id: int) -> None:
    """Make a saved session the page's chat (after forgetting the current one)."""
    histories = chat_db.load_session(session_id)
    for i, history in histories.items():
        state[f"history_{i}"] = history
    state["session_id"], state["session_rev"] = session_id, chat_db.session_rev(session_id)
    state["judgements"] = chat_db.load_judgements(session_id)
    state["qnum"] = max((m.get("q", 0) for h in histories.values() for m in h), default=0)
    if histories:                                   # show every chatbot the session has
        state["bots"] = min(MAX_BOTS, max(int(state.get("bots") or DEFAULT_BOTS), max(histories) + 1))
    state["_saved_chats"] = signature(int(state.get("bots") or DEFAULT_BOTS))


def restore() -> dict:
    """Before the widgets: bring back the settings saved in the database, then the chat: the session the Review page asked to open, or
    (once per browser session) the latest one. Returns the saved settings, to tell later whether they changed."""
    saved = chat_db.get_setting("multi_chat_settings", {})
    for key, value in saved.items():                 # Streamlit drops a widget's value when its page is left: put it back
        state.setdefault(key, value)
    opening = state.pop("open_session_id", None)
    if opening is not None:
        forget(CHAT_KEYS)
        load_into_page(int(opening))
    elif not state.get("_restored"):
        session_id = chat_db.latest_session_id()
        if session_id and "session_id" not in state:
            load_into_page(session_id)
    state["_restored"] = True
    return saved


def persist(saved: dict, n: int) -> None:
    """After the widgets: save the settings and the chat when they changed. If another tab wrote to this session meanwhile, or it was
    disabled on the Review page, the chat goes on as a new session instead of overwriting it."""
    settings = {k: v for k, v in state.items() if str(k).startswith(SETTING_KEYS)}
    if settings != saved:
        chat_db.set_setting("multi_chat_settings", settings)
        saved.clear()
        saved.update(settings)
    sig = signature(n)
    if sig != state.get("_saved_chats"):
        session_id, rev, forked = chat_db.save_session(state.get("session_id"), visible(n), state.get("session_rev", 0), state.get("judgements", {}))
        if session_id:
            state["session_id"], state["session_rev"] = session_id, rev
        if forked:
            state["notice"] = (f"This chat was changed in another tab or disabled on the Review page, so it now continues as a new session "
                               f"({session_id}); the other copy is kept as it was.")
        state["_saved_chats"] = sig


# ---- the sidebar

def bot_settings(i: int, config: dict) -> dict:
    """The sidebar block of chatbot i: provider, model, turns it sees, language, temperature, instructions."""
    history = state.setdefault(f"history_{i}", [])
    with st.sidebar.expander(f"**Chatbot {i + 1}**", expanded=False):
        want_provider, want_model = DEFAULT_MODELS[i] if i < len(DEFAULT_MODELS) else DEFAULT_MODELS[0]
        if state.get(f"provider_{i}") not in cm.PROVIDERS:
            state.pop(f"provider_{i}", None)
        provider = st.selectbox("Provider", list(cm.PROVIDERS), index=list(cm.PROVIDERS).index(want_provider), key=f"provider_{i}")
        st.caption(cm.INFO[provider].note + (" · billed" if cm.INFO[provider].billed else ""))
        if cm.missing_key(provider):
            st.warning(f"`{cm.missing_key(provider)}` is not set: this provider's calls will fail.")
        options = cm.selected(provider, config)
        if not options:
            st.warning(f"No {provider} models to offer: " + ("start Ollama (`ollama serve`) and pull a model, or " if provider == "ollama" else "")
                       + "choose some on the Settings page.")
        key = f"model_{provider}_{i}"
        if state.get(key) not in options:         # a model that is no longer offered
            state.pop(key, None)
        first = next((k for k, m in enumerate(options) if want_model in m), 0)
        model = st.selectbox("Model", options, index=first, key=key) if options else None
        name = cm.short_name(provider, model) if model else None
        last_n = int(st.number_input("Turns it sees", 1, 50, DEFAULT_TURNS, key=f"last_n_{i}",
                                     help="the most recent questions and replies sent with each question"))
        language = st.selectbox("Language", list(LANGUAGES), key=f"language_{i}",
                                help="the language this chatbot replies in, whatever the question's language; e.g. the same model in English in one chatbot and in Chinese in another")
        temperature = st.number_input("Temperature", 0.0, 2.0, value=None, step=0.1, key=f"temp_{i}", placeholder="the provider's default",
                                      help="higher is more varied, lower more predictable; empty: the provider's default. The Claude CLI ignores it")
        instructions = st.text_area("Instructions", key=f"instr_{i}", height=80, placeholder="e.g. Answer as a patient teacher, in under 150 words",
                                    help="this chatbot's own system prompt: compare prompts as well as models (the same model with two instructions)").strip()
        st.caption(f"{len(history) // 2} turn(s) so far")
    return {"llm": f"{cm.PROVIDERS[provider]}:{model}" if model else "", "provider": provider, "model": model,
            "label": f"{provider} · {name or 'no model'}" + (f" · {language}" if LANGUAGES[language] else "") + (" · 📝" if instructions else ""),
            "last_n": last_n, "language": LANGUAGES[language], "language_name": language,
            "temperature": float(temperature) if temperature is not None else None, "instructions": instructions}


def totals(n: int) -> str:
    """This chat's answers, tokens and cost (answers copied from a twin chatbot are not counted again)."""
    answers = [m for h in visible(n).values() for m in h if m["role"] == "assistant" and not m.get("shared_from")]
    if not answers:
        return ""
    tokens = sum((m.get("input_tokens") or 0) + (m.get("output_tokens") or 0) for m in answers)
    known = [m["cost_usd"] for m in answers if m.get("cost_usd") is not None]
    unknown = len(answers) - len(known)
    return (f"This chat: {len(answers)} answer(s) · {tokens:,} tokens" + (f" · {money(sum(known))}" if known else "")
            + (f" (+ {unknown} with no known cost)" if unknown and known else " · cost not known" if unknown else ""))


# ---- asking

def set_feedback(i: int, idx: int, value: int) -> None:
    """A thumb button's callback: the same thumb again takes the feedback back (0)."""
    m = state[f"history_{i}"][idx]
    m["feedback"] = 0 if m.get("feedback", 0) == value else value


def set_best(i: int, idx: int, q: int, n: int) -> None:
    """🏆's callback: this answer is the best of question q (again: no best); the other chatbots' answers to q lose the mark."""
    chosen = state[f"history_{i}"][idx]
    on = not chosen.get("best")
    for h in visible(n).values():
        for pos, m in enumerate(h):
            if m["role"] == "assistant" and pos and h[pos - 1].get("q") == q:
                m.pop("best", None)
    if on:
        chosen["best"] = 1


def request(kind: str, i: int) -> None:
    """Retry and redo buttons: run after the page is drawn, so the answer appears in its column."""
    state["pending"] = {"kind": kind, "bot": i}


def work(question: str, history: list[dict], turn: ce.Turn, images: list) -> tuple[ce.Answer, dict]:
    """One chatbot's turn and the check of the links in its reply, in the chatbot's own thread."""
    answer = ce.ask_with_images(question, history, turn, images) if images else ce.ask(question, history, turn)
    return answer, check_links(answer.reply)


def run_round(job: dict, bots: list[dict], panes: dict, targets: list[int], mode: str = "new") -> None:
    """Ask `targets` the question in `job` ({question, shown, images, lean, q}) and add the answers to their histories.

    mode "new": a new question (or a retry of one that failed); "redo": the chatbot's latest answer is asked again and replaced.
    Chatbots that would send the same thing to the same model are asked once and the reply is copied to the others (marked as shared),
    so every column stays in step. Each answer is drawn in its column as soon as it arrives; chatbots still silent after the timeout
    are given up (their thread finishes in the background, and its reply is dropped)."""
    timeout = int(state.get("timeout") or DEFAULT_TIMEOUT)
    for i in targets:
        for prefix in ("error_", "warn_", "retry_"):
            state.pop(f"{prefix}{i}", None)
    bases = {i: state[f"history_{i}"][:-2] if mode == "redo" else state[f"history_{i}"] for i in targets}
    turns = {i: ce.Turn(bots[i]["llm"], bots[i]["last_n"], bots[i]["language"], bots[i]["instructions"], bots[i]["temperature"],
                        cm.prices(bots[i]["provider"], bots[i]["model"] or "")) for i in targets}
    seen: dict[tuple, int] = {}
    twin: dict[int, int] = {}
    for i in targets:
        t = turns[i]
        sent = (t.llm, t.language, t.last_n, t.instructions, t.temperature, json.dumps([m["content"] for m in bases[i][-2 * t.last_n:]]))
        if sent in seen:
            twin[i] = seen[sent]
        else:
            seen[sent] = i
    asked = [i for i in targets if i not in twin]

    slots = {}
    for i in targets:
        with panes[i]:
            if mode != "redo":
                with st.chat_message("user"):
                    st.markdown(question_line({"content": job["question"], "shown": job["shown"], "q": job["q"]}, job["q"]))
            with st.chat_message("assistant"):
                slots[i] = st.empty()
                slots[i].caption(f"⏳ the same question as chatbot {twin[i] + 1}: its reply is copied here" if i in twin else "⏳ thinking…")

    results: dict[int, Any] = {}
    pool = ThreadPoolExecutor(max_workers=len(asked))
    futures = {pool.submit(work, job["question"], bases[i], turns[i], job["images"]): i for i in asked}
    try:
        for future in as_completed(futures, timeout=timeout):
            i = futures[future]
            try:
                results[i] = future.result()
                slots[i].markdown(reply_body({"content": results[i][0].reply, "links": results[i][1]}))
            except Exception as e:              # one failing chatbot does not lose the others' replies, and its conversation stays as it was
                results[i] = e
                slots[i].error(f"{type(e).__name__}: {e}")
            for k, source in twin.items():
                if source == i:
                    slots[k].caption("copied from chatbot " + str(i + 1))
    except FuturesTimeout:
        for future, i in futures.items():
            if i not in results:
                results[i] = TimeoutError(f"no reply after {timeout} s (the timeout is in the sidebar settings)")
                slots[i].error(str(results[i]))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    keep_files = state.get("keep_files", True)
    for i in targets:
        source = twin.get(i, i)
        result = results.get(source)
        if not isinstance(result, tuple):
            why = f"chatbot {source + 1}, whose reply this one shares, failed" if i in twin else f"{type(result).__name__}: {result}"
            state[f"error_{i}"] = f"{job['shown'][:80]!r} failed: {why}"
            state[f"retry_{i}"] = {"job": job, "mode": mode}
            continue
        answer, links = result
        t = turns[i]
        adapter, _, model = t.llm.partition(":")
        reply = {"role": "assistant", "content": answer.reply, "model": t.llm, "seconds": answer.seconds, "feedback": 0,
                 "language": bots[i]["language_name"] if t.language else None, "links": links or None,
                 "est_tokens": ce.estimate_tokens(job["question"], bases[i], t),
                 "ctx_tokens": (cm.ollama_context(model) or OLLAMA_CTX_TOKENS) if adapter == "ollama" else None,     # what Ollama loaded the model with, else the variable
                 "temperature": t.temperature, "instructions": t.instructions or None}
        if i in twin:
            reply.update(shared_from=source + 1, seconds=answer.seconds)
        else:
            reply.update(input_tokens=answer.input_tokens, output_tokens=answer.output_tokens, cost_usd=answer.cost_usd)
        reply = {k: v for k, v in reply.items() if v is not None}
        history = state[f"history_{i}"]
        if mode == "redo":
            history[-1] = reply
        else:
            history += [{"role": "user", "content": job["question"] if keep_files else job["lean"], "shown": job["shown"], "q": job["q"]}, reply]


def ask_new(text: str, files: list, bots: list[dict], panes: dict, n: int) -> bool:
    """A question typed (or from a batch): every chatbot with a model is asked. False when nothing was asked."""
    for i in range(n):                              # the previous question's notes go, also for a chatbot that is not asked now
        for prefix in ("error_", "warn_", "retry_"):
            state.pop(f"{prefix}{i}", None)
    try:
        question, shown, images, lean = with_attachments(text, files)
    except Exception as e:
        st.error(f"Could not read the attachment: {type(e).__name__}: {e}")
        return False
    if not text and not files:
        return False
    ready = [i for i in range(n) if bots[i]["llm"]]
    if not ready:
        st.error("No chatbot has a model: pick one in the sidebar.")
        return False
    state["qnum"] = state.get("qnum", 0) + 1         # numbered only once it is really asked, so the numbers have no gaps
    run_round({"question": question, "shown": shown, "images": images, "lean": lean, "q": state["qnum"]}, bots, panes, ready)
    return True


def handle_pending(pending: dict, bots: list[dict], panes: dict, n: int) -> None:
    i = pending["bot"]
    if i >= n or not bots[i]["llm"]:
        return
    if pending["kind"] == "retry" and state.get(f"retry_{i}"):
        failed = state[f"retry_{i}"]
        run_round(failed["job"], bots, panes, [i], failed["mode"])
    elif pending["kind"] == "redo":
        history = state[f"history_{i}"]
        if len(history) >= 2 and history[-2]["role"] == "user":
            user = history[-2]
            job = {"question": user["content"], "shown": user.get("shown") or user["content"], "images": [], "lean": user["content"],
                   "q": user.get("q", len(history) // 2)}
            run_round(job, bots, panes, [i], "redo")


def run_batch(questions: list[str], bots: list[dict], panes: dict, n: int, saved: dict) -> None:
    progress = st.progress(0.0, text=f"batch: 0 of {len(questions)}")
    for k, question in enumerate(questions, 1):
        ask_new(question, [], bots, panes, n)
        persist(saved, n)                           # each answered question is saved, so stopping the batch keeps what was done
        progress.progress(k / len(questions), text=f"batch: {k} of {len(questions)}")


# ---- the page

def show(i: int, history: list[dict], n: int, blind: bool, picked: set[int]) -> None:
    asked = 0
    q = 0
    for idx, m in enumerate(history):
        with st.chat_message(m["role"]):
            if m["role"] == "user":
                asked += 1
                q = m.get("q", asked)
                st.markdown(question_line(m, asked))             # the question number lines the columns up
                continue
            show_reply(m, hide_model=blind and q not in picked)
            with st.container(horizontal=True, gap="small", vertical_alignment="center"):
                speak_button(m["content"], m.get("language"))
                copy_button(m["content"])
                feedback = m.get("feedback", 0)
                st.button("👍", key=f"up_{i}_{idx}", on_click=set_feedback, args=(i, idx, 1), type="primary" if feedback == 1 else "secondary",
                          help="good answer (click again to undo)")
                st.button("👎", key=f"down_{i}_{idx}", on_click=set_feedback, args=(i, idx, -1), type="primary" if feedback == -1 else "secondary",
                          help="poor answer (click again to undo)")
                if n > 1:
                    st.button("🏆", key=f"best_{i}_{idx}", on_click=set_best, args=(i, idx, q, n), type="primary" if m.get("best") else "secondary",
                              help="the best answer to this question (click again to undo); in blind mode this also shows the models")
                if idx == len(history) - 1:
                    st.button("↻", key=f"redo_{i}", on_click=request, args=("redo", i),
                              help="ask this chatbot this question again and replace its answer (attached images are not sent again)")


def compare_section(bots: list[dict], n: int) -> None:
    """Below the columns: a model compares the answers to one question."""
    questions = {q: e for q, e in by_question(visible(n)).items() if len(e["answers"]) > 1}
    judgements = state.setdefault("judgements", {})
    if not questions:
        return
    with st.expander("Compare answers", icon=":material/compare_arrows:", expanded=bool(judgements)):
        llms = list(dict.fromkeys(b["llm"] for b in bots[:n] if b["llm"]))
        if state.get("compare_llm") not in llms:
            state.pop("compare_llm", None)
        with st.container(horizontal=True, vertical_alignment="bottom"):
            q = st.selectbox("Question", list(questions)[::-1], format_func=lambda k: f"[{k}] {' '.join(questions[k]['question'].split())[:70]}",
                             key="compare_q")
            judge = st.selectbox("Compared by", llms, key="compare_llm", help="the model that compares (one more model call)")
            go = st.button("Compare", type="primary", disabled=not judge)
        if go and judge:
            answers = [(f"Chatbot {i + 1}", m["content"]) for i, m in questions[q]["answers"]]
            provider, model = cm.provider_of(judge)
            with st.spinner(f"{judge} is comparing {len(answers)} answers"):
                try:
                    result = ce.compare(questions[q]["question"], answers, ce.Turn(judge, prices=cm.prices(provider, model)))
                    judgements[q] = {"judge": judge, "content": result.reply, "seconds": round(result.seconds, 1)}
                except Exception as e:
                    st.error(f"The comparison failed: {type(e).__name__}: {e}")
        for k in sorted(judgements, reverse=True):
            if k in questions:
                j = judgements[k]
                st.markdown(f"**[{k}]** {' '.join(questions[k]['question'].split())[:100]}")
                st.markdown(j["content"])
                st.caption(f"compared by {j.get('judge')} · {j.get('seconds') or 0:.1f}s")
                st.divider()


def main() -> None:
    saved = restore()
    st.sidebar.header("Chats")
    top = st.sidebar.container()                    # New / Export / Reset, filled once the number of chatbots is known

    with st.sidebar.expander("Settings", expanded=False):
        n = int(st.selectbox("Number of chatbots", list(range(1, MAX_BOTS + 1)), index=DEFAULT_BOTS - 1, key="bots",
                             help="each one answers every question, side by side; billed providers are one call each"))
        pane_height = int(st.number_input("Pane height (px)", 200, 2000, PANE_HEIGHT, step=50, key="pane_height",
                                          help="how tall each chatbot's conversation is before it scrolls"))
        font_size = int(st.number_input("Font size (px)", 10, 28, FONT_SIZE, step=1, key="font_size", help="the size of the conversation text"))
        st.markdown(f"<style>[data-testid='stChatMessage'] :is(p, li, code, td, th), [data-testid='stChatInput'] textarea "
                    f"{{ font-size: {font_size}px; }}</style>", unsafe_allow_html=True)
        st.checkbox("Keep attached files in the chat", value=True, key="keep_files",
                    help="on: later questions still see the attached files (a long file is sent again every turn, and may overflow a small context window); "
                         "off: a file is sent with the question it is attached to, and later turns only know its name")
        blind = st.toggle("Blind mode", key="blind",
                          help="hide the model names until you pick the best answer (🏆) to a question; keep the chatbot sections below closed")
        st.number_input("Reply timeout (s)", 30, 3600, DEFAULT_TIMEOUT, step=30, key="timeout",
                        help="how long a question waits for the chatbots; a slower one is given up and can be retried")

    with st.sidebar.expander("Presets", expanded=False):
        presets = chat_db.get_setting("presets", {})
        if state.get("preset_pick") not in presets:
            state.pop("preset_pick", None)
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.selectbox("Line-up", list(presets), key="preset_pick", placeholder="no presets yet", index=None,
                         help="a saved set of chatbots: providers, models, turns, languages, temperatures, instructions")
            st.button("Load", key="preset_load", on_click=load_preset, disabled=not state.get("preset_pick"))
            st.button("Delete", key="preset_delete", on_click=delete_preset, disabled=not state.get("preset_pick"))
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.text_input("Save the current line-up as", key="preset_name", placeholder="e.g. local only")
            st.button("Save", key="preset_save", on_click=save_preset)

    config = cm.saved_choice()
    bots = [bot_settings(i, config) for i in range(n)]

    with st.sidebar.expander("Batch", expanded=False):
        batch_file = st.file_uploader("Questions", type=["txt", "md", "csv", "yaml", "yml"], key="batch_file",
                                      help="one question per line (.txt / .md), a CSV's first column, or a YAML list (an export works too)")
        batch: list[str] = []
        if batch_file is not None:
            try:
                batch = batch_questions(batch_file.name, batch_file.getvalue())
            except (ValueError, yaml.YAMLError) as e:
                st.error(f"Cannot read {batch_file.name}: {e}")
            st.caption(f"{len(batch)} question(s); each goes to every chatbot, one question after another. Any click stops the batch; "
                       "the questions answered so far are kept.")
        if st.button("Run the batch", key="batch_run", disabled=not batch, type="primary"):
            state["pending"] = {"kind": "batch", "questions": batch}

    with top:
        histories_now = visible(n)
        with st.container(horizontal=True):
            st.button("New", key="clear", on_click=forget, args=(CHAT_KEYS,), width="stretch",
                      help="start a new chat: clear every chatbot's conversation (the settings are kept, and the old chat stays saved on the Review page)")
            with st.popover("Export", width="stretch", disabled=not histories_now, help="every question with each chatbot's answer"):
                stamp = f"{datetime.now():%y%m%d-%H%M%S}"
                snapshot, judged = copy.deepcopy(histories_now), copy.deepcopy(state.get("judgements", {}))
                meta = {"id": state.get("session_id")} if state.get("session_id") else None
                st.download_button("YAML (can be imported)", lambda: export_yaml(snapshot, judged, meta), file_name=f"GraphLang-{stamp}.chat.yaml",
                                   mime="application/x-yaml", key="export_yaml", on_click="ignore", width="stretch", icon=":material/data_object:")
                st.download_button("Markdown (to read)", lambda: export_markdown(snapshot, judged), file_name=f"GraphLang-{stamp}.chat.md",
                                   mime="text/markdown", key="export_md", on_click="ignore", width="stretch", icon=":material/description:")
            defaults = ", ".join(m for _, m in DEFAULT_MODELS[:DEFAULT_BOTS])
            st.button("Reset", key="reset", on_click=reset_settings, width="stretch",
                      help=f"back to the default settings: {DEFAULT_BOTS} chatbots ({defaults}), {DEFAULT_TURNS} turns (the chats are kept)")
        if totals(n):
            st.caption(totals(n))

    persist(saved, n)
    st.header("💬 Chats")
    if state.get("notice"):
        st.info(state.pop("notice"), icon=":material/call_split:")
    picked = {m_q for h in visible(n).values() for pos, m in enumerate(h)
              if m["role"] == "assistant" and m.get("best") and pos for m_q in [h[pos - 1].get("q")]}
    panes = {}
    for i, column in enumerate(st.columns(n)):
        bot = bots[i]
        with column:
            st.subheader(f"Chatbot {i + 1}" if blind else f"{i + 1} · {bot['label']}", divider="gray")
            panes[i] = st.container(height=pane_height, border=False)        # each chatbot scrolls on its own, so the columns stay side by side
            with panes[i]:
                show(i, state[f"history_{i}"], n, blind, picked)
                if not state[f"history_{i}"]:
                    st.caption("Ask a question below to start.")
                if state.get(f"warn_{i}"):
                    st.warning(state[f"warn_{i}"])
                if state.get(f"error_{i}"):
                    st.error(state[f"error_{i}"])
                    if state.get(f"retry_{i}"):
                        st.button("Retry", key=f"retry_btn_{i}", on_click=request, args=("retry", i), icon=":material/refresh:")
    if n > 1:
        compare_section(bots, n)

    entry = st.chat_input("Ask a question" + (" (every chatbot answers)" if n > 1 else ""), accept_file="multiple", file_type=FILE_TYPES)
    pending = state.pop("pending", None)
    if entry:
        if not ask_new(entry.text, list(entry.files), bots, panes, n):
            return
    elif pending and pending["kind"] == "batch":
        run_batch(pending["questions"], bots, panes, n, saved)
    elif pending:
        handle_pending(pending, bots, panes, n)
    else:
        return
    persist(saved, n)
    st.rerun()


main()
