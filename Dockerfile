FROM python:3.11-slim

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libvpx9 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Env vars with safe defaults — override all of these in Cloud Run console
ENV SNAPSHOTS_DIR=/tmp/snapshots \
    GEMINI_MAX_PARALLEL=1 \
    MAX_PROJECTS_TO_ANALYZE=3 \
    CORS_ORIGINS=http://localhost:5173

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Don't copy .env — use Cloud Run environment variables
RUN rm -f .env

EXPOSE 8080

CMD uvicorn main_api:app --host 0.0.0.0 --port ${PORT:-8080}
