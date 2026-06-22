# chusennote backend: the REST API / web UI plus the headless-browser scraper.
# Scraping + Chromium must run server-side (not on a phone), so this uses the
# official Playwright image, which already bundles Chromium and its OS libraries
# (avoids the Debian/Ubuntu apt-dependency mismatch of installing them by hand).
# The image's Playwright version is the contract — bump the tag deliberately.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # JS-rendered ticket sites (shiki.jp etc.) only parse with a real browser.
    CHUSENNOTE_BROWSER_FETCH=fallback

WORKDIR /app

# The app is stdlib-only on SQLite; the hosted backend talks to Postgres, so the
# image ships the psycopg driver. (SQLite still needs no driver.)
RUN pip install "psycopg[binary]==3.2.3"

# Just copy the source (see .dockerignore for exclusions).
COPY . /app

# Run as the image's non-root user; the SQLite database lives on a mounted
# volume at /data so it survives restarts. (Postgres replaces this next.)
RUN mkdir -p /data && chown -R pwuser:pwuser /app /data
USER pwuser
VOLUME ["/data"]
EXPOSE 8765

# Serve the API/UI on all interfaces. Override the command to run the scheduler:
#   docker run … python3 lottery_monitor.py watch loop --interval-minutes 60 \
#     --kind event --db /data/chusennote.sqlite3
CMD ["python3", "lottery_monitor.py", "web", \
     "--host", "0.0.0.0", "--port", "8765", "--db", "/data/chusennote.sqlite3"]
