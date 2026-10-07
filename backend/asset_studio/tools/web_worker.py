"""Fetch a page that does not want to be fetched, and search without an API key.

RUNS INSIDE data/tools/scrapling-venv, NOT the backend venv. Scrapling brings its own browsers
and a large dependency tree; keeping it in its own venv is the same arrangement graphify and the
headless browser already use, so a bad release there can never break the backend.

Reads one JSON object on stdin, writes one JSON object on stdout. Nothing else is printed —
Scrapling logs to stderr, which the caller discards.

WHY THE LADDER. Measured here against real sites, on the day this was written:

    plain urllib          g2.com 403      crunchbase.com 403
    Fetcher (no browser)  g2.com 403      crunchbase.com 403
    StealthyFetcher       g2.com 403      crunchbase.com 403   ← "Just a moment…", the challenge
    + solve_cloudflare    demo   200      crunchbase.com 200   ← 15,676 characters of real page

So the cheap tier is not a formality and the expensive tier is not optional: most pages come back
from a plain request in under a second, and the ones that matter need a browser that will sit and
solve the interstitial. Escalating means the common case stays cheap and the blocked case still
works, without the caller having to know which is which.
"""
from __future__ import annotations

import json
import sys

# STDOUT IS UTF-8, ALWAYS. A subprocess on Windows gets cp1252 by default, and the web is not
# cp1252: one emoji in a page title — a U+FE0F variation selector was the real case — raised
# UnicodeEncodeError inside `print` and the whole answer was lost with it. Every payload is also
# written with ensure_ascii below, so the bytes on the wire are plain ASCII whatever happens here.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# A page that "succeeded" with a challenge in it has not succeeded. These are the words the
# interstitials actually use — checked against the crunchbase and nopecha responses.
_WALL = (
    "just a moment", "verify your session", "checking your browser",
    "enable javascript and cookies", "attention required", "ddos protection by",
    "verifying you are human", "access denied", "request unsuccessful",
)
_MAX_TEXT = 200_000          # a hard ceiling; the caller trims to what it wants


def _clean(page) -> str:
    """Readable text, without the script and style noise that dominates a raw dump."""
    try:
        return " ".join(page.get_all_text(ignore_tags=("script", "style", "noscript")).split())
    except Exception:
        try:
            return " ".join((page.body or "").split())
        except Exception:
            return ""


def _looks_blocked(status: int, text: str) -> bool:
    if status in (403, 429, 503):
        return True
    low = text[:2000].lower()
    return any(w in low for w in _WALL)


def _title(page) -> str:
    for sel in ("title", "h1", "meta[property='og:title']"):
        try:
            els = page.css(sel)
            if not els:
                continue
            t = _text_of(els[0]) or " ".join((els[0].attrib.get("content") or "").split())
            if t:
                return t[:300]
        except Exception:
            continue
    return ""


def _try_http(url: str, timeout: int) -> dict:
    from scrapling.fetchers import Fetcher
    p = Fetcher.get(url, timeout=timeout, stealthy_headers=True)
    text = _clean(p)
    return {"tier": "http", "status": int(p.status), "text": text, "title": _title(p),
            "blocked": _looks_blocked(int(p.status), text)}


def _try_stealth(url: str, timeout: int, solve: bool = True) -> dict:
    from scrapling.fetchers import StealthySession
    # solve_cloudflare is what turns a 403 interstitial into the real page. It costs seconds,
    # which is why it is only reached after the cheap tier has been tried.
    with StealthySession(headless=True, solve_cloudflare=solve,
                         disable_resources=False) as s:
        p = s.fetch(url)
        text = _clean(p)
        return {"tier": "stealth", "status": int(p.status), "text": text, "title": _title(p),
                "blocked": _looks_blocked(int(p.status), text)}


def fetch(url: str, mode: str = "auto", timeout: int = 30) -> dict:
    tried: list = []
    if mode in ("auto", "http"):
        try:
            r = _try_http(url, timeout)
            tried.append({"tier": "http", "status": r["status"], "blocked": r["blocked"]})
            if mode == "http" or not r["blocked"]:
                r["tried"] = tried
                return r
        except Exception as e:
            tried.append({"tier": "http", "error": "%s: %s" % (type(e).__name__, str(e)[:120])})
            if mode == "http":
                return {"ok": False, "error": tried[-1]["error"], "tried": tried}
    try:
        r = _try_stealth(url, timeout)
        tried.append({"tier": "stealth", "status": r["status"], "blocked": r["blocked"]})
        r["tried"] = tried
        return r
    except Exception as e:
        tried.append({"tier": "stealth", "error": "%s: %s" % (type(e).__name__, str(e)[:120])})
        return {"ok": False, "error": tried[-1]["error"], "tried": tried}


def _text_of(el) -> str:
    """Readable text of one element, whether the text sits on it or in its children."""
    if el is None:
        return ""
    for attr in ("get_all_text", "text"):
        try:
            v = getattr(el, attr)
            v = v() if callable(v) else v
            v = " ".join(str(v or "").split())
            if v:
                return v
        except Exception:
            continue
    return ""


def _unwrap(href: str) -> str:
    """DuckDuckGo hands back a redirect: //duckduckgo.com/l/?uddg=<the real url>&rut=…"""
    from urllib.parse import urlparse, parse_qs, unquote
    if "uddg=" not in (href or ""):
        return href or ""
    try:
        return unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
    except Exception:
        return href


# Engine, result-link selector, snippet selector. Measured on the day this was written:
#   lite.duckduckgo.com  200, 10 results, 24 KB   ← small, structured, cheap to parse
#   bing.com             200, 10 results, 802 KB  ← works, but thirty times the page
#   html.duckduckgo.com  202, a CAPTCHA asking to "select all squares containing a duck"
#   mojeek.com           403, "your network appears to be sending automated queries"
# The lite endpoint is first because it is the cheapest that works, not because it is favoured.
_ENGINES = (
    ("https://lite.duckduckgo.com/lite/?q=", "a.result-link", "td.result-snippet"),
    ("https://www.bing.com/search?q=", "li.b_algo h2 a", "li.b_algo p"),
)


def search(query: str, n: int = 8, timeout: int = 30) -> dict:
    """Real search results, no API key and no account.

    Every engine that returns a plain result list refuses a naive scraper — that is exactly the
    wall this module exists to get over, so search comes free with the fetch.
    """
    from urllib.parse import quote_plus
    want = max(1, min(int(n), 25))
    errors: list = []
    for base, link_sel, snip_sel in _ENGINES:
        url = base + quote_plus(query)
        try:
            page = _try_stealth_page(url, timeout)
        except Exception as e:
            errors.append("%s: %s" % (base.split("/")[2], str(e)[:80]))
            continue
        try:
            links = page.css(link_sel)
            snips = page.css(snip_sel)
        except Exception:
            links, snips = [], []
        out: list = []
        for i, a in enumerate(links[:want]):
            href = _unwrap(a.attrib.get("href") or "")
            if not href.startswith("http"):
                continue
            out.append({"title": _text_of(a)[:200], "url": href,
                        "snippet": (_text_of(snips[i]) if i < len(snips) else "")[:400]})
        if out:
            return {"ok": True, "query": query, "engine": base.split("/")[2], "results": out}
        errors.append("%s: no results parsed (status %s)" % (base.split("/")[2], getattr(page, "status", "?")))
    return {"ok": False, "error": "; ".join(errors)[:300] or "no engine answered", "results": []}


def _try_stealth_page(url: str, timeout: int):
    from scrapling.fetchers import StealthySession
    with StealthySession(headless=True, solve_cloudflare=True) as s:
        return s.fetch(url)


def _try_http_page(url: str, timeout: int):
    from scrapling.fetchers import Fetcher
    return Fetcher.get(url, timeout=timeout, stealthy_headers=True)


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": "bad request: %s" % e}))
        return 0
    try:
        if req.get("op") == "search":
            res = search(str(req.get("query") or ""), int(req.get("n") or 8),
                         int(req.get("timeout") or 30))
        else:
            res = fetch(str(req.get("url") or ""), str(req.get("mode") or "auto"),
                        int(req.get("timeout") or 30))
        if "text" in res and len(res["text"]) > _MAX_TEXT:
            res["text"] = res["text"][:_MAX_TEXT]
            res["truncated"] = True
        res.setdefault("ok", not res.get("error"))
        print(json.dumps(res, ensure_ascii=True))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
