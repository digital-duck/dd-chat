# Multi-Chat codebase review

Reviewer: Claude Opus 5.5 · Date: 2026-11-14 · Scope: the working tree on `main` (including uncommitted files `chat_db.py`, `chat_text.py`, `link_check.py`, `views/review.py`), about 1,270 lines of Python plus `chat_turn.graph.yaml`.

Items marked **verified** were reproduced by running the code; the rest come from reading it.

---

## 1. Strengths

1. **The graph fits the showcase.** `chat_turn.graph.yaml` keeps the chat logic (append, trim, generate, append) apart from the UI, and the same file runs from `graf run`. The app is a fair demonstration of GraphLang rather than a Streamlit app with a graph added on.
2. **Cost is controlled.** `MeteredLLM(..., max_calls=1)` caps each run at one billed call. Chatbots that would send identical input to the same model are skipped (`views/multi_chat.py:378-391`), and the UI says plainly which providers cost money.
3. **One chatbot's failure stays contained.** Each chatbot runs in its own thread, its exception is caught on its own, and the failed chatbot's history is left unchanged (`views/multi_chat.py:399-404`). The other replies are kept.
4. **Persistence is small and sensible.** `chat_db.py` is one short module. It uses additive in-place migrations, soft delete (`disabled`), JSON settings in a key-value table, and a one-time import of the old `chat_models.json`. Streamlit's habit of dropping widget values when you leave a page is handled on purpose by `restore()` before the widgets and `persist()` after them, and `persist()` writes only when something changed.
5. **Model output is handled with care.** Dead links get flagged, the link checker avoids private-network hosts, `tidy_markdown` changes only the display (the stored reply is untouched), the prompt tells models not to invent URLs, and Ollama context overflow is detected and explained to the user.
6. **The HTML snippets are injection-safe.** The copy and speak buttons embed text with `json.dumps(...).replace("</", "<\\/")`, model Markdown is rendered without `unsafe_allow_html`, and the only raw-HTML `<style>` blocks interpolate integers.
7. **The comments explain why.** Docstrings and inline comments give the reasons behind decisions (for example "a site that blocks bots is shown as not checked rather than as broken"), which makes the code easy to pick up.
8. **The export format is easy to read and robust.** YAML literal blocks with an explicit indent indicator (`|2-`) need no escaping, and control characters are stripped. The Review page reads the file back.

---

## 2. Weaknesses

1. **A Jupyter kernel starts for every chatbot, every question.** `ask()` builds a new `GraphLangRuntime` each call (`views/multi_chat.py:110`). Each runtime creates its own `KernelPool`, which starts an `ipykernel` process via `KernelManager.start_kernel()` (graflang `grafr/kernel.py:74`) to run three short `json.loads/dumps` snippets, then shuts it down. With five chatbots that is five kernel processes per question. The cost is seconds of latency and noticeable CPU and memory. The `seconds` shown as "response time" also includes kernel start-up, so it is not model latency. `KernelPool` also calls `atexit.register(self.close)` once per runtime, so a long-running server collects one atexit entry, and the object it references, per question.
2. **`views/multi_chat.py` does too much.** At 416 lines it holds attachment parsing, two prompt paths, TTS and clipboard widgets, export serialization, state restore and persist, and the main loop. Export/parse (`export_yaml` here, `parse_export` in `review.py`) and the per-message rendering (`show` vs `show_chat`) are each written twice and have already drifted apart. For example, Review omits the Ollama context warning and shows a different caption layout.
3. **There are no tests.** `tidy_markdown`, `link_check`, `chat_db`, `block`/`export_yaml` → `parse_export` round-tripping, and the graph's `trim` arithmetic are all pure functions and easy to test. Two of the bugs below would have been caught by a small test.
4. **The prompt rules exist in two copies.** `FORMAT_RULES` (`views/multi_chat.py:42`) repeats the `answer` node's prompt in the graph by hand, and the system prompt in the image path is a third wording of the same rules.
5. **Every database call reopens SQLite and re-runs the schema and migrations.** `connect()` runs `executescript(SCHEMA)` and two `PRAGMA table_info` checks each time, and `get_setting` runs several times per rerun (`_config()` is called once per sidebar dropdown). Also, `with connect() as con:` commits but does **not** close the connection. The `sqlite3` context manager handles only the transaction, so closing is left to the garbage collector.
6. **Every save rewrites the whole session.** `save_session` deletes and re-inserts every message. That is fine at today's sizes, but with "Keep attached files" on, each user message can hold up to 100k characters per file, and all of it is rewritten on every 👍.
7. **Side effects run on every rerun.** `export_yaml()` serializes all histories on every rerun because `download_button` is given the data eagerly. `ollama_context()` makes an uncached HTTP call per Ollama reply.
8. **Link checking blocks the reply.** Up to 30 URLs, each with up to two requests at a 6-second timeout and 8 workers, run inside the same spinner as the model call. A reply with many links to slow sites can add tens of seconds before anything is shown.
9. **Configuration is scattered.** Defaults live in module constants in three files (`DEFAULT_MODELS`, `PANE_HEIGHT`, `OLLAMA_CTX_TOKENS`, `MAX_FILE_CHARS`), the graph's `last_n` default (10) differs from the UI's (5), and `FONT_SIZE` must be kept equal to `config.toml` by hand.

---

## 3. Bugs and gaps

Roughly ordered by impact.

| # | Where | Problem | Effect |
|---|---|---|---|
| B1 | `views/multi_chat.py:160-176`, `chat_db.py:69` | **Two browser tabs (or two users) write to the same session.** Each new browser session restores `latest_session_id()` and then calls `save_session(session_id, ...)`, which **deletes all messages** and writes its own. | Last writer wins: one tab's conversation silently replaces the other's in the database. Data loss. |
| B2 | `link_check.py:17` | **verified.** The URL regex stops at `)`, so `https://en.wikipedia.org/wiki/Graph_(abstract_data_type)` becomes `…Graph_(abstract_data_type`. | The cut URL is checked (and is likely reported dead), and `flag_dead` then puts ❌ *inside* the Markdown link target, breaking the link. Wikipedia links, which models give often, are hit hardest. |
| B3 | `link_check.py:61-63` | **verified.** `flag_dead` matches a dead URL that is a prefix of another URL: `https://x.com/a` dead and `https://x.com/a.html` ok gives `https://x.com/a ❌.html`. The lookahead `(?![\w/])` does not exclude `.`, `-`, `?`, `#`. | A valid link is corrupted. |
| B4 | `link_check.py:28-45` | **The private-network guard can be bypassed.** `_public()` checks the host once, but `urlopen` follows redirects without checking again, and resolves DNS again (rebinding). A URL in a model reply (which could come from a prompt-injected attachment) that redirects to `http://127.0.0.1:…` or `169.254.169.254` gets requested. | The module docstring's promise does not hold. Only ok/dead leaks back, so the impact is low, but local services can be probed. Fix: a `HTTPRedirectHandler` that runs `_public()` on each `Location`. |
| B5 | `views/multi_chat.py:378-391` | **A chatbot skipped as a duplicate falls permanently out of step.** Its history does not get the question or reply, so on the next question its recent history differs from its twin's, it is no longer a duplicate, and it is asked with a conversation missing turns. | The comparison silently breaks after the first duplicate. Fix: copy the twin's reply into the skipped chatbot's history (marked as shared), or keep skipping consistently. |
| B6 | `views/multi_chat.py:183`, `:298` | **Hidden chatbots' histories are still saved and exported.** If you lower "Number of chatbots" from 5 to 3, `history_3` and `history_4` stay in session state, get saved and appear in Export and on Review, with gaps for questions they never saw. | Exports and the Review page are misleading. |
| B7 | Review "Disable" vs Chats page | Disabling the session that is currently open on the Chats page does not detach it. `persist()` keeps writing to the disabled session (and `save_session` does not re-enable it). | Later chats are saved into a hidden session; the user believes they are lost. |
| B8 | `views/multi_chat.py:376-380` | `qnum` goes up before the "no chatbot has a model" check. | Question numbers get gaps (`[3]` → `[5]`), which defeats their purpose of lining the columns up. |
| B9 | `views/multi_chat.py:400` | `error_{i}` / `warn_{i}` are cleared only for chatbots that were asked this turn. A chatbot whose model was removed keeps its old error. | A stale error message stays on screen. |
| B10 | `views/multi_chat.py:334` | The Reset help text says "three chatbots (sonnet, opus, gemma3), 10 turns", but the defaults are opus, sonnet, `lfm3-omni` and 5 turns. | The documentation is wrong. |
| B11 | `chat_models.py:18` | `"fable": "claude-fable-5-1"`. The current Fable model id is `claude-fable-5-2`. | Possibly an outdated default; check with the Claude CLI. |
| B12 | `views/multi_chat.py:407` | `est_tokens` counts `history[-2*last_n:]` plus the question, but leaves out the prompt template and system text, and is estimated *after* the call. | The overflow warning appears only after a truncated answer has been paid for. Estimate before calling and warn (or trim) first. |
| B13 | `views/review.py:83` | Import accepts only `*.chat.yaml`. A browser-renamed duplicate (`…chat (1).yaml`) and the older export in `docs/chat-history/GraphLang-Multi-Chats-261113-033758.yaml` are rejected. | An avoidable UX failure: `parse_export` already validates the content. |
| B14 | `views/review.py:41` | `int(a.get("chatbot") or 1)` raises `TypeError` (not caught) for a list or dict value; only `ValueError` is handled. | A malformed import crashes the page instead of showing the error. |
| B15 | Export ↔ Review | The round trip loses `shown` vs `content` (attachments), `links`, token estimates and the session id. Imported chats cannot be saved or continued. | A gap rather than a bug, but the YAML is not a full backup. |
| B16 | `chat_db.py` | `delete_session()` and `chat_models.has_choice()` are never used, and there is no hard delete or "empty the trash". | Dead code; disabled sessions (with large attachments) pile up indefinitely. |
| B17 | `README.md` | The layout table is missing `views/review.py`, `chat_db.py`, `chat_text.py` and `link_check.py`. It says model choices are saved in `~/.graflang/chat_models.json`, but they are now in SQLite. | Outdated docs. |
| B18 | Whole app | No way to cancel or time out a slow model call; `future.result()` has no timeout. | One stuck chatbot blocks every column. |

---

## 4. New features

Ordered by value to a "compare models side by side" app, with a rough size estimate.

### High value

1. **Streaming replies, one column at a time** (M). Today nothing shows until the slowest chatbot finishes. Stream each chatbot's tokens into its column (`st.write_stream` per placeholder, fed from the threads through a queue). The bypass in `ask_with_images` already shows the adapter path; a streaming `GENERATE` in GraphLang would keep it on the graph. This also gives real **time to first token** and **total time** numbers for comparison.
2. **Reuse one runtime or kernel per chatbot** (S, mostly in GraphLang). A long-lived runtime per chatbot or per thread (or a `backend: inline` for pure-Python SOLVE nodes) removes the per-question kernel start-up from W1 and makes `seconds` honest.
3. **Token and cost metering per answer and per session** (S). `MeteredLLM` already sits on every call; show input/output tokens and $ per answer (OpenRouter prices are already fetched in `openrouter_models()`), with session totals in the sidebar. This turns the comparison into quality vs cost vs speed.
4. **Blind mode and side-by-side scoring** (M). Hide the model names until the user votes, and add "which answer is best" per question in addition to 👍/👎. Add a small leaderboard on the Review page (win rate, average seconds, average cost per model) built from the `messages` table. This uses the feedback data the app already collects.
5. **Per-chatbot system prompt and temperature** (S). Compare prompts as well as models: the same model with two system prompts is a common evaluation. Add it as a graph input so the CLI gets it too.

### Medium value

6. **Retry or re-ask a single chatbot** (S): a ↻ on a failed or poor answer that re-runs just that column for the same question number, keeping the alternatives.
7. **Continue a saved session from Review** (S): "Open in Chats" loads the session (and fixes B1/B7 by making the active session explicit). Also **hard delete** and **empty disabled**.
8. **Batch / eval mode** (M): upload a list of questions (CSV/YAML) and run them all against all chatbots without manual typing, then export. This is the same graph run in a loop, and GraphLang's CLI shows it is already possible.
9. **Answer diff and agreement view** (M): highlight where the answers to one question disagree (a text diff, or an LLM-judge summary of agreements and contradictions, run as a second small graph).
10. **Markdown/HTML export and an export with full fidelity** (S): include attachments metadata, links and token counts so an import can recreate the session; add a shareable HTML report.

### Nice to have

11. **Search across saved chats** on the Review page (SQLite FTS5 on `messages.content`).
12. **More providers**: the direct Anthropic API (with an API key, also unlocking images for Claude), OpenAI-compatible endpoints (vLLM, LM Studio), Gemini.
13. **Model presets**: save named chatbot line-ups ("local only", "frontier", "zh vs en") instead of rebuilding the sidebar.
14. **Rendering of math (KaTeX) and Mermaid** in answers, since models produce both often.

---

## Suggested order of work

1. Fix B2/B3 (link parsing and flagging) and B4 (redirect check), with tests in a new `tests/test_link_check.py`.
2. Fix B1 (give each browser session its own new session id unless the user explicitly opens an old one) and B7.
3. Fix B5, B6, B8 (make the chatbots' histories stay aligned).
4. Reuse runtimes or kernels (feature 2) and then add streaming (feature 1).
5. Split `views/multi_chat.py` into `chat_export.py` (shared by both pages), `chat_render.py` (shared `show`), and the page itself.

---

## 5. Build status (2026-11-14)

The fixes and features below are in the working tree on `main`, **not yet committed**.

### Verification

| Check | Result |
|---|---|
| `pytest tests` (47 tests, no model calls: mock adapter, fake `ask`, temporary database) | ✅ 47 passed |
| `graf validate chat_turn.graph.yaml` and `compare_answers.graph.yaml` | ✅ both valid |
| One real call and one comparison through local Ollama (`qwen2.5`): tokens, model time, cost 0 | ✅ |
| Paid providers (Anthropic CLI or API, OpenRouter, OpenAI, DeepSeek, Qwen) | ⏳ not called; needs a manual test |
| Clicking through every new feature in a browser | ⏳ needs a manual test (checklist below) |

Environment note: `streamlit-aggrid` (already in `requirements.txt`) was missing from the `graf` conda environment, so it was installed. Without it the Review page cannot load.

### Bugs

| # | Status | Fix | Where |
|---|---|---|---|
| B1 | ✅ fixed | sessions carry a `rev`; a save with an out-of-date rev forks a new session instead of overwriting | `chat_db.save_session` |
| B2 | ✅ fixed | URLs keep balanced brackets; a Markdown link ends at its own `)` | `link_check.find_urls` |
| B3 | ✅ fixed | ❌ is not put inside a longer URL; dead URLs are flagged longest first | `link_check.flag_dead` |
| B4 | ✅ fixed | connections go to the checked public IP, every redirect is checked, and only http(s) redirects are followed | `link_check` |
| B5 | ✅ fixed | a duplicate chatbot gets the twin's reply, marked `shared_from` | `run_round` |
| B6 | ✅ fixed | only visible chatbots are saved and exported (the conversations of hidden ones are dropped from the session at the next save) | `visible(n)` |
| B7 | ✅ fixed | writing to a disabled session forks a new one, with a notice on the page | `chat_db.save_session` |
| B8 | ✅ fixed | the question number goes up only once something is asked | `ask_new` |
| B9 | ✅ fixed | errors and warnings are cleared for every chatbot when a new question is asked | `ask_new` |
| B10 | ✅ fixed | the Reset help text is built from `DEFAULT_MODELS` and `DEFAULT_TURNS` | Chats sidebar |
| B11 | ✅ fixed | `claude-fable-5-2` | `chat_models.ANTHROPIC` |
| B12 | 🟡 partly fixed | the estimate counts the prompt template and instructions and uses the history from before the call; still four characters per token | `chat_engine.estimate_tokens` |
| B13 | ✅ fixed | any `.yaml` / `.yml` file imports; the content is validated instead of the file name | Review page |
| B14 | ✅ fixed | bad numbers fall back to defaults instead of crashing | `chat_export.parse_export` |
| B15 | ✅ fixed | the YAML export includes prompt, tokens, cost, best, links, temperature, instructions and comparisons; it round-trips | `chat_export` |
| B16 | ✅ fixed | Delete and Delete-all-disabled on the Review page; `has_choice` removed | Review page, `chat_db` |
| B17 | ✅ fixed | README rewritten (features, providers, layout, tests) | `README.md` |
| B18 | ✅ fixed | a reply timeout (sidebar settings); slow chatbots are given up and get a Retry button | `run_round` |

### Weaknesses

| # | Status | Notes |
|---|---|---|
| W1 kernel per run | ✅ fixed in the app | kept kernel pools are lent to runs: about 0.7 s down to 0.03 s per run. This uses GraphLang's undocumented `runtime.kernels` and falls back if it is missing |
| W2 large page file | ✅ fixed | `chat_engine.py`, `chat_export.py` and `chat_render.py` split out; the Chats and Review pages share the export and rendering code |
| W3 no tests | ✅ fixed | `tests/`, with `requirements-dev.txt` |
| W4 rules written twice | ✅ fixed | `rules` is a graph input; the image path reads its default |
| W5 SQLite setup per call | ✅ fixed | `connect()` closes connections; the schema is checked once per process |
| W6 whole-session rewrite | ❌ not changed | still deletes and re-inserts the session's messages on every save |
| W7 side effects every rerun | ✅ fixed | downloads are built on click; `ollama_context` is asked only after a reply |
| W8 link check blocks the reply | 🟡 partly | each column shows its answer once its own links are checked, so one slow site no longer holds up the other columns |
| W9 scattered defaults | 🟡 partly | the graph and the UI now share `last_n = 5`; the other constants stay in their modules |

### Features

| # | Feature | Status | Notes |
|---|---|---|---|
| 1 | Streaming replies | 🟡 partly | each answer appears as soon as its chatbot finishes; dd-llm has no streaming call, so text is not streamed token by token and time to first token is not measured |
| 2 | Runtime / kernel reuse | ✅ | in the app (see W1) |
| 3 | Tokens and cost | ✅ | per answer, and totals per chat in the sidebar; cost is what the provider reports, or OpenRouter's prices times the tokens; the Claude CLI's cost is unknown (shown as "~" estimated tokens) |
| 4 | Blind mode, 🏆 pick, leaderboard | ✅ | Settings → Blind mode; Review → Leaderboard |
| 5 | Per-chatbot instructions and temperature | ✅ | the Claude CLI ignores temperature |
| 6 | Retry / redo one chatbot | ✅ | Retry under an error; ↻ under the latest answer (images are not sent again) |
| 7 | Continue a saved session, hard delete | ✅ | Review → Open in Chats / Delete |
| 8 | Batch mode | ✅ | sidebar → Batch (.txt, .md, .csv, .yaml; at most 100 questions) |
| 9 | Answer comparison | ✅ | "Compare answers" under the columns; `compare_answers.graph.yaml` |
| 10 | Export with everything, report | ✅ | YAML (round-trips) and Markdown; no HTML report |
| 11 | Search saved chats | ✅ | LIKE search over titles, questions and answers (not FTS5) |
| 12 | More providers | ✅ | `anthropic-api`, `openai`, `deepseek`, `qwen`; no Gemini (`google-genai` is not installed) |
| 13 | Presets | ✅ | sidebar → Presets |
| 14 | KaTeX / Mermaid | ❌ | LaTeX already renders in `st.markdown`; Mermaid would need a component |

### Manual test checklist

Restart the app first (`streamlit run app.py`) so the new modules load. On first use the existing database gains new columns (additive only). Use Ollama models for free runs.

1. **Ask**: 3 chatbots and one question. Each column fills in as its answer arrives; captions show seconds, tokens and cost; the sidebar shows the chat's totals.
2. **Twins**: give two chatbots the same model and settings, then ask twice. The second gets "same as chatbot N", and both columns keep the same number of turns.
3. **Instructions and temperature**: give one chatbot instructions ("answer in one sentence"); its header shows 📝 and its answer follows the instructions.
4. **Retry and redo**: pick a model that fails (for example a provider without its API key). An error appears with Retry; fix the setting and press Retry. Press ↻ under a latest answer and it is replaced.
5. **Timeout**: set the reply timeout to 30 s with a slow model. It is given up with a message and can be retried.
6. **Blind mode and 🏆**: turn on Blind mode. Headers read "Chatbot N" and captions hide the model; pick 🏆 on one answer, and that question's captions show the models. Only one 🏆 per question.
7. **Compare answers**: open the expander under the columns, choose a question and a model, and press Compare. The result is shown and kept after a reload.
8. **Batch**: upload a .txt with 3 lines. All chatbots answer all 3 in turn, with a progress bar.
9. **Presets**: save the line-up as "test", change a model, then load "test". The line-up comes back.
10. **Export**: Export → YAML and Markdown both download. Import the YAML on the Review page; it shows the same chat, and "Save as a chat session" adds it to the table.
11. **Two tabs**: open the Chats page in two tabs and ask in both. The second tab shows the "continues as a new session" notice, and both chats appear on the Review page.
12. **Review**: search for a word from an answer; Open in Chats continues that chat; Disable then Restore; Delete (on a disabled row) and Delete all disabled ask for confirmation first.
13. **Leaderboard**: Review → Leaderboard lists each model with wins, win rate, thumbs, average seconds and cost.
14. **Links**: ask for "the Wikipedia page on graph (abstract data type)". The link stays whole, and any ❌ goes after the link, never inside it.
15. **Images**: attach a picture with an Ollama vision model or an `anthropic-api` model. The `anthropic` (CLI) chatbot reports that it cannot read images.

### Still best done upstream

- GraphLang: a kernel scope that lasts across runs (so `chat_engine` would no longer need to swap `runtime.kernels`), a temperature taken from a run's inputs, and a streaming `GENERATE`.
- dd-llm: a `stream()` call on the adapters.
