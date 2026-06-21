"""HTTP fetching and HTML parsing for chusennote.

Depends on :mod:`chusennote.models` and :mod:`chusennote.util` only.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .models import (
    BROWSER_FETCH_ENV,
    BROWSER_MIN_TEXT_LENGTH,
    BROWSER_SETTLE_MS,
    BROWSER_TIMEOUT_MS,
    BROWSER_USER_AGENT,
    EMPTY_STATE_MARKERS,
    Link,
    Page,
    TIMEOUT_SECONDS,
    USER_AGENT,
)
from .util import absolute_url, clean_text


class ExtractedHTML:
    def __init__(self) -> None:
        self.title = ""
        self.og_title = ""
        self.text_parts: list[str] = []
        self.links: list[Link] = []
        self._tag_stack: list[str] = []
        self._current_href: str | None = None
        self._current_label: list[str] = []
        self._title_parts: list[str] = []


class EventHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.data = ExtractedHTML()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self.data._tag_stack.append(tag)
        if tag == "meta" and attr_map.get("property", "").lower() == "og:title":
            self.data.og_title = clean_text(attr_map.get("content", ""))
        if tag == "a" and attr_map.get("href"):
            self.data._current_href = absolute_url(self.base_url, attr_map["href"])
            self.data._current_label = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.data._title_parts:
            self.data.title = clean_text(" ".join(self.data._title_parts))
            self.data._title_parts = []
        if tag == "a" and self.data._current_href:
            href = self.data._current_href
            label = clean_text(" ".join(self.data._current_label)) or href
            if not href.startswith(("mailto:", "tel:", "javascript:")):
                self.data.links.append(Link(label=label[:120], url=href))
            self.data._current_href = None
            self.data._current_label = []
        if self.data._tag_stack:
            self.data._tag_stack.pop()

    def handle_data(self, value: str) -> None:
        current = self.data._tag_stack[-1] if self.data._tag_stack else ""
        if current in {"script", "style", "noscript"}:
            return
        if current == "title":
            self.data._title_parts.append(value)
        if self.data._current_href:
            self.data._current_label.append(value)
        self.data.text_parts.append(value)


def request_html(url: str, params: dict[str, str] | None = None) -> str:
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read()
        content_type = response.headers.get_content_charset() or "utf-8"
    return body.decode(content_type, errors="replace")


def request_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset, errors="replace"))


def parse_page(url: str, html: str) -> Page:
    parser = EventHTMLParser(url)
    parser.feed(html)
    extracted = parser.data
    seen: set[str] = set()
    links: list[Link] = []
    for link in extracted.links:
        if link.url in seen:
            continue
        seen.add(link.url)
        links.append(link)
    return Page(
        url=url,
        title=extracted.og_title or extracted.title,
        text=clean_text(" ".join(extracted.text_parts)),
        links=tuple(links),
    )


def browser_fetch_mode() -> str:
    """Read the headless-browser fetch mode from the environment.

    Browser rendering stays opt-in: it spawns Chromium per page, so enabling it
    by default would add a slow render to every dead or SPA link on every watch
    run. Set ``CHUSENNOTE_BROWSER_FETCH=fallback`` to render JS-built ticket
    sites (e.g. shiki.jp) that the plain fetch reads as an empty shell, or
    ``always`` to render every page.
    """
    value = os.environ.get(BROWSER_FETCH_ENV, "").strip().lower()
    if value in {"always", "force"}:
        return "always"
    if value in {"1", "true", "on", "auto", "fallback"}:
        return "fallback"
    return "off"


def page_needs_browser(page: Page) -> bool:
    """Whether a plainly-fetched page is likely a JS shell worth re-rendering.

    Two shapes both mean "real content was not in the static HTML": the page is
    too thin to hold a schedule, or it carries an empty-state placeholder that a
    client-side app replaces once it renders.
    """
    text = page.text
    if len(text) < BROWSER_MIN_TEXT_LENGTH:
        return True
    compact = text.replace(" ", "").replace("　", "").lower()
    return any(marker.replace(" ", "") in compact for marker in EMPTY_STATE_MARKERS)


def _browser_render(url: str) -> Page:
    """One headless-Chromium render pass. Raises ImportError if playwright is
    absent and playwright's own errors on any browser failure."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=BROWSER_USER_AGENT, locale="ja-JP")
            page = context.new_page()
            # "networkidle" never settles on ad/analytics-heavy JP sites;
            # wait for the DOM then give client-side JS a moment to render.
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            page.wait_for_timeout(BROWSER_SETTLE_MS)
            html = page.content()
        finally:
            browser.close()
    return parse_page(url, html)


def fetch_page_browser(url: str, attempts: int = 2) -> Page:
    """Render a page with headless Chromium so JS-built ticket platforms parse.

    Playwright is an optional dependency; a missing install or any browser
    failure is surfaced as OSError so callers' existing fetch-error handling
    treats it like any other unreachable page. A cold Chromium launch can fail
    transiently, which would otherwise leave ``fetch_page`` silently serving the
    JS shell, so a render is retried once before giving up.
    """
    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            return _browser_render(url)
        except ImportError as error:
            # A missing install never fixes itself on retry.
            raise OSError(
                "headless-browser fetch requires playwright "
                "(pip install playwright && playwright install chromium)"
            ) from error
        except Exception as error:  # playwright raises its own error hierarchy
            last_error = error
    raise OSError(f"headless-browser fetch failed for {url}: {last_error}") from last_error


def fetch_page(url: str) -> Page:
    mode = browser_fetch_mode()
    if mode == "always":
        return fetch_page_browser(url)
    try:
        page = parse_page(url, request_html(url))
    except (OSError, ValueError):
        if mode == "fallback":
            return fetch_page_browser(url)
        raise
    if mode == "fallback" and page_needs_browser(page):
        try:
            return fetch_page_browser(url)
        except (OSError, ValueError):
            return page
    return page
