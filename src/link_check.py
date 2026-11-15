"""Check the URLs in a model's reply: which are dead (404, 410, no such host), so the page can flag them.

Models cannot check links and sometimes invent them. After a reply arrives each URL in it is requested once (HEAD, then GET when a site
refuses HEAD), with a short timeout and a few at a time. Status per URL: "ok", "dead", or "unsure" (a site that blocks bots or is slow:
403, 429, 5xx, timeouts), which is shown as not checked rather than as broken. Addresses on this machine or its private network are never
requested: the host is checked before the request and again for every redirect, and the connection is made to the address that was checked
(so a second DNS answer cannot point it elsewhere).
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

URL = re.compile(r"https?://[^\s<>\"'`\]]+")
MAX_LINKS = 30                      # per reply: the rest are left unchecked
TIMEOUT = 6.0
AGENT = "Mozilla/5.0 (compatible; multi-chat-link-check)"
TRAILING = ".,;:!?*_"
CONTINUES = r"(?![\w/\-~%?#=&+@$]|[.,;:!*]+[\w/\-~%?#=&+@$])"   # not followed by more URL: a dead URL is not flagged inside a longer one


class PrivateAddress(OSError):
    """The URL (or a redirect) points at this machine or a private network."""


def _trim(url: str) -> str:
    """`url` without the punctuation that ends a sentence, and without a `)` that closes the text around it rather than the URL:
    `(see https://x.org/a)` ends at `a`, while `https://en.wikipedia.org/wiki/Graph_(abstract_data_type)` keeps its own brackets."""
    while url:
        if url[-1] in TRAILING:
            url = url[:-1]
        elif url[-1] == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        else:
            break
    return url


def find_urls(text: str) -> list[str]:
    """The distinct URLs in `text` in order of appearance, without trailing punctuation. In a Markdown link `[t](url)` the URL ends at its `)`."""
    urls = []
    for match in URL.finditer(text):
        url = match.group(0)
        if text[max(0, match.start() - 2):match.start()] == "](":       # a Markdown link target: it ends at the `)` that closes the link
            depth, end = 1, len(url)
            for k, c in enumerate(url):
                depth += (c == "(") - (c == ")")
                if depth == 0:
                    end = k
                    break
            url = url[:end]
        urls.append(_trim(url))
    return list(dict.fromkeys(u for u in urls if urlparse(u).hostname))


def _public_address(host: str) -> str | None:
    """A global IP address of `host` to connect to; None when there is no such host. Raises PrivateAddress for any non-global address."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return None
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses):
        raise PrivateAddress(host)
    return addresses[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        address = _public_address(self.host)
        if address is None:
            raise urllib.error.URLError(socket.gaierror(f"no such host: {self.host}"))
        self.sock = socket.create_connection((address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        address = _public_address(self.host)
        if address is None:
            raise urllib.error.URLError(socket.gaierror(f"no such host: {self.host}"))
        sock = socket.create_connection((address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]  # the certificate is still checked against the name


class _HTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _HTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme not in ("http", "https"):
            raise PrivateAddress(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)      # the new host is checked when it connects


_opener = urllib.request.build_opener(_HTTP, _HTTPS, _Redirects)


def check(url: str) -> str:
    host = urlparse(url).hostname or ""
    if not host:
        return "unsure"
    try:
        if _public_address(host) is None:
            return "dead"
    except PrivateAddress:
        return "unsure"
    for method in ("HEAD", "GET"):
        try:
            request = urllib.request.Request(url, method=method, headers={"User-Agent": AGENT, "Accept": "*/*"})
            with _opener.open(request, timeout=TIMEOUT) as r:
                r.read(0)
                return "ok"
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                if method == "GET":
                    return "dead"
                continue            # some servers answer HEAD with 404: ask again with GET
            if method == "HEAD":
                continue            # 403, 405, 5xx...: HEAD is often refused
            return "unsure"
        except PrivateAddress:
            return "unsure"         # a redirect to this machine or a private network: not followed
        except urllib.error.URLError as e:
            reason = e.reason.reason if isinstance(e.reason, urllib.error.URLError) else e.reason
            if isinstance(reason, PrivateAddress):
                return "unsure"
            return "dead" if isinstance(reason, (socket.gaierror, ConnectionRefusedError)) else "unsure"
        except Exception:
            return "unsure"
    return "unsure"


def check_links(text: str) -> dict[str, str]:
    """{url: "ok" | "dead" | "unsure"} for the URLs in `text` (empty when it has none)."""
    urls = find_urls(text)
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, urls[:MAX_LINKS]))
    return {**dict.fromkeys(urls, "unsure"), **dict(zip(urls, results))}


def flag_dead(text: str, links: dict[str, str] | None) -> str:
    """`text` with ❌ after every dead link: after a bare URL, or after the `)` that closes a Markdown link to it. A dead URL that is only
    the start of a longer URL is left alone."""
    for url in sorted((u for u, s in (links or {}).items() if s == "dead"), key=len, reverse=True):
        pattern = (r"\]\(" + re.escape(url) + r"(?:\s+\"[^\"]*\")?\)"                        # [text](url) or [text](url "title")
                   r"|(?<![(\[\w/])" + re.escape(url) + CONTINUES)                                    # a bare URL, not part of a longer one
        text = re.sub(pattern, lambda m: m.group(0) + " ❌", text)
    return text


def summary(links: dict[str, str] | None) -> str:
    if not links:
        return ""
    counts = {s: sum(v == s for v in links.values()) for s in ("ok", "dead", "unsure")}
    return f"🔗 {len(links)} link(s): {counts['ok']} ok" + (f" · {counts['dead']} ❌ dead" if counts["dead"] else "") + (f" · {counts['unsure']} not verified" if counts["unsure"] else "")
