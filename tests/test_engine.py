import chat_engine as ce


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.opts = None

    def generate(self, prompt, model=None, **opts):
        self.opts = opts
        return "ok"


def test_temperature_is_passed_only_when_set():
    inner = FakeLLM()
    ce._Tuned(inner, 0.3).generate("p", temperature=None)
    assert inner.opts["temperature"] == 0.3
    ce._Tuned(inner, None).generate("p", temperature=None)
    assert inner.opts["temperature"] is None
    assert ce._Tuned(inner, 0.3).name == "fake"


def test_cost():
    assert ce._cost(0.5, 10, 10, (1.0, 2.0)) == 0.5
    assert ce._cost(None, 1_000_000, 500_000, (1.0, 2.0)) == 2.0
    assert ce._cost(None, 10, 10, None) is None


def test_ask_with_mock_trims_and_instructs():
    history = []
    for k in range(1, 4):
        history += [{"role": "user", "content": f"question {k}"}, {"role": "assistant", "content": f"answer {k}"}]
    answer = ce.ask("newest", history, ce.Turn("mock:m", last_n=1, instructions="Be brief.", language="French"))
    assert answer.reply.startswith("[Mock LLM response from m")
    assert "Be brief." in answer.reply                 # the mock echoes the start of the prompt
    assert answer.seconds > 0 and answer.cost_usd is None


def test_kernel_pool_is_reused():
    ce.ask("one", [], ce.Turn("mock:m"))
    pool = ce._idle.get_nowait()
    ce._idle.put(pool)
    ce.ask("two", [], ce.Turn("mock:m"))
    assert ce._idle.get_nowait() is pool
    ce._idle.put(pool)


def test_compare_with_mock():
    answer = ce.compare("q?", [("Chatbot 1", "yes"), ("Chatbot 2", "no")], ce.Turn("mock:m"))
    assert answer.reply.startswith("[Mock LLM response")


def test_image_messages():
    history = [{"role": "user", "content": "a", "q": 1}, {"role": "assistant", "content": "b", "model": "x"}]
    openai = ce.image_messages("what is this?", history, 5, [("image/png", b"\x89PNG")], "openai")
    assert openai[:2] == [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    assert openai[2]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    anthropic = ce.image_messages("what is this?", [], 5, [("image/png", b"\x89PNG")], "anthropic")
    assert anthropic[0]["content"][0]["type"] == "image" and anthropic[0]["content"][1] == {"type": "text", "text": "what is this?"}


def test_rules_come_from_the_graph():
    assert "never invent a URL" in ce.graph_input_default("rules")
    assert "never invent a URL" in ce.system_prompt(ce.Turn("ollama:x", instructions="Be kind."))


def test_estimate_counts_the_prompt_template():
    turn = ce.Turn("ollama:x", last_n=1)
    assert ce.estimate_tokens("", [], turn) > 100
