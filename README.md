# Multi-Chat

Ask one question, let up to five LLMs answer it side by side. A small, working showcase of [GraphLang](https://github.com/digital-duck/graflang): the chat logic is a graph file (`chat_turn.graph.yaml`), and this Streamlit app only draws the conversation and runs that graph, once per chatbot per question.

## Features

- **Compare models**: 1 to 5 chatbots, each with its own provider, model, "turns it sees", language, temperature and instructions (a system prompt, so you can compare prompts as well as models). Every question goes to all of them in parallel, each keeps its own conversation, and each answer appears in its column as soon as it arrives.
- **Providers**: `anthropic` (through the Claude CLI), `anthropic-api`, `openrouter`, `openai`, `deepseek`, `qwen`, `ollama`.
- **Settings page**: fetch each provider's model list, tick the models you want; only those appear in the Model dropdown (saved in the SQLite database `~/.graflang/multi_chat.db`).
- **Easy to line up**: numbered questions (`[4] what is ...`) in every column, response time, tokens and cost per answer, totals per chat, a scrollbar per chatbot, adjustable pane height and font size.
- **Feedback and blind mode**: 📋 copy, 🔊 read aloud, 👍 / 👎, 🏆 best answer per question. Blind mode hides the model names until you pick the best answer.
- **Compare answers**: a model of your choice compares the answers to one question (agree, differ, doubtful, most complete); it is a second graph, `compare_answers.graph.yaml`.
- **Retry and redo**: a failed chatbot can be retried alone; ↻ asks a chatbot its latest question again.
- **Batch**: upload a list of questions (.txt, .csv, .yaml) and every chatbot answers them all.
- **Presets**: save a line-up of chatbots under a name and load it later.
- **Attachments**: text files and PDFs go to every chatbot; images to the providers that can read them.
- **Dead links flagged**: the links in each answer are checked, and dead ones get ❌ (addresses on your own machine or network are never requested).
- **Export**: YAML (`GraphLang-YYMMDD-HHMMSS.chat.yaml`, complete, can be imported again) or Markdown, organised by question.
- **Review page**: every chat is saved; search them, read them, open one on the Chats page to go on, disable or delete it, import an export file. A leaderboard shows, per model, the 🏆 wins, thumbs, time and cost.
- **About page**: how a question travels through the graph, and the stack behind it.

## Install and run

Python 3.11 or newer.

```bash
git clone https://github.com/digital-duck/multi-chat.git
cd multi-chat

python -m venv .venv && source .venv/bin/activate
# conda create -n chat python=3.11
# conda activate chat

pip install -r requirements.txt
streamlit run app.py
```

What each provider needs:

| Provider | Needs | Cost |
|---|---|---|
| `anthropic` | the [Claude CLI](https://docs.claude.com/en/docs/claude-code) installed and signed in (no images, no temperature) | billed |
| `anthropic-api` | `export ANTHROPIC_API_KEY=...` | billed |
| `openrouter` | `export OPENROUTER_API_KEY=...` (the model list loads without it) | billed |
| `openai` | `export OPENAI_API_KEY=...` | billed |
| `deepseek` | `export DEEPSEEK_API_KEY=...` | billed |
| `qwen` | `export DASHSCOPE_API_KEY=...` | billed |
| `ollama` | [Ollama](https://ollama.com) running at `localhost:11434` (or `$OLLAMA_HOST`) with a model pulled | free |

Costs shown are what the provider reports, or OpenRouter's prices times the tokens; the Claude CLI's cost is not known (its token counts are estimates).

`requirements.txt` installs GraphLang from GitHub, because this app needs `graphlang.grafr.metering`, which the PyPI release (0.1.1) does not have yet.

## The graph

```
add_question (SOLVE) -> trim (SOLVE) -> answer (GENERATE) -> add_reply (SOLVE)
```

The history and the question go in (with the chatbot's language, instructions and the formatting rules); the question is appended, the history is trimmed to the last N turns, the model replies (one call, capped at one per run), and the reply and the new history come out. The same graph runs without the app:

```bash
graf validate chat_turn.graph.yaml
graf run chat_turn.graph.yaml --llm claude_cli:claude-sonnet-5-5 \
    --param question="What is a graph?" --param history='[]'
```

## Layout

| Path | What |
|---|---|
| `app.py` | entry point: the four pages |
| `views/multi_chat.py` | the Chats page |
| `views/review.py` | the Review page: saved chats, import, leaderboard |
| `views/config_llm.py` | the Settings page |
| `views/about.py` | the About page |
| `chat_turn.graph.yaml` | one chat turn as a GraphLang graph |
| `compare_answers.graph.yaml` | a comparison of the answers to one question |
| `chat_engine.py` | runs the graphs (and the image turns, which bypass them); tokens, cost, kernel reuse |
| `chat_db.py` | SQLite: sessions, messages, comparisons, settings |
| `chat_models.py` | providers, their model lists and the saved selection |
| `chat_export.py` | YAML and Markdown export, YAML import |
| `chat_render.py` | how an answer is shown (both pages) |
| `link_check.py` | the dead-link check |
| `tests/` | `pytest tests` (no model is called) |

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests
```

## Status

An early prototype, built on an early prototype ([GraphLang](https://github.com/digital-duck/graflang)). Anthropic and OpenRouter calls cost money: every question is one call per chatbot.

## License

Apache License 2.0, see `LICENSE`.
