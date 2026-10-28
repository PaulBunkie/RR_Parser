# RR Fetcher Service

**Single-container** microservice for extracting chapter text from Royal Road.
Runs both **RR Fetcher (FastAPI)** and **FlareSolverr (Chromium)** via supervisord.
Bypasses Anubis/Cloudflare challenges. Total RAM ~1 GB.

## Architecture

```
fly.io (main app)          GCE VM (single container)
┌─────────────────┐       ┌──────────────────────────────────┐
│ royalroad/      │──────▶│ Port 5555: RR Fetcher (FastAPI)  │
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
    --container-env=PORT=5555 \
    --container-restart-policy=always \
    --tags=http-server \
    --boot-disk-size=20GB
```

### 3. Allow HTTP traffic (only port 5555 exposed externally)
```bash
gcloud compute firewall-rules create allow-rr-fetcher \
    --allow=tcp:5555 --target-tags=http-server --source-ranges=0.0.0.0/0
```

### 4. Get external IP
```bash
gcloud compute instances describe rr-fetcher --zone=us-central1-a \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

## Integration with Main App (fly.io)

Set environment variable on fly.io:
```bash
fly secrets set FETCHER_SERVICE_URL=http://YOUR_GCE_FETCHER_EXTERNAL_IP:5555
```

The `royalroad/client.py` will automatically use this service when the env var is set.

## Local Development (Docker Desktop on Windows)

**Known issue:** Docker Desktop on Windows has a port forwarding bug that closes long-running connections (>30s). The container works perfectly internally, but host-to-container port forwarding times out.

**Workaround for local testing:**

Option A: Test from inside the container
```bash
docker exec -it rr-fetcher-test python -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:5555/fetch', 
    data=json.dumps({'url': 'https://www.royalroad.com/fiction/...'}).encode(),
    headers={'Content-Type': 'application/json'}, method='POST')
print(urllib.request.urlopen(req, timeout=300).read().decode())
"
```

Option B: Run services natively on host (not in Docker)
```bash
# Terminal 1: FlareSolverr
python -m flaresolverr --port 8191

# Terminal 2: RR Fetcher
cd E:\Projects\Cursor\RR_Parser
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 5555 --timeout-keep-alive 300
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