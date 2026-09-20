# chusennote backend: the REST API / web UI plus the headless-browser scraper.
# Scraping + Chromium must run server-side (not on a phone), so this uses the
# official Playwright image, which already bundles Chromium and its OS libraries
# (avoids the Debian/Ubuntu apt-dependency mismatch of installing them by hand).
# The image's Playwright version is the contract — bump the tag deliberately.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # JS-rendered ticket sites (shiki.jp etc.) only parse with a real browser.
    CHUSENNOTE_BROWSER_FETCH=fallback

WORKDIR /app

# The hosted backend uses Google ADC for FCM and talks to Postgres, so install
# the declared runtime dependencies plus the database driver. SQLite still
# needs no database driver.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    "playwright==1.62.0" \
    "psycopg[binary]==3.2.3"

# Just copy the source (see .dockerignore for exclusions).
COPY . /app

# Run as the image's non-root user. SQLite deployments persist through the
# mounted /data volume; Postgres deployments use CHUSENNOTE_DATABASE_URL.
RUN mkdir -p /data && chown -R pwuser:pwuser /app /data
USER pwuser
VOLUME ["/data"]
EXPOSE 8877

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import json, os, urllib.request; from chusennote.models import APP_BUILD, APP_VERSION, DB_SCHEMA_VERSION; port = int(os.environ.get('PORT', '8877')); response = urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=4); payload = json.load(response); assert response.status == 200 and payload.get('status') == 'ok' and payload.get('version') == APP_VERSION and payload.get('build') == APP_BUILD and payload.get('schema_version', 0) >= DB_SCHEMA_VERSION"]

# Serve the API/UI on all interfaces. Override the command to run the scheduler:
#   docker run … python3 lottery_monitor.py watch loop --interval-minutes 60 \
#     --kind event --db /data/chusennote.sqlite3
CMD ["python3", "lottery_monitor.py", "web", \
     "--host", "0.0.0.0"]
