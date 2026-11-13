# RR Fetcher Service - FastAPI only
# FlareSolverr runs in a separate container (official image v3.5.2+)

FROM python:3.12-slim

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 fetcher && \
    mkdir -p /home/fetcher/.cache /home/fetcher/data && \
    chown -R fetcher:fetcher /home/fetcher

WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=fetcher:fetcher . .

USER fetcher

# Environment
ENV PYTHONUNBUFFERED=1 \
    FLARESOLVERR_URL=http://flaresolverr:8191 \
    PYTHONMALLOC=malloc \
    MALLOC_ARENA_MAX=2

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:5555/health || exit 1

EXPOSE 5555

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5555"]