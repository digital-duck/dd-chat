"""Export and import of a chat: YAML (complete, can be read back) and Markdown (to read or share).

The YAML is organised by question. Each question has its text (`question`, as the page shows it), the full prompt when attached files made
it longer (`prompt`), the answers (chatbot number, `<provider>:<model-id>`, language, seconds, tokens, cost, feedback 1 / -1 / 0, `best`,
temperature, instructions, links, `shared_from` when a twin chatbot's reply was copied, and the response) and the comparison, if one was
made. Older exports (just chatbot, llm, language, seconds, feedback, response) still import.
"""
from __future__ import annotations

from datetime import datetime

import yaml

from chat_models import ADAPTER_PROVIDER, PROVIDERS

ANSWER_FIELDS = ("seconds", "input_tokens", "output_tokens", "cost_usd", "temperature", "instructions", "links", "shared_from",
                 "est_tokens", "ctx_tokens")


class _Dumper(yaml.SafeDumper):
    pass


def _text(dumper: yaml.SafeDumper, value: str):
    """Multi-line text as a literal block (`|`), so a response reads as it was written; PyYAML falls back to quoting when it must."""
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


_Dumper.add_representer(str, _text)


def _clean(text: str) -> str:
    """Line ends unified and control characters (which YAML cannot hold) removed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


def by_question(histories: dict[int, list[dict]]) -> dict[int, dict]:
    """{question number: {question, prompt, answers: [(chatbot index, assistant message)]}} across the chatbots."""
    out: dict[int, dict] = {}
    for i, history in sorted(histories.items()):
        asked, pending = 0, None
        for m in history:
            if m["role"] == "user":
                asked += 1
                pending = m
            elif pending is not None:
                q = pending.get("q", asked)
                entry = out.setdefault(q, {"question": pending.get("shown") or pending["content"], "prompt": pending["content"], "answers": []})
                entry["answers"].append((i, m))
                pending = None
    return dict(sorted(out.items()))


def provider_llm(model: str | None) -> str:
    adapter, _, name = (model or ":").partition(":")
    return f"{ADAPTER_PROVIDER.get(adapter, adapter)}:{name}"


def export_yaml(histories: dict[int, list[dict]], judgements: dict[int, dict] | None = None, session: dict | None = None) -> str:
    questions = []
    for q, entry in by_question(histories).items():
        item: dict = {"number": q, "question": _clean(entry["question"])}
        if entry["prompt"] != entry["question"]:
            item["prompt"] = _clean(entry["prompt"])
        answers = []
        for i, m in entry["answers"]:
            a: dict = {"chatbot": i + 1, "llm": provider_llm(m.get("model")), "language": m.get("language") or "",
                       "feedback": m.get("feedback", 0), "best": bool(m.get("best"))}
            for f in ANSWER_FIELDS:
                if m.get(f) not in (None, "", {}):
                    a[f] = round(m[f], 2) if f == "seconds" else m[f]
            a["response"] = _clean(m["content"])
            answers.append(a)
        item["answers"] = answers
        if judgements and q in judgements:
            j = judgements[q]
            item["comparison"] = {"judge": provider_llm(j.get("judge")), "seconds": j.get("seconds"), "content": _clean(j["content"])}
        questions.append(item)
    doc: dict = {"exported": datetime.now().isoformat(timespec="seconds"), "format": 2}
    if session:
        doc["session"] = session
    doc["questions"] = questions
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000)


def export_markdown(histories: dict[int, list[dict]], judgements: dict[int, dict] | None = None, title: str = "Multi-Chat") -> str:
    lines = [f"# {title}", "", f"Exported {datetime.now():%Y-%m-%d %H:%M}", ""]
    for q, entry in by_question(histories).items():
        lines += [f"## [{q}] {' '.join(entry['question'].split())[:120]}", ""]
        if len(entry["question"]) > 120 or "\n" in entry["question"]:
            lines += ["> " + line for line in entry["question"].split("\n")] + [""]
        for i, m in entry["answers"]:
            facts = [provider_llm(m.get("model"))]
            if m.get("seconds") is not None:
                facts.append(f"{m['seconds']:.1f}s")
            if m.get("cost_usd") is not None:
                facts.append(f"${m['cost_usd']:.4f}")
            facts += [s for s, on in (("🏆 best", m.get("best")), ("👍", m.get("feedback") == 1), ("👎", m.get("feedback") == -1)) if on]
            lines += [f"### Chatbot {i + 1} · " + " · ".join(facts), "", m["content"].strip(), ""]
        if judgements and q in judgements:
            lines += [f"### Comparison · {provider_llm(judgements[q].get('judge'))}", "", judgements[q]["content"].strip(), ""]
    return "\n".join(lines)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _model(llm) -> str:
    """`provider:model` in the file -> `adapter:model`, as the page keeps it."""
    provider, _, name = str(llm or "").partition(":")
    return f"{PROVIDERS.get(provider, provider)}:{name}" if name else str(llm or "")


def parse_export(data: bytes) -> tuple[dict[int, list[dict]], dict[int, dict]]:
    """The chat in an export file: ({chatbot index: history} like the Chats page keeps it, {question number: judgement}).
    Raises ValueError if it is not an export."""
    try:
        doc = yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise ValueError(f"not valid YAML: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("questions"), list):
        raise ValueError("this does not look like a Multi-Chat export (no `questions` list)")
    histories: dict[int, list[dict]] = {}
    judgements: dict[int, dict] = {}
    for n, q in enumerate(doc["questions"], 1):
        if not isinstance(q, dict) or "question" not in q:
            continue
        number = _int(q.get("number"), n)
        question = str(q["question"])
        prompt = str(q.get("prompt") or question)
        for a in q.get("answers") or []:
            if not isinstance(a, dict):
                continue
            bot = max(0, _int(a.get("chatbot"), 1) - 1)
            user = {"role": "user", "content": prompt, "q": number}
            if prompt != question:
                user["shown"] = question
            reply = {"role": "assistant", "content": str(a.get("response") or ""), "model": _model(a.get("llm")),
                     "language": str(a["language"]) if a.get("language") else None, "feedback": _int(a.get("feedback"), 0) if a.get("feedback") in (1, -1, 0, "1", "-1", "0") else 0}
            if a.get("best"):
                reply["best"] = 1
            for f in ("seconds", "cost_usd", "temperature"):
                if _float(a.get(f)) is not None:
                    reply[f] = _float(a.get(f))
            for f in ("input_tokens", "output_tokens", "est_tokens", "ctx_tokens", "shared_from"):
                if a.get(f) is not None and _int(a.get(f), -1) >= 0:
                    reply[f] = _int(a.get(f), 0)
            if isinstance(a.get("instructions"), str) and a["instructions"]:
                reply["instructions"] = a["instructions"]
            if isinstance(a.get("links"), dict):
                reply["links"] = {str(k): str(v) for k, v in a["links"].items()}
            histories.setdefault(bot, []).extend([user, reply])
        c = q.get("comparison")
        if isinstance(c, dict) and c.get("content"):
            judgements[number] = {"judge": _model(c.get("judge")), "content": str(c["content"]), "seconds": _float(c.get("seconds"))}
    return histories, judgements
