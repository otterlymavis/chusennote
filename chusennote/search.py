"""Search backends and official-page ranking for chusennote.

Queries a managed search API when configured, otherwise scrapes DuckDuckGo/Bing
HTML, then scores results for how "official" they look. Depends only on the
:mod:`chusennote.models`, :mod:`chusennote.util`, and :mod:`chusennote.netio`
leaf modules.
"""

from __future__ import annotations

import base64
import html
import os
import re
import sys
import urllib.parse
from collections.abc import Sequence

from .models import (
    BING_SEARCH_URL,
    MIN_KEYWORD_OVERLAP,
    OFFICIAL_HINTS,
    OFFICIAL_HOST_HINTS,
    Page,
    SEARCH_API_KEY_ENV,
    SEARCH_PROVIDER_ENV,
    SEARCH_URL,
    SOCIAL_OR_NOISY_DOMAINS,
    SearchResult,
)
from .netio import parse_page, request_html, request_json
from .util import clean_text, hostname, is_ticket_url, is_web_url


def search_query(keyword: str) -> str:
    return f"{keyword} 公式 チケット 抽選 先行"


def warn_search_backend(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def search_api(keyword: str, limit: int = 8) -> list[SearchResult]:
    """Query a managed search API when configured via env vars.

    Returns ``[]`` when no provider/key is set or the call fails, so callers can
    fall back to HTML scraping. Supported providers: brave, bing, serpapi.
    """
    provider = os.environ.get(SEARCH_PROVIDER_ENV, "").strip().lower()
    api_key = os.environ.get(SEARCH_API_KEY_ENV, "").strip()
    if not provider or not api_key:
        return []
    query = search_query(keyword)
    try:
        if provider == "brave":
            url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
                {"q": query, "count": limit, "search_lang": "jp", "country": "jp"}
            )
            data = request_json(url, {"X-Subscription-Token": api_key})
            rows = (data.get("web") or {}).get("results", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "title", "url", "description", limit)
        if provider == "bing":
            url = "https://api.bing.microsoft.com/v7.0/search?" + urllib.parse.urlencode(
                {"q": query, "count": limit, "mkt": "ja-JP"}
            )
            data = request_json(url, {"Ocp-Apim-Subscription-Key": api_key})
            rows = (data.get("webPages") or {}).get("value", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "name", "url", "snippet", limit)
        if provider == "serpapi":
            url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(
                {"engine": "google", "q": query, "num": limit, "hl": "ja", "gl": "jp", "api_key": api_key}
            )
            data = request_json(url)
            rows = data.get("organic_results", []) if isinstance(data, dict) else []
            return parse_api_results(rows, "title", "link", "snippet", limit)
    except (OSError, ValueError, TypeError) as exc:
        warn_search_backend(
            f"{SEARCH_PROVIDER_ENV}={provider!r} failed ({type(exc).__name__}); falling back to HTML search."
        )
        return []
    warn_search_backend(f"unsupported {SEARCH_PROVIDER_ENV}={provider!r}; falling back to HTML search.")
    return []


def parse_api_results(
    rows: object, title_key: str, url_key: str, snippet_key: str, limit: int
) -> list[SearchResult]:
    results: list[SearchResult] = []
    if not isinstance(rows, list):
        return results
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get(url_key, "")
        if not is_web_url(url):
            continue
        results.append(
            SearchResult(
                title=clean_text(str(row.get(title_key, ""))),
                url=url,
                snippet=clean_text(str(row.get(snippet_key, ""))),
            )
        )
        if len(results) >= limit:
            break
    return results


def search_web(keyword: str, limit: int = 8) -> list[SearchResult]:
    api_results = search_api(keyword, limit=limit)
    if api_results:
        return api_results
    query = search_query(keyword)
    html = request_html(SEARCH_URL, {"q": query})
    page = parse_page(SEARCH_URL, html)
    results: list[SearchResult] = []
    for link in page.links:
        if "duckduckgo.com/l/" not in link.url and "uddg=" not in link.url:
            continue
        parsed = urllib.parse.urlparse(link.url)
        href = urllib.parse.parse_qs(parsed.query).get("uddg", [link.url])[0]
        if not href.startswith("http"):
            continue
        results.append(SearchResult(title=link.label, url=href, snippet=""))
        if len(results) >= limit:
            break
    return results or search_bing(keyword, limit=limit)


def decode_bing_url(url: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    encoded = query.get("u", [""])[0]
    if encoded.startswith("a1"):
        payload = encoded[2:]
        padding = "=" * (-len(payload) % 4)
        try:
            return base64.urlsafe_b64decode(f"{payload}{padding}").decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            return url
    return url


def strip_tags(value: str) -> str:
    return clean_text(re.sub(r"<[^>]+>", " ", html.unescape(value)))


def search_bing(keyword: str, limit: int = 8) -> list[SearchResult]:
    query = search_query(keyword)
    html_text = request_html(BING_SEARCH_URL, {"q": query})
    results: list[SearchResult] = []
    seen: set[str] = set()
    for block in re.findall(r'<li class="b_algo".*?</li>', html_text, flags=re.DOTALL):
        anchor = re.search(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, flags=re.DOTALL)
        if not anchor:
            continue
        url = decode_bing_url(anchor.group(1))
        title = strip_tags(anchor.group(2))
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, flags=re.DOTALL)
        snippet = strip_tags(snippet_match.group(1)) if snippet_match else ""
        if not title or not is_web_url(url):
            continue
        if urllib.parse.urlparse(url).netloc.endswith("bing.com"):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append(SearchResult(title=title, url=url, snippet=snippet))
        if len(results) >= limit:
            break
    return results


def text_bigrams(value: str) -> set[str]:
    """Whitespace/punctuation-insensitive character bigrams for CJK-aware matching."""
    chars = re.sub(r"[\s　〜～・！？、。「」『』【】（）()\"'’”|/\\-]+", "", value.lower())
    return {chars[index : index + 2] for index in range(len(chars) - 1)}


def keyword_overlap(keyword: str, text: str) -> float:
    """Fraction of the keyword's character bigrams that appear in ``text`` (0..1).

    Japanese keywords have no spaces, so the old token split produced a single
    token that never matched a result title. Character bigrams let a result like
    ``ディア・エヴァン・ハンセン 公式`` score highly while unrelated pages stay near 0.
    """
    keyword_grams = text_bigrams(keyword)
    if not keyword_grams:
        return 0.0
    return len(keyword_grams & text_bigrams(text)) / len(keyword_grams)


def keyword_matches_text(keyword: str, text: str) -> bool:
    normalized_keyword = clean_text(keyword).lower()
    haystack = clean_text(text).lower()
    if not normalized_keyword:
        return False
    if normalized_keyword in haystack:
        return True
    tokens = [token for token in re.split(r"\s+", normalized_keyword) if len(token) >= 3]
    if tokens and any(token in haystack for token in tokens):
        return True
    if re.fullmatch(r"[a-z0-9 ._'-]+", normalized_keyword):
        return False
    return keyword_overlap(keyword, text) >= MIN_KEYWORD_OVERLAP


def official_score(result: SearchResult, keyword: str) -> float:
    host = hostname(result.url)
    text = f"{result.title} {result.snippet} {result.url}".lower()
    title_snippet = f"{result.title} {result.snippet}"
    overlap = keyword_overlap(keyword, title_snippet)
    token_score = sum(2 for token in keyword.lower().split() if len(token) >= 3 and token in text)
    trusted_host = any(hint in host for hint in OFFICIAL_HOST_HINTS)
    has_keyword_relevance = keyword_matches_text(keyword, title_snippet) or token_score > 0
    if not trusted_host and not has_keyword_relevance:
        return -20.0 if any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS) else 0.0

    score = 0.0
    if any(noisy in host for noisy in SOCIAL_OR_NOISY_DOMAINS):
        score -= 20
    if is_ticket_url(result.url):
        score -= 5
    if any(hint.lower() in text for hint in OFFICIAL_HINTS):
        score += 10
    # CJK-aware relevance: reward results whose title/snippet share characters
    # with the keyword. Worth up to +30 so a strong title match dominates noise.
    score += 30 * overlap
    # Latin keywords still benefit from token matching.
    score += token_score
    if trusted_host:
        score += 6
    if host.endswith((".co.jp", ".or.jp")):
        score += 4
    if "news" in result.url or "live" in result.url or "stage" in result.url:
        score += 3
    return score


def choose_official_results(results: Sequence[SearchResult], keyword: str, limit: int = 3) -> list[SearchResult]:
    scored = [(official_score(result, keyword), result) for result in results]
    return [result for score, result in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:limit]


def page_matches_keyword(keyword: str, page: Page) -> bool:
    title_and_intro = f"{page.title} {page.text[:1200]}"
    return keyword_matches_text(keyword, title_and_intro)
