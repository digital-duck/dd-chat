import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import link_check
from link_check import PrivateAddress, check, find_urls, flag_dead


def test_urls_keep_their_own_brackets():
    text = "See [Graph](https://en.wikipedia.org/wiki/Graph_(abstract_data_type)) and https://en.wikipedia.org/wiki/Tree_(graph_theory)."
    assert find_urls(text) == ["https://en.wikipedia.org/wiki/Graph_(abstract_data_type)", "https://en.wikipedia.org/wiki/Tree_(graph_theory)"]


def test_urls_drop_surrounding_punctuation():
    assert find_urls("(see https://x.org/a). Or **https://x.org/b**, then https://x.org/c?q=1!") == [
        "https://x.org/a", "https://x.org/b", "https://x.org/c?q=1"]


def test_markdown_link_ends_at_its_bracket():
    assert find_urls("[a](https://x.org/a)[b](https://x.org/b)") == ["https://x.org/a", "https://x.org/b"]


def test_flag_after_markdown_link_not_inside_it():
    url = "https://en.wikipedia.org/wiki/Graph_(abstract_data_type)"
    assert flag_dead(f"[Graph]({url}) is good", {url: "dead"}) == f"[Graph]({url}) ❌ is good"


def test_flag_skips_longer_urls():
    text = "a https://x.com/a.html and https://x.com/a/b and https://x.com/a. End"
    assert flag_dead(text, {"https://x.com/a": "dead"}) == "a https://x.com/a.html and https://x.com/a/b and https://x.com/a ❌. End"


def test_flag_both_when_both_dead():
    text = "https://x.com/a and https://x.com/a.html"
    assert flag_dead(text, {"https://x.com/a": "dead", "https://x.com/a.html": "dead"}) == "https://x.com/a ❌ and https://x.com/a.html ❌"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.1.2.3", "192.168.0.1", "169.254.169.254", "::1"])
def test_private_hosts_are_refused(host):
    with pytest.raises(PrivateAddress):
        link_check._public_address(host)


def test_private_urls_are_not_requested():
    assert check("http://127.0.0.1:1/") == "unsure"
    assert check("http://[::1]:1/") == "unsure"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/ok":
            self.send_response(200)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://private.test/secret")
        else:
            self.send_response(404)
        self.end_headers()

    do_HEAD = do_GET


@pytest.fixture
def server(monkeypatch):
    """A local server that the checker is told is public (`public.test`); every other host counts as private."""
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def fake(host):
        if host == "public.test":
            return "127.0.0.1"
        raise PrivateAddress(host)
    monkeypatch.setattr(link_check, "_public_address", fake)
    yield f"http://public.test:{httpd.server_port}"
    httpd.shutdown()


def test_ok_and_dead(server):
    assert check(server + "/ok") == "ok"
    assert check(server + "/missing") == "dead"


def test_redirect_to_private_host_is_not_followed(server):
    assert check(server + "/redirect") == "unsure"
