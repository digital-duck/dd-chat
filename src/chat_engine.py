"""Asking the models: one chatbot's turn, a turn with pictures, and a comparison of answers. No Streamlit here, so it runs in the
chatbots' threads and in tests (with the `mock` adapter).

A text turn is one run of `chat_turn.graph.yaml`; a comparison is one run of `compare_answers.graph.yaml`. Every run is metered
(`MeteredLLM`, capped at one model call), and its tokens, cost and model time come back with the reply.

Kernels: the graph's SOLVE nodes run in a Python (Jupyter) kernel. GraphLang starts a kernel for each run and stops it at the end, which
costs most of a second per chatbot per question. Here a few kernel pools are kept and lent to runs one at a time (`_kernels`); the
runtime's own pool is swapped for a lent one before the run. This leans on `GraphLangRuntime.kernels`, an attribute GraphLang does not
document: when it is missing the run simply starts its own kernel as before. The kernels' sockets go in the repo's `tmp/`
(`GRAFLANG_KERNEL_DIR`; set it yourself to choose another folder).
"""
from __future__ import annotations

import atexit
import base64
import json
import os
import queue
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml
from graphlang.grafr.adapters import make_llm
from graphlang.grafr.kernel import KernelPool
from graphlang.grafr.metering import MeteredLLM
from graphlang.grafr.runtime import GraphLangRuntime, load_graph

ROOT = Path(__file__).resolve().parent
TMP = ROOT.parent / "tmp"                   # the repo's tmp/ (git-ignored): the kernels' Unix sockets go here, not in the folder Streamlit started in
os.environ.setdefault("GRAFLANG_KERNEL_DIR", str(TMP))      # read by GraphLang when a kernel starts; an older GraphLang ignores it
TMP.mkdir(exist_ok=True)
TMP.chmod(0o700)                            # the sockets accept code: only this user may reach them
GRAPH = ROOT / "chat_turn.graph.yaml"
COMPARE_GRAPH = ROOT / "compare_answers.graph.yaml"
SAME_LANGUAGE = "the same language as the most recent user message"
IMAGE_FORMATS = {"ollama": "openai", "openrouter": "openai", "openai": "openai", "qwen": "openai", "anthropic": "anthropic"}  # adapter -> image format
CHARS_PER_TOKEN = 4


@dataclass
class Turn:
    """What one chatbot is asked with: `adapter:model`, recent turns it sees, reply language, its own instructions and temperature."""
    llm: str
    last_n: int = 5
    language: str = ""              # "" = the language of the question
    instructions: str = ""
    temperature: float | None = None
    prices: tuple[float, float] | None = None      # $ per million prompt / completion tokens, to estimate a cost the adapter does not report


@dataclass
class Answer:
    reply: str
    seconds: float                  # wall time of the turn, kernel and all
    model_ms: int = 0               # time inside the model call
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None   # None: not known (the adapter reports none and the price is not known)

    def fields(self) -> dict:
        return asdict(self)


@lru_cache(maxsize=None)
def graph_input_default(name: str, graph: Path = GRAPH) -> str:
    """The default of a graph input, so the image turns use the same text as the graph (e.g. `rules`)."""
    doc = yaml.safe_load(graph.read_text(encoding="utf-8"))
    return next(str(i.get("default", "")) for i in doc["workflow"]["inputs"] if i["name"] == name)


def reply_language(language: str) -> str:
    """The phrase that completes "Write the reply in ...": the chatbot's language, or the language of the question when it has none."""
    return language or SAME_LANGUAGE


# ---- kernels lent to runs

class _KeptPool(KernelPool):
    """A kernel pool that outlives the run: the runtime's `close()` at the end of a run does nothing, `shutdown()` really stops it."""

    def close(self) -> None:
        pass

    def shutdown(self) -> None:
        KernelPool.close(self)


_idle: "queue.SimpleQueue[_KeptPool]" = queue.SimpleQueue()


@contextmanager
def _kernels() -> Iterator[_KeptPool]:
    """An idle kept pool, or a new one; given back afterwards. A pool whose run failed is stopped, not reused (its kernel may be broken)."""
    try:
        pool = _idle.get_nowait()
    except queue.Empty:
        pool = _KeptPool("python3", str(ROOT))
        atexit.register(pool.shutdown)
    try:
        yield pool
    except BaseException:
        pool.shutdown()
        raise
    _idle.put(pool)


def _run(graph: Path, llm: Any, inputs: dict) -> dict:
    runtime = GraphLangRuntime(load_graph(graph), llm=llm, verbose=False)
    if not isinstance(getattr(runtime, "kernels", None), KernelPool):     # a GraphLang without the attribute: let it start its own kernel
        return runtime.execute(inputs)
    with _kernels() as pool:
        runtime.kernels = pool
        return runtime.execute(inputs)


# ---- the model, with a chatbot's temperature

class _Tuned:
    """The run's LLM with this chatbot's temperature (GraphLang takes a temperature only from the node's config)."""

    def __init__(self, inner: Any, temperature: float | None):
        self.inner, self.temperature = inner, temperature

    def __getattr__(self, attr):
        if attr == "inner":
            raise AttributeError(attr)
        return getattr(self.inner, attr)

    def generate(self, prompt: str, model: str | None = None, **opts: Any) -> str:
        if self.temperature is not None:
            opts["temperature"] = self.temperature
        return self.inner.generate(prompt, model=model, **opts)


def _metered(turn: Turn) -> MeteredLLM:
    adapter, _, model = turn.llm.partition(":")
    return MeteredLLM(_Tuned(make_llm(adapter, model or None), turn.temperature), max_calls=1)


def _cost(reported: float | None, input_tokens: int, output_tokens: int, prices: tuple[float, float] | None) -> float | None:
    if reported:
        return reported
    if prices:
        return round((input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000, 6)
    return None


def _answer(reply: str, started: float, meter: MeteredLLM, prices: tuple[float, float] | None) -> Answer:
    snap = meter.snapshot()
    reported = None if meter.cost_unreported else snap["cost_usd"]
    if meter.name == "claude_cli":                  # the CLI reports cost 0 (subscription) and estimated tokens: the cost is not known
        reported = None
    return Answer(reply, time.monotonic() - started, snap["ms"], snap["input_tokens"], snap["output_tokens"],
                  _cost(reported, snap["input_tokens"], snap["output_tokens"], prices))


# ---- a chatbot's turn

def ask(question: str, history: list[dict], turn: Turn) -> Answer:
    """One run of the graph for one chatbot. The graph sees only role and content."""
    started = time.monotonic()
    meter = _metered(turn)
    plain = [{"role": m["role"], "content": m["content"]} for m in history]
    out = _run(GRAPH, meter, {"history": json.dumps(plain), "question": question, "last_n": turn.last_n,
                              "language": reply_language(turn.language), "instructions": turn.instructions,
                              "rules": graph_input_default("rules")})
    return _answer(out["reply"], started, meter, turn.prices)


def system_prompt(turn: Turn) -> str:
    """The system prompt of an image turn: the graph's prompt, said directly."""
    return " ".join(filter(None, ["You are a helpful assistant engaged in a multi-turn conversation.", turn.instructions,
                                  f"Reply helpfully, in {reply_language(turn.language)}.", graph_input_default("rules")]))


def image_messages(question: str, history: list[dict], last_n: int, images: list[tuple[str, bytes]], fmt: str) -> list[dict]:
    """The recent history, then the question with its images, in the adapter's message format."""
    recent = [{"role": m["role"], "content": m["content"]} for m in history[-2 * last_n:]]
    encoded = [(mime, base64.b64encode(data).decode("ascii")) for mime, data in images]
    if fmt == "anthropic":
        parts = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}} for mime, b64 in encoded]
        content = parts + [{"type": "text", "text": question}]
    else:
        content = [{"type": "text", "text": question}] + [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}} for mime, b64 in encoded]
    return recent + [{"role": "user", "content": content}]


def ask_with_images(question: str, history: list[dict], turn: Turn, images: list[tuple[str, bytes]]) -> Answer:
    """A turn with pictures, straight to the adapter (the graph passes text only)."""
    adapter, _, model = turn.llm.partition(":")
    fmt = IMAGE_FORMATS.get(adapter)
    if not fmt:
        raise RuntimeError("this chatbot cannot read images; use an Ollama vision model, or an OpenRouter, OpenAI, Qwen or Anthropic API model")
    import dd_llm
    started = time.monotonic()
    options = {"temperature": turn.temperature} if turn.temperature is not None else {}
    result = dd_llm.get_adapter(adapter).call(model=model, messages=image_messages(question, history, turn.last_n, images, fmt),
                                              system=system_prompt(turn), **options)
    if not result.success:
        errors = result.error_history or []
        raise RuntimeError(errors[-1]["error"] if errors else f"{adapter} call failed")
    tokens_in, tokens_out = int(result.input_tokens or 0), int(result.output_tokens or 0)
    return Answer(result.content, time.monotonic() - started, int(result.latency_ms or 0), tokens_in, tokens_out,
                  _cost(result.cost_usd, tokens_in, tokens_out, turn.prices))


def estimate_tokens(question: str, history: list[dict], turn: Turn) -> int:
    """A rough count of the prompt a turn sends (four characters a token): the recent history, the question, and the graph's own text."""
    recent = history[-2 * turn.last_n:]
    chars = sum(len(m["content"]) + 30 for m in recent) + len(question) + len(turn.instructions) + len(graph_input_default("rules")) + 300
    return chars // CHARS_PER_TOKEN


# ---- comparing answers

def compare(question: str, answers: list[tuple[str, str]], turn: Turn) -> Answer:
    """One run of the comparison graph: `answers` are (label, text)."""
    started = time.monotonic()
    meter = _metered(turn)
    text = "\n\n".join(f"### {label}\n{reply}" for label, reply in answers)
    out = _run(COMPARE_GRAPH, meter, {"question": question, "answers": text,
                                      "language": turn.language or "the same language as the question"})
    return _answer(out["comparison"], started, meter, turn.prices)
