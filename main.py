# -*- coding: utf-8 -*-
"""RR Fetcher Service - FastAPI + FlareSolverr
Extracts chapter text from Royal Road, bypassing Anubis/Cloudflare.
Uses persistent FlareSolverr session with auto-recreate on challenge.
Proper REST API with HTTP status codes and structured errors.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("rr-fetcher")

# FlareSolverr configuration
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")
SESSION_NAME = "rr_session"

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

# Global HTTP client for FlareSolverr
_http_client: Optional[httpx.AsyncClient] = None
_session_created = False


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
    """Detect if HTML is an Anubis/Cloudflare challenge page."""
    if not html:
        return True
    lowered = html.lower()
    # Only match explicit challenge page indicators, not generic Anubis references
    # (Royal Road pages reference 'anubis' in scripts even after challenge is solved)
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


# ==================== FLARESOLVERR CLIENT ====================

async def create_flaresolverr_session(max_retries: int = 10, retry_delay: float = 3.0) -> bool:
    """Create persistent FlareSolverr session with consistent headers.
    
    Args:
        max_retries: Maximum number of retry attempts (default 10)
        retry_delay: Delay between retries in seconds (default 3.0)
    """
    global _session_created
    
    for attempt in range(max_retries):
        try:
            resp = await _http_client.post(
                f"{FLARESOLVERR_URL}/v1",
                json={
                    "cmd": "sessions.create",
                    "session": SESSION_NAME,
                },
                timeout=30
            )
            data = resp.json()
            if data.get("status") == "ok":
                log.info("FlareSolverr session '%s' created", SESSION_NAME)
                _session_created = True
                return True
            else:
                log.warning("Failed to create FlareSolverr session (attempt %d/%d): %s", 
                           attempt + 1, max_retries, data)
        except httpx.ConnectError as e:
            log.warning("FlareSolverr not ready (attempt %d/%d): %s", 
                       attempt + 1, max_retries, e)
        except httpx.TimeoutException as e:
            log.warning("FlareSolverr timeout (attempt %d/%d): %s", 
                       attempt + 1, max_retries, e)
        except Exception as e:
            log.warning("Error creating FlareSolverr session (attempt %d/%d): %s", 
                       attempt + 1, max_retries, e)
        
        if attempt < max_retries - 1:
            log.info("Retrying in %.1f seconds... (attempt %d/%d)", 
                    retry_delay, attempt + 2, max_retries)
            await asyncio.sleep(retry_delay)
    
    log.error("Failed to create FlareSolverr session after %d attempts", max_retries)
    return False


async def destroy_flaresolverr_session() -> None:
    """Destroy FlareSolverr session on shutdown."""
    global _session_created
    if _session_created:
        try:
            await _http_client.post(
                f"{FLARESOLVERR_URL}/v1",
                json={"cmd": "sessions.destroy", "session": SESSION_NAME},
                timeout=10
            )
            log.info("FlareSolverr session '%s' destroyed", SESSION_NAME)
        except Exception:
            pass
        _session_created = False


async def fetch_with_flaresolverr(url: str, timeout_ms: int) -> dict:
    """Fetch URL using persistent FlareSolverr session.
    
    Retries once with session recreation if challenge detected.
    """
    global _session_created
    
    if not _session_created:
        await create_flaresolverr_session()
    
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
        
        try:
            resp = await _http_client.post(
                f"{FLARESOLVERR_URL}/v1",
                json=payload,
                timeout=timeout_ms / 1000 + 30
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
    except SessionError:
        log.warning("Session not found, recreating...")
        _session_created = False
        await create_flaresolverr_session()
        data = await do_request()
    
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
        _session_created = False
        
        # Recreate session and retry ONCE
        await create_flaresolverr_session()
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
    
    return {"success": True, "text": text, "title": title, "html": html}
# ==================== FASTAPI APP ====================

@asynccontextmanager
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
    responses={
        200: {"model": FetchSuccessResponse, "description": "Successfully extracted chapter"},
        400: {"model": ErrorResponse, "description": "Validation Error"},
        408: {"model": ErrorResponse, "description": "Request Timeout"},
        502: {"model": ErrorResponse, "description": "Upstream Unavailable"},
        503: {"model": ErrorResponse, "description": "Service Unavailable (Challenge/Session)"},
        504: {"model": ErrorResponse, "description": "Upstream Timeout"},
    }
)
async def fetch_chapter(request: FetchRequest):
    """Fetch chapter content from Royal Road URL via FlareSolverr.
    
    Returns extracted text, title, and full HTML on success.
    On failure, returns appropriate HTTP error with structured error body.
    """
    url = str(request.url)
    
    if "royalroad.com" not in url:
        raise ValidationError(
            "Only royalroad.com URLs are supported",
            details={"provided_url": url}
        )
    
    result = await fetch_with_flaresolverr(url, request.timeout_ms)
    return result


@app.get(
    "/health",
    summary="Health check",
    response_model=dict
)
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "flaresolverr_url": FLARESOLVERR_URL,
        "session": SESSION_NAME,
        "session_created": _session_created,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)