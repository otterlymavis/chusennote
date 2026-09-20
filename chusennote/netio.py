"""HTTP fetching and HTML parsing for chusennote.

Depends on :mod:`chusennote.models` and :mod:`chusennote.util` only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from .models import (
    BROWSER_FETCH_ENV,
    BROWSER_MIN_TEXT_LENGTH,
    BROWSER_SETTLE_MS,
    BROWSER_TIMEOUT_MS,
    BROWSER_USER_AGENT,
    EMPTY_STATE_MARKERS,
    Link,
    MAX_FETCH_RESPONSE_BYTES,
    Page,
    TIMEOUT_SECONDS,
    USER_AGENT,
)
from .util import absolute_url, clean_text, is_public_fetch_url


class ExtractedHTML:
    def __init__(self) -> None:
        self.title = ""
        self.og_title = ""
        self.text_parts: list[str] = []
        self.links: list[Link] = []
        self.discovery_links: list[Link] = []
        self.structured_data: list[object] = []
        self._tag_stack: list[str] = []
        self._current_href: str | None = None
        self._current_label: list[str] = []
        self._title_parts: list[str] = []
        self._json_ld_parts: list[str] | None = None


class EventHTMLParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.data = ExtractedHTML()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self.data._tag_stack.append(tag)
        if tag == "link" and attr_map.get("href"):
            rel = {value.lower() for value in attr_map.get("rel", "").split()}
            content_type = attr_map.get("type", "").split(";", 1)[0].strip().lower()
            if "sitemap" in rel or ("alternate" in rel and content_type in {"application/rss+xml", "application/atom+xml"}):
                self.data.discovery_links.append(
                    Link(label=content_type or "sitemap", url=absolute_url(self.base_url, attr_map["href"]))
                )
        if tag == "img":
            alt = clean_text(attr_map.get("alt", ""))
            if alt:
                self.data.text_parts.append(alt)
                if self.data._current_href:
                    self.data._current_label.append(alt)
        if tag == "script" and attr_map.get("type", "").split(";", 1)[0].strip().lower() == "application/ld+json":
            self.data._json_ld_parts = []
        if tag == "meta" and attr_map.get("property", "").lower() == "og:title":
            self.data.og_title = clean_text(attr_map.get("content", ""))
        if tag == "a" and attr_map.get("href"):
            self.data._current_href = absolute_url(self.base_url, attr_map["href"])
            self.data._current_label = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.data._json_ld_parts is not None:
            raw = "".join(self.data._json_ld_parts).strip()
            if raw and len(raw) <= 262_144 and len(self.data.structured_data) < 20:
                try:
                    self.data.structured_data.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            self.data._json_ld_parts = None
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
        if self.data._json_ld_parts is not None:
            self.data._json_ld_parts.append(value)
            return
        current = self.data._tag_stack[-1] if self.data._tag_stack else ""
        if current in {"script", "style", "noscript"}:
            return
        if current == "title":
            self.data._title_parts.append(value)
        if self.data._current_href:
            self.data._current_label.append(value)
        self.data.text_parts.append(value)


class _PublicFetchRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only while every visible hop remains a public web URL."""

    _credential_headers = {
        "authorization",
        "ocp-apim-subscription-key",
        "x-subscription-token",
    }

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme.lower() == "https" else 80 if parsed.scheme.lower() == "http" else None
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        if not is_public_fetch_url(new_url):
            raise urllib.error.URLError("redirect target is not a public HTTP(S) URL")
        credentialed = any(
            name.lower() in self._credential_headers
            for name, _value in (*request.header_items(), *request.unredirected_hdrs.items())
        )
        if credentialed and self._origin(request.full_url) != self._origin(new_url):
            raise urllib.error.URLError("credentialed redirect target changed origin")
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def _open_public_request(request: urllib.request.Request):
    if not is_public_fetch_url(request.full_url):
        raise ValueError("Fetch target must be a credential-free public HTTP(S) URL")
    opener = urllib.request.build_opener(_PublicFetchRedirectHandler())
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def _read_limited_response(response) -> bytes:
    body = response.read(MAX_FETCH_RESPONSE_BYTES + 1)
    if len(body) > MAX_FETCH_RESPONSE_BYTES:
        raise OSError(f"Fetch response exceeds {MAX_FETCH_RESPONSE_BYTES} bytes")
    return body


def request_html(url: str, params: dict[str, str] | None = None) -> str:
    if params:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
    )
    with _open_public_request(request) as response:
        if not is_public_fetch_url(response.geturl()):
            raise OSError("Fetch response URL is not a public HTTP(S) URL")
        body = _read_limited_response(response)
        content_type = response.headers.get_content_charset() or "utf-8"
    return body.decode(content_type, errors="replace")


def request_json(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    json_body: object | None = None,
) -> object:
    encoded_body = None
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    if json_body is not None:
        encoded_body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url,
        data=encoded_body,
        headers=request_headers,
    )
    with _open_public_request(request) as response:
        if not is_public_fetch_url(response.geturl()):
            raise OSError("Fetch response URL is not a public HTTP(S) URL")
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(_read_limited_response(response).decode(charset, errors="replace"))


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
        structured_data=tuple(extracted.structured_data),
        discovery_links=tuple(extracted.discovery_links),
    )


def parse_discovery_document(
    base_url: str,
    xml_text: str,
    limit: int = 1000,
) -> tuple[tuple[Link, ...], tuple[str, ...]]:
    """Extract page and nested-manifest URLs from RSS, Atom, or sitemap XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return (), ()

    def local_name(element: ET.Element) -> str:
        return element.tag.rsplit("}", 1)[-1].lower()

    pages: list[Link] = []
    manifests: list[str] = []
    root_name = local_name(root)
    if root_name in {"urlset", "sitemapindex"}:
        child_name = "url" if root_name == "urlset" else "sitemap"
        for child in root:
            if local_name(child) != child_name:
                continue
            location = next((clean_text(item.text or "") for item in child if local_name(item) == "loc"), "")
            if not location:
                continue
            resolved = absolute_url(base_url, location)
            if root_name == "sitemapindex":
                manifests.append(resolved)
            else:
                pages.append(Link(label=resolved, url=resolved))
            if len(pages) + len(manifests) >= limit:
                break
    else:
        entries = [element for element in root.iter() if local_name(element) in {"item", "entry"}]
        for entry in entries[:limit]:
            title = next((clean_text(item.text or "") for item in entry if local_name(item) == "title"), "")
            url = ""
            fallback_url = ""
            for item in entry:
                if local_name(item) != "link":
                    continue
                candidate = clean_text(item.attrib.get("href", "") or item.text or "")
                if not candidate:
                    continue
                fallback_url = fallback_url or candidate
                if item.attrib.get("rel", "alternate").lower() in {"", "alternate"}:
                    url = candidate
                    break
            url = url or fallback_url
            if url:
                resolved = absolute_url(base_url, url)
                pages.append(Link(label=title or resolved, url=resolved))
    return tuple(pages), tuple(manifests)


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
    if not is_public_fetch_url(url):
        raise ValueError("Fetch target must be a credential-free public HTTP(S) URL")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runner:
        browser = runner.chromium.launch(headless=True)
        try:
            context = browser.new_context(user_agent=BROWSER_USER_AGENT, locale="ja-JP")
            context.route(
                "**/*",
                lambda route: route.continue_() if is_public_fetch_url(route.request.url) else route.abort(),
            )
            page = context.new_page()
            # "networkidle" never settles on ad/analytics-heavy JP sites;
            # wait for the DOM then give client-side JS a moment to render.
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            page.wait_for_timeout(BROWSER_SETTLE_MS)
            html = page.content()
            if len(html.encode("utf-8")) > MAX_FETCH_RESPONSE_BYTES:
                raise OSError(f"Browser response exceeds {MAX_FETCH_RESPONSE_BYTES} bytes")
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
