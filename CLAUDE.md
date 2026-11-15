# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Streamlit app that sends one question to 1–5 LLM "chatbots" side by side. It is a showcase of GraphLang (`graflang>=0.2.1`; the app needs `graphlang.grafr.metering`, which 0.1.1 lacks). The chat logic lives in graph files (`chat_turn.graph.yaml`, `compare_answers.graph.yaml`); the app keeps the conversations and runs the graphs.

## Commands

```bash
pip install -r requirements-dev.txt      # Python 3.11+; requirements.txt for the app alone
streamlit run src/app.py                 # the app; run from the repo root so .streamlit/config.toml applies

pytest tests                             # all tests; no model is called (mock adapter, fake ask, temp database)
pytest tests/test_chat_db.py::test_second_tab_does_not_overwrite_first    # one test

graf validate src/chat_turn.graph.yaml   # check a graph after editing it
graf run src/chat_turn.graph.yaml --llm claude_cli:claude-sonnet-5-5 \
    --param question="What is a graph?" --param history='[]'
```

Billed providers cost money: every question is one call per chatbot. To try changes for free, use a local Ollama model or the graflang `mock` adapter (`chat_engine.ask(..., Turn("mock:x"))`).

## Packaging and release

Published on PyPI as `dd-chat` (`pip install dd-chat`, then `dd-chat`). `pyproject.toml` maps the package `dd_chat` onto the `src/` folder (flat modules keep importing each other by bare name, so `src/__init__.py` is empty); `src/cli.py` is the `dd-chat` command: it runs `streamlit run` on the installed `app.py` and passes the theme and warning options that `.streamlit/config.toml` gives a checkout (that file is not installed, so keep the two in step). The graph YAML files and `views/*.py` ship as package data. Installed, the data folder is `~/.dd-chat` (`DD_CHAT_HOME`), including the kernel sockets; from a checkout the sockets go in the repo's `tmp/`. Release: bump `version` in `pyproject.toml`, `rm -rf dist && python -m build && twine upload dist/*` (token in `~/.pypirc`); a PyPI version can never be reused.

## Architecture

All code and both graphs live in `src/` (Streamlit puts `src/` on `sys.path`, so modules import each other flat, e.g. `import chat_db`); the root holds docs, requirements, `tests/` and `.streamlit/`. Paths below are relative to `src/`.

- `app.py` registers five pages with `st.navigation`: Chats (`views/multi_chat.py`), Review (`views/review.py`), Notes (`views/notes.py`), Settings (`views/config_llm.py`), About (`views/about.py`). Logic shared by pages lives in the modules beside `app.py`; the page files hold UI. `st.Page` / `st.switch_page` paths are relative to `app.py`.
- **`chat_engine.py` asks the models and has no Streamlit code** (it runs in worker threads and tests). A text turn is one run of `chat_turn.graph.yaml` through `MeteredLLM(..., max_calls=1)`; the returned `Answer` carries reply, seconds, model ms, tokens and cost. Per-chatbot temperature is injected by the `_Tuned` wrapper (GraphLang reads temperature only from node config); instructions and formatting `rules` are graph inputs. The `rules` text lives only in the graph's input default and is read back for the image path. Image turns bypass the graph and call `dd_llm` directly, in OpenAI or Anthropic message format (`IMAGE_FORMATS`).
- **Kernel reuse**: SOLVE nodes run in a Jupyter kernel that GraphLang starts per run. `chat_engine._kernels()` lends long-lived `_KeptPool`s and swaps them into `runtime.kernels` before `execute()` (an undocumented GraphLang attribute; falls back if missing). It cuts about 0.7 s to 0.03 s per run. The kernels' Unix sockets go in the repo's `tmp/` (`chat_engine` sets `GRAFLANG_KERNEL_DIR`, which GraphLang's `kernel.ipc_prefix()` reads; an older GraphLang ignores it and puts `kernel-ipc-*` in the launch folder).
- **Chats page round** (`run_round` in `views/multi_chat.py`): chatbots that would send identical input are asked once and the reply is copied (`shared_from`), so histories stay aligned; answers are drawn into each column's pane as futures complete; stragglers past the timeout are given up; failures store a `retry_{i}` job. Buttons (retry, redo ↻) set `state["pending"]`, handled after the panes are drawn so output lands in the right column.
- **API keys** (`env_keys.py`, no Streamlit code): precedence key typed on the Settings page (memory only) > process environment > `.env` (`./.env`, then `~/.dd-chat/.env`; python-dotenv's convention). Every source ends up in `os.environ`, where the adapters read it; an empty `.env` value counts as unset. `example.env` is the template.
- **LLM ids** are `<graflang adapter>:<model>`. `chat_models.INFO` describes each shown provider (adapter, API-key env var, image format, known models); `PROVIDERS` maps provider → adapter. Anthropic ids are hard-coded in `chat_models.ANTHROPIC` (the CLI cannot list models).
- **Persistence** (`chat_db.py`): SQLite at `$DD_CHAT_HOME/multi_chat.db` (default `~/.dd-chat`). Message columns are declared once in `COLUMNS` (schema and additive `ALTER TABLE` migrations are generated from it); add a message field there. `connect()` is a context manager that commits and closes; the schema check runs once per process. `save_session` uses an optimistic `rev`: if another tab wrote, or the session was disabled, it forks a new session instead of overwriting. Settings are JSON values per key (`multi_chat_settings`, `chat_models`, `presets`). Notes (`notes`, soft delete by `disabled`) keep their attachments as BLOBs in `note_files`; listings never read the bytes (`note_file_data` does, on download). `schema/multi_chat.sql` is a readable copy of the DDL (not run by the app); a schema change goes in both, and `test_schema_file_matches_code` compares them.
- **Chats-page state**: per chatbot `i`, `st.session_state["history_{i}"]` is a list of message dicts (user: `content`, `shown`, `q`; assistant: `model`, `seconds`, tokens, `cost_usd`, `feedback`, `best`, `links`, `shared_from`, ...). `restore()` runs before the widgets, `persist(saved, n)` after them. Persisted widget keys are listed by prefix in `SETTING_KEYS` (a new persisted widget needs its prefix there; mind prefix collisions such as `judge` vs `judgements`), preset keys in `PRESET_KEYS`, per-chat keys cleared by New in `CHAT_KEYS`. Only the visible chatbots (`range(n)`) are saved and exported.
- `chat_export.py`: YAML export via PyYAML with literal blocks (round-trips with `parse_export`, which also reads the older format) and Markdown export. `chat_render.py`: answer captions and display shared by the Chats and Review pages. `link_check.py`: URL extraction (keeps balanced brackets), dead-link flags, and a check that pins connections to verified public IPs and re-checks every redirect.

## Conventions

- `chat_text.FONT_SIZE` must equal `baseFontSize` in `.streamlit/config.toml`.
- `.streamlit/config.toml` disables the widget-state-duplication warning on purpose (saved values are restored into session state while widgets also have defaults).
- Tests isolate the database through the autouse `temp_db` fixture in `tests/conftest.py`; `conftest.py` puts `src/` on `sys.path`; page tests use `streamlit.testing.v1.AppTest` from the `../src/app.py` entrypoint.
- The code style is dense, with docstrings and inline comments written as plain-English explanations of *why*; follow it.
