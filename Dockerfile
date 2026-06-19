FROM python:3.12-slim

WORKDIR /app

# Install system dependencies required by Playwright/Chromium
RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (includes playwright browsers)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" \
    && playwright install chromium \
    && rm -rf ~/.cache/pip

COPY . .

CMD ["homeops", "daemon"]
