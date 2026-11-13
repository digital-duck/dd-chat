# Multi-Chat

Ask one question, let up to five LLMs answer it side by side. A small, working showcase of [GraphLang](https://github.com/digital-duck/graflang): the chat logic is a graph file (`chat_turn.graph.yaml`), and this Streamlit app only draws the conversation and runs that graph, once per chatbot per question.

## Features

- **Compare models**: 1 to 5 chatbots, each with its own provider, model and "turns it sees"; every question goes to all of them in parallel, and each keeps its own conversation.
- **Providers**: `anthropic` (through the Claude CLI), `openrouter`, `ollama`.
- **Config LLM page**: fetch each provider's model list, tick the models you want; only those appear in the Model dropdown (saved in `~/.graflang/chat_models.json`).
- **Easy to line up**: numbered questions (`[4] what is ...`) in every column, response time per answer, a scrollbar per chatbot, adjustable pane height and font size.
- **Feedback**: 📋 copy Markdown, 👍 / 👎 under each answer.
- **Export**: one YAML file (`GraphLang-Multi-Chats-YYMMDD-HHMMSS.yaml`) organised by question, with each chatbot's id, `<provider>:<model-id>`, response time, response and feedback (1, -1, 0).
- **About page**: how a question travels through the graph, and the stack behind it.

## Install and run

Python 3.11 or newer.

```bash
git clone https://github.com/digital-duck/multi-chat.git
cd multi-chat
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

What each provider needs:

| Provider | Needs | Cost |
|---|---|---|
| `anthropic` | the [Claude CLI](https://docs.claude.com/en/docs/claude-code) installed and signed in | billed |
| `openrouter` | `export OPENROUTER_API_KEY=...` (the model list loads without it) | billed |
| `ollama` | [Ollama](https://ollama.com) running at `localhost:11434` with a model pulled | free |

`requirements.txt` installs GraphLang from GitHub, because this app needs `graphlang.grafr.metering`, which the PyPI release (0.1.1) does not have yet.

## The graph

```
add_question (SOLVE) -> trim (SOLVE) -> answer (GENERATE) -> add_reply (SOLVE)
```

The history and the question go in; the question is appended, the history is trimmed to the last N turns, the model replies (one call, capped at one per run), and the reply and the new history come out. The same graph runs without the app:

```bash
graf validate chat_turn.graph.yaml
graf run chat_turn.graph.yaml --llm claude_cli:claude-sonnet-5-5 \
    --param question="What is a graph?" --param history='[]'
```

## Layout

| Path | What |
|---|---|
| `app.py` | entry point: the three pages |
| `views/multi_chat.py` | the Multi-Chat page |
| `views/config_llm.py` | the Config LLM page |
| `views/about.py` | the About page |
| `chat_models.py` | model lists per provider and the saved selection |
| `chat_turn.graph.yaml` | one chat turn as a GraphLang graph |

## Status

An early prototype, built on an early prototype ([GraphLang](https://github.com/digital-duck/graflang)). Anthropic and OpenRouter calls cost money: every question is one call per chatbot.

## License

Apache License 2.0, see `LICENSE`.
