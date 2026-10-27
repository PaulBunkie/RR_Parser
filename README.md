# RR Fetcher Service

**Single-container** microservice for extracting chapter text from Royal Road.
Runs both **RR Fetcher (FastAPI)** and **FlareSolverr (Chromium)** via supervisord.
Bypasses Anubis/Cloudflare challenges. Total RAM ~1 GB.

## Architecture

```
fly.io (main app)          GCE VM (single container)
┌─────────────────┐       ┌──────────────────────────────────┐
│ royalroad/      │──────▶│ Port 8080: RR Fetcher (FastAPI)  │
│ client.py       │       │   → calls FlareSolverr           │
└─────────────────┘       │   → extracts text                │
                          │ Port 8191: FlareSolverr          │
                          │   → Chromium browser             │
                          │   → solves Anubis                │
                          └──────────────────────────────────┘
                          (supervisord manages both processes)
```

## Deploy: Single Container on GCE

### 1. Build & Push Docker Image
```bash
# From this directory (E:\Projects\Cursor\RR_Parser)
cd E:\Projects\Cursor\RR_Parser

# Build
docker build -t gcr.io/YOUR_PROJECT_ID/rr-fetcher:latest .

# Push to Google Container Registry
docker push gcr.io/YOUR_PROJECT_ID/rr-fetcher:latest
```

### 2. Deploy to GCE VM (Container-Optimized OS)
```bash
# Single VM with 1-2 GB RAM (e2-small or e2-medium)
gcloud compute instances create rr-fetcher \
    --project=YOUR_PROJECT_ID \
    --zone=us-central1-a \
    --machine-type=e2-small \          # 2 GB RAM, 2 vCPU (~$15/mo)
    --image-family=cos-stable \
    --image-project=cos-cloud \
    --container-image=gcr.io/YOUR_PROJECT_ID/rr-fetcher:latest \
    --container-env=PORT=8080 \
    --container-restart-policy=always \
    --tags=http-server \
    --boot-disk-size=20GB
```

### 3. Allow HTTP traffic (only port 8080 exposed externally)
```bash
gcloud compute firewall-rules create allow-rr-fetcher \
    --allow=tcp:8080 --target-tags=http-server --source-ranges=0.0.0.0/0
```

### 4. Get external IP
```bash
gcloud compute instances describe rr-fetcher --zone=us-central1-a \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

## Integration with Main App (fly.io)

Set environment variable on fly.io:
```bash
fly secrets set FETCHER_SERVICE_URL=http://YOUR_GCE_FETCHER_EXTERNAL_IP:8080
```

The `royalroad/client.py` will automatically use this service when the env var is set.

## API Usage

### Fetch chapter
```bash
curl -X POST http://YOUR_GCE_FETCHER_IP:8080/fetch \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.royalroad.com/fiction/12345/chapter/67890/chapter-title"}'
```

### Success Response (200 OK)
```json
{
  "text": "Chapter content...",
  "title": "Chapter 1: Title",
  "html": "<full html>"
}
```

### Error Responses

| Status | Error Code | Description |
|--------|------------|-------------|
| 400 | VALIDATION_ERROR | Invalid URL (not royalroad.com) |
| 408 | REQUEST_TIMEOUT | Request timeout |
| 502 | UPSTREAM_UNAVAILABLE | FlareSolverr unavailable |
| 503 | CHALLENGE_FAILED / SESSION_ERROR | Anubis challenge failed or session error |
| 504 | UPSTREAM_TIMEOUT | FlareSolverr timeout |

### Error Response Format
```json
{
  "error": "CHALLENGE_FAILED",
  "message": "Anubis/Cloudflare challenge persists after session recreation",
  "details": {"url": "...", "attempt": 2},
  "request_id": "optional-request-id"
}
```

### Health Check
```bash
curl http://YOUR_GCE_FETCHER_IP:8080/health
# {"status": "ok", "flaresolverr_url": "http://localhost:8191", "session": "rr_session", "session_created": true}
```

## Resource Requirements

| Component | RAM | CPU | Disk |
|-----------|-----|-----|------|
| **Total (single container)** | 1-2 GB | 2 vCPU | 20 GB |

## Cost Estimate (GCE us-central1)

- **e2-small** (2 GB RAM, 2 vCPU): ~$15/month always-on
- **e2-medium** (4 GB RAM, 2 vCPU): ~$30/month always-on (recommended for concurrent requests)
- **Spot/Preemptible**: ~$5-10/month

Much cheaper than running browser in main app!

## Updating

```bash
# Build & push new version
docker build -t gcr.io/YOUR_PROJECT_ID/rr-fetcher:v2 .
docker push gcr.io/YOUR_PROJECT_ID/rr-fetcher:v2

# Update GCE container
gcloud compute instances update-container rr-fetcher \
    --container-image=gcr.io/YOUR_PROJECT_ID/rr-fetcher:v2 \
    --zone=us-central1-a
```

## Key Features

- **Persistent FlareSolverr session** (`rr_session`) - created once, reused for all requests
- **Auto-recreate on challenge** - session recreated only when Anubis challenge detected
- **Consistent headers** - User-Agent, Accept-Language, Accept-Encoding (no brotli) fixed across all requests
- **Proper REST API** - HTTP status codes, structured error responses, OpenAPI docs
- **Graceful fallback** - curl → cookies → Fetcher Service → error