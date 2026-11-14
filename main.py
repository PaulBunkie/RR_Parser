# -*- coding: utf-8 -*-
"""RR Fetcher Service - FastAPI + FlareSolverr
Extracts chapter text from Royal Road, bypassing Anubis/Cloudflare.
Uses persistent FlareSolverr session with auto-recreate on challenge.
Proper REST API with HTTP status codes and structured errors.
"""

import asyncio
import gc
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from curl_cffi.requests import AsyncSession as CurlAsyncSession
from fastapi import FastAPI, HTTPException, Request, status, Depends, Header
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, Field

# API Key verification dependency
async def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"}
        )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("rr-fetcher")

# FlareSolverr configuration
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")
SESSION_NAME = "rr_session"

# API Key authentication
API_KEY = os.getenv("API_KEY", "MfN4l9DjJ7OBrXHjJodjUdKToE0hJ8O_RYKI8t-b-0A")

# Cookie persistence
COOKIES_FILE = "/home/fetcher/data/flaresolverr_cookies.json"

# Consistent headers - MUST match between client and FlareSolverr session
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",  # NO brotli (br) - critical for consistency
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": "\"Not_A Brand\";v=\"8\", \"Chromium\";v=\"120\", \"Google Chrome\";v=\"120\"",
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": "\"Windows\"",
}

SELECTORS = [
    ".chapter-content",
    ".chapter-inner",
    "#chapter-content",
    "article.chapter",
    "div[class*='chapter-content']",
    "main",
]


def load_saved_cookies() -> list:
    """Load cookies from file (returns list of cookie dicts for FlareSolverr)."""
    try:
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, "r") as f:
                cookies = json.load(f)
            if isinstance(cookies, list):
                log.info(f"Loaded {len(cookies)} saved cookies from {COOKIES_FILE}")
                return cookies
            elif isinstance(cookies, dict):
                # Backward compatibility if saved as dict
                cookie_list = [{"name": k, "value": v} for k, v in cookies.items() if k]
                log.info(f"Loaded {len(cookie_list)} saved cookies (converted from dict) from {COOKIES_FILE}")
                return cookie_list
    except Exception as e:
        log.info(f"Failed to load cookies from {COOKIES_FILE}: {e}")
    return []


def save_cookies_to_flaresolverr(cookies_list):
    """Save cookies only if they contain clearance cookies."""
    try:
        os.makedirs(os.path.dirname(COOKIES_FILE), exist_ok=True)
    except Exception:
        pass

    if not cookies_list:
        try:
            with open(COOKIES_FILE, "w") as f:
                json.dump([], f)
            log.info("Cleared cookies file")
        except Exception as e:
            log.error(f"Failed to clear cookies: {e}")
        return

    # Normalize to list of dicts if dict was passed
    if isinstance(cookies_list, dict):
        cookies_list = [{"name": k, "value": v} for k, v in cookies_list.items() if k]

    # Only save if we have actual clearance cookies
    cookie_names = [c.get("name") for c in cookies_list if isinstance(c, dict)]
    has_clearance = any(name in ["cf_clearance", "__cf_bm", "RoyalRoad.SessionId"] for name in cookie_names)
    if not has_clearance:
        log.warning("Refusing to save cookies without clearance tokens")
        return

    try:
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies_list, f)
        log.info(f"Saved {len(cookies_list)} cookies to {COOKIES_FILE}")
    except Exception as e:
        log.error(f"Failed to save cookies to {COOKIES_FILE}: {e}")


# Global HTTP client for FlareSolverr
_http_client: Optional[httpx.AsyncClient] = None
_session_manager: Optional["SessionManager"] = None


# ==================== SESSION MANAGER WITH ROTATION ====================

class SessionManager:
    """Manages FlareSolverr session with lazy creation + TTL to prevent memory leaks."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        flaresolverr_url: str,
        session_name: str,
        max_requests_per_session: int = 50,
        max_session_age_seconds: int = 1800,  # 30 minutes
        idle_timeout_seconds: int = 1800,     # 30 minutes idle = kill session + Chrome
    ):
        self.http_client = http_client
        self.flaresolverr_url = flaresolverr_url
        self.session_name = session_name
        self.max_requests_per_session = max_requests_per_session
        self.max_session_age_seconds = max_session_age_seconds
        self._idle_timeout_seconds = idle_timeout_seconds

        self._session_created = False
        self._request_count = 0
        self._session_created_at: Optional[float] = None
        self._last_request_at: Optional[float] = None
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start_cleanup_task(self) -> None:
        """Start background task for idle session cleanup."""
        async def cleanup_loop():
            while True:
                await asyncio.sleep(10)  # Check every 10 seconds
                await self._check_idle_timeout()

        self._cleanup_task = asyncio.create_task(cleanup_loop())
        log.info("Session cleanup task started (check every 60s)")

    async def stop_cleanup_task(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            log.info("Session cleanup task stopped")

    async def _check_idle_timeout(self) -> None:
        """Check if session is idle and destroy if timeout reached."""
        async with self._lock:
            if not self._session_created:
                return
            if self._last_request_at is None:
                return
            now = time.time()
            if (now - self._last_request_at) >= self._idle_timeout_seconds:
                log.info(f"Idle timeout reached ({now - self._last_request_at:.0f}s), destroying session...")
                await self._destroy_session_internal()

    async def ensure_session(self) -> bool:
        """Create session if needed, rotate if limits exceeded or idle timeout reached."""
        async with self._lock:
            now = time.time()

            # Check if rotation needed
            if self._session_created:
                if self._request_count >= self.max_requests_per_session:
                    log.info(f"Session request limit reached ({self._request_count}), rotating...")
                    await self._destroy_session_internal()
                elif self._session_created_at and (now - self._session_created_at) >= self.max_session_age_seconds:
                    log.info(f"Session age limit reached ({now - self._session_created_at:.0f}s), rotating...")
                    await self._destroy_session_internal()

            if not self._session_created:
                return await self._create_session_internal()

            return True

    async def _create_session_internal(self) -> bool:
        """Internal session creation with retries."""
        for attempt in range(20):
            try:
                resp = await self.http_client.post(
                    f"{self.flaresolverr_url}/v1",
                    json={"cmd": "sessions.create", "session": self.session_name},
                    timeout=30
                )
                data = resp.json()
                if data.get("status") == "ok":
                    log.info(f"FlareSolverr session '{self.session_name}' created")
                    self._session_created = True
                    self._request_count = 0
                    self._session_created_at = time.time()
                    return True
                else:
                    log.warning(f"Failed to create session (attempt {attempt+1}/20): {data}")
            except httpx.ConnectError as e:
                log.warning(f"FlareSolverr not ready (attempt {attempt+1}/20): {e}")
            except httpx.TimeoutException as e:
                log.warning(f"FlareSolverr timeout (attempt {attempt+1}/20): {e}")
            except Exception as e:
                log.warning(f"Error creating session (attempt {attempt+1}/20): {e}")

            if attempt < 19:
                await asyncio.sleep(5.0)

        log.error("Failed to create FlareSolverr session after 20 attempts")
        return False

    async def _destroy_session_internal(self) -> None:
        """Destroy session and forcibly kill Chrome processes."""
        if self._session_created:
            try:
                await self.http_client.post(
                    f"{self.flaresolverr_url}/v1",
                    json={"cmd": "sessions.destroy", "session": self.session_name},
                    timeout=10
                )
                log.info(f"FlareSolverr session '{self.session_name}' destroyed via API")
            except Exception as e:
                log.warning(f"Failed to destroy session via API: {e}")

        # BRUTE FORCE: Kill any remaining Chrome/Chromium processes
        try:
            import subprocess
            result = subprocess.run(
                ["pkill", "-f", "chrome"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                log.info("Killed residual Chrome processes via pkill")
            elif result.returncode == 1:
                log.debug("No Chrome processes to kill")
            else:
                log.warning(f"pkill returned code {result.returncode}: {result.stderr.decode()}")
        except subprocess.TimeoutExpired:
            log.warning("pkill timed out")
        except Exception as e:
            log.warning(f"Failed to kill Chrome processes: {e}")

        self._session_created = False
        self._request_count = 0
        self._session_created_at = None
        await asyncio.sleep(1.5)  # Ensure pkill settles before creating new session

    async def record_request(self) -> None:
        """Increment request counter and update last request time."""
        async with self._lock:
            self._request_count += 1
            self._last_request_at = time.time()

    @property
    def session_created(self) -> bool:
        return self._session_created

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def last_request_at(self) -> Optional[float]:
        return self._last_request_at

    @property
    def idle_timeout_seconds(self) -> int:
        return self._idle_timeout_seconds


# ==================== ERROR CLASSES ====================

class FetcherError(Exception):
    """Base exception with HTTP status code and error code."""
    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_code: str = "INTERNAL_ERROR",
        details: Optional[dict] = None
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}


class ValidationError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_400_BAD_REQUEST, "VALIDATION_ERROR", details)


class UpstreamUnavailableError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_502_BAD_GATEWAY, "UPSTREAM_UNAVAILABLE", details)


class UpstreamTimeoutError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_504_GATEWAY_TIMEOUT, "UPSTREAM_TIMEOUT", details)


class ChallengeError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_503_SERVICE_UNAVAILABLE, "CHALLENGE_FAILED", details)


class ExtractionError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_500_INTERNAL_SERVER_ERROR, "EXTRACTION_FAILED", details)


class SessionError(FetcherError):
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(message, status.HTTP_503_SERVICE_UNAVAILABLE, "SESSION_ERROR", details)


# ==================== ERROR RESPONSE MODEL ====================

class ErrorResponse(BaseModel):
    """Standard error response format."""
    error: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[dict] = Field(None, description="Additional error context")
    request_id: Optional[str] = Field(None, description="Request ID for tracing")


class FetchResponse(BaseModel):
    """Successful fetch response."""
    text: str = Field(..., description="Extracted chapter text")
    title: str = Field(..., description="Chapter title")
    html: str = Field(..., description="Full HTML response")


# ==================== HELPERS ====================

def is_challenge_response(html: str) -> bool:
    """Detect if HTML is an Anubis/Cloudflare challenge page.
    
    Anubis overlays the challenge on top of the actual content, so we must
    FIRST check if chapter content is present before flagging as challenge.
    """
    if not html:
        return True
    
    # FIRST: Check if chapter content is already present - if so, NOT a challenge
    if 'chapter-content' in html or 'chapter-inner' in html:
        return False  # Content is present = NOT a challenge page
    
    lowered = html.lower()
    # Only match explicit challenge page indicators (no chapter content present)
    challenge_markers = [
        "making sure you're not a bot",
        "making sure you are not a bot",
        "protected by anubis",
        "proof of work",
        "just a moment",
        "checking your browser",
        "challenges.cloudflare.com",
        "cf_chl_captcha",
        "cf_chl_opt",
        "ray id",
    ]
    return any(m in lowered for m in challenge_markers)


# ==================== FLARESOLVERR CLIENT (UPDATED) ====================

async def create_flaresolverr_session(max_retries: int = 10, retry_delay: float = 3.0) -> bool:
    """Create persistent FlareSolverr session with consistent headers (legacy compatibility)."""
    global _session_manager
    if _session_manager:
        return await _session_manager.ensure_session()
    return False


async def destroy_flaresolverr_session() -> None:
    """Destroy FlareSolverr session on shutdown."""
    global _session_manager
    if _session_manager:
        await _session_manager._destroy_session_internal()


async def fetch_with_flaresolverr(url: str, timeout_ms: int) -> dict:
    """Fetch URL using persistent FlareSolverr session with auto-rotation."""
    global _session_manager

    # Ensure session exists (creates or rotates if needed)
    if not await _session_manager.ensure_session():
        raise UpstreamUnavailableError("Failed to create/ensure FlareSolverr session")

    async def do_request() -> dict:
        """Single fetch attempt."""
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout_ms,
            "session": SESSION_NAME,
            "waitFor": ", ".join(SELECTORS),
            "headers": BROWSER_HEADERS,
            "returnOnlyCookies": False,
        }

        saved_cookies = load_saved_cookies()
        if saved_cookies:
            payload["cookies"] = saved_cookies
            log.info(f"Injecting {len(saved_cookies)} saved cookies into FlareSolverr request")

        try:
            # Add 2 minutes buffer for Anubis challenge resolution
            resp = await _http_client.post(
                f"{FLARESOLVERR_URL}/v1",
                json=payload,
                timeout=timeout_ms / 1000 + 180
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as e:
            raise UpstreamUnavailableError(
                "Cannot connect to FlareSolverr",
                details={"url": FLARESOLVERR_URL, "error": str(e)}
            )
        except httpx.TimeoutException as e:
            raise UpstreamTimeoutError(
                "FlareSolverr request timeout",
                details={"url": url, "timeout_ms": timeout_ms, "error": str(e)}
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and "session" in str(e).lower():
                raise SessionError("FlareSolverr session not found")
            raise UpstreamUnavailableError(
                f"FlareSolverr HTTP error: {e.response.status_code}",
                details={"status_code": e.response.status_code, "body": e.response.text}
            )
    
    # Attempt 1: Use existing session
    try:
        data = await do_request()
    except (SessionError, UpstreamUnavailableError) as e:
        log.warning("FlareSolverr request failed, recreating session: %s", e)
        await _session_manager._destroy_session_internal()
        await _session_manager._create_session_internal()
        try:
            data = await do_request()
        except Exception:
            raise
    
    # Check response structure
    if "solution" not in data:
        raise UpstreamUnavailableError(
            "Invalid FlareSolverr response",
            details={"response": data}
        )
    
    solution = data["solution"]
    html = solution.get("response", "")
    
    # DEBUG: Log HTML preview for debugging challenge detection
    log.debug("FlareSolverr HTML preview (first 500 chars): %s", html[:500] if html else "EMPTY")
    
    # IMMEDIATE challenge check - FlareSolverr v2 may say "Challenge not detected!" but return challenge page
    if is_challenge_response(html):
        log.warning("FlareSolverr returned challenge page (FS said 'not detected'), triggering retry...")
        log.warning("Challenge detected in FlareSolverr response, recreating session...")
        await _session_manager._destroy_session_internal()
        await _session_manager._create_session_internal()
        log.info("Retrying request with new session...")
        try:
            data = await do_request()
            solution = data.get("solution", {})
            html = solution.get("response", "")
            
            # DEBUG
            log.debug("Retry HTML preview (first 500 chars): %s", html[:500] if html else "EMPTY")
            
            if is_challenge_response(html):
                log.error("Challenge persists after session recreation")
                raise ChallengeError(
                    "Anubis/Cloudflare challenge persists after session recreation",
                    details={"url": url, "attempt": 2}
                )
            else:
                log.info("Retry succeeded - challenge resolved")
        except FetcherError:
            raise
        except Exception as e:
            raise UpstreamUnavailableError(
                f"Retry failed: {e}",
                details={"url": url, "error": str(e)}
            )
    
    # Record successful request for rotation tracking
    await _session_manager.record_request()
    
    # Extract text from HTML
    text = ""
    title = ""
    
    if html:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Try selectors
        for sel in SELECTORS:
            element = soup.select_one(sel)
            if element:
                text = element.get_text(strip=True)
                if len(text) > 500:
                    break
        
        # Fallback
        if len(text) < 500:
            body = soup.select_one("body")
            if body:
                text = body.get_text(strip=True)
        
        # Title
        title_tag = soup.select_one("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
    
    if len(text) < 500:
        raise ExtractionError(
            "Failed to extract chapter content",
            details={"url": url, "html_length": len(html), "selectors_tried": SELECTORS}
        )
    
    # Extract cookies from solution (FlareSolverr v2 returns list of cookie objects)
    cookies_list = solution.get("cookies", [])
    if cookies_list:
        save_cookies_to_flaresolverr(cookies_list)
        # Destroy FlareSolverr session immediately to kill Chrome process and free 100% CPU and 1GB RAM
        if _session_manager:
            log.info("Destroying FlareSolverr session after cookie extraction to free CPU and RAM")
            await _session_manager._destroy_session_internal()

    return {"success": True, "text": text, "title": title, "html": html}
# ==================== FASTAPI APP ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client, _session_manager
    log.info("Starting RR Fetcher...")
    
    # HTTP client with connection pooling
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=30.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )
    
    # Initialize session manager with lazy creation + TTL
    _session_manager = SessionManager(
        http_client=_http_client,
        flaresolverr_url=FLARESOLVERR_URL,
        session_name=SESSION_NAME,
        max_requests_per_session=50,      # Rotate every 50 requests
        max_session_age_seconds=1800,     # Or every 30 minutes
        idle_timeout_seconds=30,          # 30 seconds idle = kill session + Chrome
    )
    
    # Start background cleanup task for idle sessions
    await _session_manager.start_cleanup_task()
    
    # NOTE: Lazy session creation - session will be created on first fetch request
    
    log.info("RR Fetcher ready (lazy session mode)")
    yield
    
    log.info("Shutting down RR Fetcher...")
    await _session_manager.stop_cleanup_task()
    await destroy_flaresolverr_session()
    await _http_client.aclose()


app = FastAPI(
    title="RR Fetcher",
    version="1.0.0",
    lifespan=lifespan,
    responses={
        400: {"model": ErrorResponse, "description": "Validation Error"},
        408: {"model": ErrorResponse, "description": "Request Timeout"},
        502: {"model": ErrorResponse, "description": "Upstream Unavailable"},
        503: {"model": ErrorResponse, "description": "Service Unavailable (Challenge/Session)"},
        504: {"model": ErrorResponse, "description": "Upstream Timeout"},
    }
)


# ==================== EXCEPTION HANDLERS ====================

@app.exception_handler(FetcherError)
async def fetcher_error_handler(request: Request, exc: FetcherError):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.error_code,
            message=exc.message,
            details=exc.details,
            request_id=request.headers.get("X-Request-ID")
        ).model_dump(exclude_none=True)
    )


@app.exception_handler(httpx.TimeoutException)
async def timeout_handler(request: Request, exc: httpx.TimeoutException):
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content=ErrorResponse(
            error="UPSTREAM_TIMEOUT",
            message="Upstream service timeout",
            details={"error": str(exc)},
            request_id=request.headers.get("X-Request-ID")
        ).model_dump(exclude_none=True)
    )


@app.exception_handler(httpx.ConnectError)
async def connect_error_handler(request: Request, exc: httpx.ConnectError):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content=ErrorResponse(
            error="UPSTREAM_UNAVAILABLE",
            message="Cannot connect to upstream service",
            details={"error": str(exc)},
            request_id=request.headers.get("X-Request-ID")
        ).model_dump(exclude_none=True)
    )


# ==================== REQUEST/RESPONSE MODELS ====================

class FetchRequest(BaseModel):
    url: HttpUrl = Field(..., description="Royal Road chapter URL")
    timeout_ms: int = Field(120000, ge=5000, le=300000, description="Timeout in milliseconds")


class FetchSuccessResponse(BaseModel):
    success: bool = Field(..., description="Whether the fetch was successful")
    text: str = Field(..., description="Extracted chapter text")
    title: str = Field(..., description="Chapter title")
    html: str = Field(..., description="Full HTML response")


# ==================== ENDPOINTS ====================

@app.post(
    "/fetch",
    response_model=FetchSuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch chapter content from Royal Road",
    dependencies=[Depends(verify_api_key)],
    responses={
        200: {"model": FetchSuccessResponse, "description": "Successfully extracted chapter"},
        400: {"model": ErrorResponse, "description": "Validation Error"},
        401: {"model": ErrorResponse, "description": "Unauthorized - Invalid API Key"},
        408: {"model": ErrorResponse, "description": "Request Timeout"},
        502: {"model": ErrorResponse, "description": "Upstream Unavailable"},
        503: {"model": ErrorResponse, "description": "Service Unavailable (Challenge/Session)"},
        504: {"model": ErrorResponse, "description": "Upstream Timeout"},
    }
)
async def fetch_chapter(request: FetchRequest):
    """Fetch chapter content from Royal Road URL.
    
    Tries saved cookies first (bypasses FlareSolverr).
    Falls back to FlareSolverr with retry logic on challenge.
    Returns extracted text, title, and full HTML on success.
    On failure, returns appropriate HTTP error with structured error body.
    """
    url = str(request.url)
    
    if "royalroad.com" not in url:
        raise ValidationError(
            "Only royalroad.com URLs are supported",
            details={"provided_url": url}
        )
    
    # Try fast curl_cffi request with saved cookies (Chrome TLS fingerprint impersonation)
    saved_cookies = load_saved_cookies()
    if saved_cookies:
        cookie_dict = {}
        if isinstance(saved_cookies, list):
            cookie_dict = {c.get("name"): c.get("value") for c in saved_cookies if c.get("name") and c.get("value")}
        elif isinstance(saved_cookies, dict):
            cookie_dict = saved_cookies

        if cookie_dict:
            try:
                async with CurlAsyncSession(impersonate="chrome") as session:
                    resp = await session.get(
                        url,
                        cookies=cookie_dict,
                        headers=BROWSER_HEADERS,
                        timeout=request.timeout_ms / 1000,
                        allow_redirects=True
                    )
                    if resp.status_code == 200 and not is_challenge_response(resp.text):
                        log.info("Successfully fetched chapter instantly using curl_cffi (Chrome TLS impersonation)")
                        text = ""
                        title = ""
                        if resp.text:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(resp.text, "html.parser")
                            for sel in SELECTORS:
                                element = soup.select_one(sel)
                                if element:
                                    text = element.get_text(strip=True)
                                    if len(text) > 500:
                                        break
                            if len(text) < 500:
                                body = soup.select_one("body")
                                if body:
                                    text = body.get_text(strip=True)
                            title_tag = soup.select_one("title")
                            title = title_tag.get_text(strip=True) if title_tag else ""
                        if len(text) >= 500:
                            gc.collect()
                            return FetchSuccessResponse(success=True, text=text, title=title or "Chapter", html=resp.text)
                    else:
                        log.warning(f"curl_cffi fast fetch returned status {resp.status_code} or challenge page, falling back to FlareSolverr")
            except Exception as e:
                log.warning(f"curl_cffi fast fetch failed: {e}, falling back to FlareSolverr")

    # Fallback to FlareSolverr
    result = await fetch_with_flaresolverr(url, request.timeout_ms)
    gc.collect()  # Force GC after each fetch to reclaim memory
    return result


@app.get(
    "/health",
    summary="Health check",
    response_model=dict,
    dependencies=[Depends(verify_api_key)]
)
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "flaresolverr_url": FLARESOLVERR_URL,
        "session": SESSION_NAME,
        "session_created": _session_manager.session_created if _session_manager else False,
    }


@app.get(
    "/cookies/status",
    summary="Check saved cookie status",
)
async def cookies_status():
    cookies = load_saved_cookies()
    if not cookies:
        return {"valid": False, "reason": "no_cookies", "cookie_count": 0, "cookie_names": []}
    
    has_clearance = any(cookies.get(c) for c in ["__cf_bm", "cf_clearance", "RoyalRoad.SessionId"])
    
    # Quick validation: test cookies on Royal Road homepage
    valid = False
    if has_clearance:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.royalroad.com/",
                    cookies=cookies,
                    headers=BROWSER_HEADERS,
                    timeout=10.0,
                    follow_redirects=True
                )
                if resp.status_code == 200 and not is_challenge_response(resp.text):
                    valid = True
        except Exception:
            pass
    
    return {
        "valid": valid,
        "has_clearance_cookies": has_clearance,
        "cookie_count": len(cookies),
        "cookie_names": list(cookies.keys()),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
