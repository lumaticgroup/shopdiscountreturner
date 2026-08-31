# Playwright is a hard requirement (Trendyol flow is Cloudflare-gated).
# The official Playwright image ships the Chromium binary + all system libs.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python deps first so this layer caches when only source changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source. .dockerignore keeps .env, .venv, *.db, etc. out of the image.
COPY . .

# Start the Telegram Bot process
CMD ["python", "bot.py"]
