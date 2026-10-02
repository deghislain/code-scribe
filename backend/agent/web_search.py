"""Lightweight web search via DuckDuckGo HTML — no API key required."""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

import httpx

from backend.logger import get_logger

log = get_logger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 10.0
_MAX_SNIPPET_CHARS = 600
_MAX_PAGE_CHARS = 3000


def _strip_tags(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    return re.sub(r"\s+", " ", text).strip()


def search(query: str, max_results: int = 4) -> list[dict]:
    """
    Search DuckDuckGo for *query* and return up to *max_results* hits.

    Each result is a dict with keys:
        title   – page title
        url     – canonical URL
        snippet – short description from DuckDuckGo
    """
    try:
        resp = httpx.post(
            _DDG_URL,
            data={"q": query, "b": "", "kl": "us-en"},
            headers=_HEADERS,
            follow_redirects=True,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        log.warning("DuckDuckGo search failed: %s", exc)
        return []

    html = resp.text
    results: list[dict] = []

    # Each result block: <div class="result ...">
    for block in re.findall(r'<div class="result[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.DOTALL):
        # Title + URL
        title_match = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_match:
            continue
        url = title_match.group(1)
        # DuckDuckGo wraps URLs in a redirect — extract the real URL from uddg= param
        uddg = re.search(r"uddg=([^&\"]+)", url)
        if uddg:
            from urllib.parse import unquote
            url = unquote(uddg.group(1))
        title = _strip_tags(title_match.group(2))

        # Snippet
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        snippet = snippet[:_MAX_SNIPPET_CHARS]

        if title and url.startswith("http"):
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    log.debug("DuckDuckGo: %d results for %r", len(results), query)
    return results


def fetch_page_text(url: str, max_chars: int = _MAX_PAGE_CHARS) -> str:
    """
    Fetch *url* and return stripped plain text up to *max_chars*.
    Returns an empty string on any error.
    """
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=_TIMEOUT)
        resp.raise_for_status()
        return _strip_tags(resp.text)[:max_chars]
    except Exception as exc:
        log.debug("Could not fetch %s: %s", url, exc)
        return ""


def search_and_fetch(query: str, max_results: int = 3) -> list[dict]:
    """
    Run a DuckDuckGo search and enrich each result with a page text snippet.

    Returns a list of dicts with keys: title, url, snippet, page_text.
    """
    results = search(query, max_results=max_results)
    for r in results:
        r["page_text"] = fetch_page_text(r["url"])
    return results
