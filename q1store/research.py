"""Web research helpers shared by the Hermes agent and the course-discovery scheduler.

Everything here uses only the standard library: DuckDuckGo's HTML endpoint for general and
YouTube search, the arXiv Atom API for papers, and a bounded HTML-to-text fetcher. Network
failures surface as empty results plus an ``error`` field; nothing is fabricated.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Q1Store-Tutorial/1.0"
MAX_FETCH_BYTES = 1_500_000
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n{3,}")


def _get(url, timeout=12, data=None, headers=None):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(MAX_FETCH_BYTES).decode(charset, "replace"), response.headers.get("Content-Type", "")


def html_to_text(markup):
    markup = SCRIPT_RE.sub(" ", markup)
    markup = re.sub(r"<(br|/p|/div|/li|/h[1-6]|/tr)[^>]*>", "\n", markup, flags=re.I)
    text = html.unescape(TAG_RE.sub(" ", markup))
    text = WS_RE.sub(" ", text)
    return NL_RE.sub("\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


def classify(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "arxiv.org" in host:
        return "arxiv"
    if "github.com" in host:
        return "github"
    if "isaac-sim.github.io" in host or "nvidia.com" in host:
        return "docs"
    return "web"


def web_search(query, max_results=8, timeout=12):
    """DuckDuckGo HTML results: [{title, url, snippet, type}]."""
    try:
        body, _ = _get("https://html.duckduckgo.com/html/", timeout=timeout,
                       data=urllib.parse.urlencode({"q": query}).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"query": query, "results": [], "error": f"search failed: {exc}"}
    results = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<div class="result\b|<div class="nav-link|$)', body, re.S):
        href, title_html, rest = match.groups()
        url = href
        if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            url = qs.get("uddg", [href])[0]
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', rest, re.S)
        snippet = html_to_text(next((g for g in (snippet_match.groups() if snippet_match else ()) if g), ""))
        title = html_to_text(title_html)
        if not title or not url.startswith("http"):
            continue
        results.append({"title": title, "url": url, "snippet": snippet[:400], "type": classify(url)})
        if len(results) >= max_results:
            break
    return {"query": query, "results": results}


def youtube_search(query, max_results=6, timeout=12):
    out = web_search(f"site:youtube.com {query}", max_results=max_results * 2, timeout=timeout)
    out["results"] = [r for r in out["results"] if r["type"] == "youtube"][:max_results]
    return out


def arxiv_search(query, max_results=6, timeout=15):
    """arXiv Atom API: [{title, url, snippet, authors, published, type: arxiv}]."""
    params = urllib.parse.urlencode({"search_query": f"all:{query}", "start": 0, "max_results": max_results,
                                     "sortBy": "submittedDate", "sortOrder": "descending"})
    try:
        body, _ = _get("http://export.arxiv.org/api/query?" + params, timeout=timeout)
        root = ET.fromstring(body)
    except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
        return {"query": query, "results": [], "error": f"arxiv failed: {exc}"}
    ns = {"a": "http://www.w3.org/2005/Atom"}
    results = []
    for entry in root.findall("a:entry", ns):
        title = WS_RE.sub(" ", (entry.findtext("a:title", "", ns) or "").replace("\n", " ")).strip()
        link = entry.findtext("a:id", "", ns) or ""
        summary = WS_RE.sub(" ", (entry.findtext("a:summary", "", ns) or "").replace("\n", " ")).strip()
        authors = [a.findtext("a:name", "", ns) for a in entry.findall("a:author", ns)]
        results.append({"title": title, "url": link, "snippet": summary[:500], "authors": authors[:6],
                        "published": entry.findtext("a:published", "", ns), "type": "arxiv"})
    return {"query": query, "results": results}


def fetch_url(url, max_chars=12000, timeout=15):
    """Fetch a page and return readable text (bounded). PDFs are reported, not parsed."""
    if not re.match(r"https?://", url or ""):
        return {"url": url, "error": "only http(s) URLs are allowed"}
    try:
        body, content_type = _get(url, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"url": url, "error": f"fetch failed: {exc}"}
    if "pdf" in content_type.lower():
        return {"url": url, "content_type": content_type, "text": "", "note": "PDF content; use the arXiv abstract page instead."}
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    text = html_to_text(body) if "html" in content_type.lower() or "<html" in body[:2000].lower() else body
    return {"url": url, "title": html_to_text(title_match.group(1)) if title_match else "", "content_type": content_type,
            "text": text[:max_chars], "truncated": len(text) > max_chars}
