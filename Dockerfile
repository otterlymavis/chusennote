# chusennote backend: the REST API / web UI plus the optional headless-browser
# scraper. Scraping + Chromium must run server-side (not on a phone), so the
# image bundles Playwright's Chromium and turns browser fetch on by default.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    # JS-rendered ticket sites (shiki.jp etc.) only parse with a real browser.
    CHUSENNOTE_BROWSER_FETCH=fallback

WORKDIR /app

# Playwright + Chromium and its OS libraries. Pinned so the browser install
# matches the library; bump deliberately.
RUN pip install "playwright==1.48.0" \
    && playwright install --with-deps chromium

# App is pure stdlib, so just copy the source (see .dockerignore for exclusions).
COPY . /app

# Run as non-root; the SQLite database lives on a mounted volume at /data so it
# survives container restarts. (Postgres replaces this in a later Phase 0 step.)
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /app /data /ms-playwright
USER app
VOLUME ["/data"]
EXPOSE 8765

# Serve the API/UI on all interfaces. Override the command to run the scheduler:
#   docker run … watch loop --interval-minutes 60 --kind event --db /data/chusennote.sqlite3
CMD ["python", "lottery_monitor.py", "web", \
     "--host", "0.0.0.0", "--port", "8765", "--db", "/data/chusennote.sqlite3"]
