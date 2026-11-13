"""About: what this app is, how a question travels through GraphLang, and what it depends on."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path

import streamlit as st
import yaml

GRAPH = Path(__file__).resolve().parent.parent / "chat_turn.graph.yaml"
REPO = "https://github.com/digital-duck/multi-chat"
STACK = [      # package, what it does here, repository
    ("graflang", "the GraphLang runtime (`graphlang` package): loads `chat_turn.graph.yaml` and runs it as token-passing dataflow", "https://github.com/digital-duck/graflang"),
    ("dd-llm", "the LLM adapters behind `GENERATE`: `claude_cli`, `openrouter`, `ollama`", "https://github.com/digital-duck/dd-llm"),
    ("dd-logging", "run logs (GraphLang's logging layer)", "https://github.com/digital-duck/dd-logging"),
    ("zinet", "graph code shared with GraphLang (drawing, graph algorithms); not used directly by this app", "https://github.com/digital-duck/zinet"),
    ("ipykernel", "the Python kernel that runs the graph's `SOLVE` nodes (`backend: kernel`)", "https://ipython.org"),
    ("streamlit", "this web app", "https://streamlit.io"),
]


def version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


def graph_dot(doc: dict) -> str:
    """The graph as Graphviz DOT: control flow (`triggers`) solid, data (`binds`) dashed, one box per node."""
    shape = {"INPUT": "ellipse", "OUTPUT": "ellipse", "GENERATE": "box3d"}
    lines = ["digraph G { rankdir=LR; node [fontsize=11]; edge [fontsize=9];"]
    for node in doc["nodes"]:
        label = f"{node['id']}\\n{node['type']}"
        lines.append(f'  "{node["id"]}" [label="{label}", shape={shape.get(node["type"], "box")}, '
                     f'style=filled, fillcolor="{"#ffd8a8" if node["type"] == "GENERATE" else "#e7f5ff"}"];')
    for edge in doc["edges"]:
        (src, sport), (dst, dport) = edge["source"].split(".", 1), edge["target"].split(".", 1)
        if edge["type"] == "triggers":
            lines.append(f'  "{src}" -> "{dst}";')
        else:
            lines.append(f'  "{src}" -> "{dst}" [style=dashed, color="#868e96", label="{sport.removeprefix("out_")}"];')
    return "\n".join(lines + ["}"])


st.title("ℹ️ About Multi-Chat")
st.markdown(f"""
**Multi-Chat** asks one question and lets up to five chatbots answer it at the same time, side by side, so you can compare models
on the same prompts. It is also a small, working showcase of **[GraphLang]({STACK[0][2]})**: the chat logic is not Python code in
this app, it is a graph file, and the app only draws the conversation and runs that graph.

Source: [{REPO}]({REPO}) · GraphLang: [{STACK[0][2]}]({STACK[0][2]})
""")

st.header("What you can do")
st.markdown("""
- **Compare models.** Choose 1 to 5 chatbots, each with its own provider, model and "turns it sees" (how many recent
  questions and replies it is sent). Every question goes to all of them in parallel; each keeps its own conversation.
- **Providers.** `anthropic` (through the Claude CLI, billed), `openrouter` (hundreds of hosted models, billed, needs
  `OPENROUTER_API_KEY`) and `ollama` (local models, free).
- **Config LLM page.** Fetch each provider's model list and tick the models you want; only those are options in the Model
  dropdown. The choice is saved in `~/.graflang/chat_models.json`.
- **Line up the answers.** Every question is numbered (`[4] what is ...`) in all columns, so the same question is easy to find.
  Each answer shows its model and response time. Each column scrolls on its own; pane height and font size are settings.
- **Give feedback.** Under each answer: 📋 copies its Markdown, 👍 and 👎 record your judgement (click again to undo).
- **Export.** One YAML file, organised by question: for each, every chatbot's id, `<provider>:<model-id>`, response time,
  response (as multi-line YAML, so nothing needs quoting) and your feedback (1, -1 or 0).
""")

st.header("How a question travels")
st.markdown("""
Each chatbot's turn is **one run of the graph below**. The history so far and the new question go in; the question is appended,
the history is trimmed to the last N turns, the model replies, and the reply and the new history come out. The page keeps the
conversation and shows it; it holds none of the chat logic.
""")
try:
    doc = yaml.safe_load(GRAPH.read_text(encoding="utf-8"))
    st.graphviz_chart(graph_dot(doc), width="stretch")
    st.caption("`chat_turn.graph.yaml`: solid arrows pass control, dashed arrows carry data. The orange node is the only LLM call.")
    with st.expander("The graph file"):
        st.code(GRAPH.read_text(encoding="utf-8"), language="yaml")
except (OSError, yaml.YAMLError, KeyError) as e:
    st.warning(f"could not draw the graph ({type(e).__name__}: {e})")
st.markdown("""
| Node | Type | Does |
|---|---|---|
| `add_question` | `SOLVE` (Python kernel) | appends the question to the history |
| `trim` | `SOLVE` (Python kernel) | keeps the last N turns, so a long chat stays within the model's context |
| `answer` | `GENERATE` | one LLM call, capped at one per run (`MeteredLLM(max_calls=1)`) |
| `add_reply` | `SOLVE` (Python kernel) | appends the reply, giving the new history |

Because the logic is a graph, it can be checked, drawn and run without this page:

```bash
graf validate chat_turn.graph.yaml
graf run chat_turn.graph.yaml --llm claude_cli:claude-sonnet-5-5 --param question="What is a graph?" --param history='[]'
```
""")

st.header("What GraphLang is")
st.markdown(f"""
GraphLang is a graph-first language for agentic workflows. The graph (YAML, `*.graph.yaml`) is the program: typed nodes with
ports, joined by `triggers` (control) and `binds` (data) edges. Node types include `GENERATE` (an LLM call), `SOLVE` and `ASSERT`
(deterministic steps: an expression, a Python kernel, or any program), `FORK`/`JOIN` (explicit parallelism), `LOOP`, `GUARD` (policy
checks) and `MUTATE` (a model proposes a change to the graph itself, applied only if it passes validation). Where LangGraph or
AutoGen describe a graph in Python code, GraphLang keeps the graph as the source of truth.

This app uses only a small, linear corner of that: one `GENERATE` between three `SOLVE` steps. GraphLang is an **early prototype**:
see its [README]({STACK[0][2]}) for what is implemented and what is still aspirational.
""")

st.header("The stack")
st.markdown("| Package | Version | Role |\n|---|---|---|\n" + "\n".join(
    f"| [{name}]({url}) | {version(name)} | {role} |" for name, role, url in STACK))
st.caption("Multi-Chat is released under the Apache License 2.0.")
