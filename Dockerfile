# RR Fetcher Service - FastAPI + FlareSolverr in single container
# Runs both RR Fetcher (port 8080) and FlareSolverr (port 8191) via supervisord

FROM python:3.12-slim

# Install system dependencies for FlareSolverr + Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    supervisor \
    wget \
    gnupg \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome (for FlareSolverr) - modern approach without apt-key
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 fetcher && \
    mkdir -p /home/fetcher/.cache && \
    chown -R fetcher:fetcher /home/fetcher

WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install FlareSolverr
RUN pip install --no-cache-dir flaresolverr

# Copy application code
COPY --chown=fetcher:fetcher . .

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create log directories
RUN mkdir -p /var/log && chown -R fetcher:fetcher /var/log

USER fetcher

# Environment
ENV PYTHONUNBUFFERED=1 \
    PORT=5555 \
    FLARESOLVERR_URL=http://localhost:8191

EXPOSE 5555

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]