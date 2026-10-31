with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old3 = '''@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _session_created
    log.info("Starting RR Fetcher...")
    
    # HTTP client with connection pooling
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )
    
    # Create persistent FlareSolverr session (with retries)
    success = await create_flaresolverr_session(max_retries=15, retry_delay=4.0)
    if not success:
        log.error("Failed to create FlareSolverr session after retries. Service will start but fetches will fail.")
    
    log.info("RR Fetcher ready")
    yield
    
    log.info("Shutting down RR Fetcher...")
    await destroy_flaresolverr_session()
    await _http_client.aclose()'''

new3 = '''@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _session_manager
    log.info("Starting RR Fetcher...")
    
    # HTTP client with connection pooling
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )
    
    # Initialize session manager with rotation
    _session_manager = SessionManager(
        http_client=_http_client,
        flaresolverr_url=FLARESOLVERR_URL,
        session_name=SESSION_NAME,
        max_requests_per_session=50,      # Rotate every 50 requests
        max_session_age_seconds=1800,     # Or every 30 minutes
    )
    
    # Create persistent FlareSolverr session (with retries)
    success = await _session_manager.ensure_session()
    if not success:
        log.error("Failed to create FlareSolverr session after retries. Service will start but fetches will fail.")
    
    log.info("RR Fetcher ready")
    yield
    
    log.info("Shutting down RR Fetcher...")
    await destroy_flaresolverr_session()
    await _http_client.aclose()'''

if old3 in content:
    content = content.replace(old3, new3)
    print('Replacement 3 OK')
else:
    print('OLD3 NOT FOUND')
    idx = content.find('@asynccontextmanager')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+200]))

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)