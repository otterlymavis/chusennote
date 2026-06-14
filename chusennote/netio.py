"""HTTP fetching and HTML parsing for chusennote.

Depends on :mod:`chusennote.models` and :mod:`chusennote.util` only.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .models import Link, Page, TIMEOUT_SECONDS, USER_AGENT
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


def fetch_page(url: str) -> Page:
    return parse_page(url, request_html(url))
