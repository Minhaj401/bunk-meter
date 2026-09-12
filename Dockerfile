FROM python:3.12-slim

# Chromium + chromedriver for Selenium. Letting apt resolve chromium's
# Depends pulls in every shared lib headless Chrome needs (works on any Debian release).
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Railway injects PORT; default matches EXPOSE for local docker runs
ENV PORT=8000
EXPOSE 8000

# Single worker: each login spawns a Chrome instance, keep memory predictable.
# Threads handle concurrent dashboard/simulate requests; 120s covers the Selenium scrape.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT} app:app"]
