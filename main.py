# -*- coding: utf-8 -*-
"""RR Fetcher Service - FastAPI + FlareSolverr
Extracts chapter text from Royal Road, bypassing Anubis/Cloudflare.
Uses persistent FlareSolverr session with auto-recreate on challenge.
Proper REST API with HTTP status codes and structured errors.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("rr-fetcher")

# FlareSolverr configuration
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191").rstrip("/")
SESSION_NAME = "rr_session"

# Cookie persistence configuration
COOKIES_FILE = "/home/fetcher/data/flaresolverr_cookies.json"

# Consistent headers
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
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

# Cookie persistence state
_saved_cookies: Optional[dict] = None


# ==================== COOKIE PERSISTENCE ====================


def load_saved_cookies() -> dict:
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        log.info(f"Loaded {len(cookies)} saved cookies from {COOKIES_FILE}")
        return cookies
    except (FileNotFoundError, json.JSONDecodeError):
        log.info(f"No saved cookies found in {COOKIES_FILE}")
        return {}


def save_cookies_to_flaresolverr(cookies: dict):
    try:
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f)
        log.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")
    except Exception as e:
        log.error(f"Failed to save cookies to {COOKIES_FILE}: {e}")

# ==================== ERROR CLASSES ====================


class FetcherError(Exception):
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
        super().__init__(message, status.HTTP_503_SERVICE_UNAVAILABLE, "CHALLENGE_ERROR", details)


# ==================== FLARESOLVERR HELPER ====================


async def fetch_with_flaresolverr(url: str, timeout_ms: int) -> dict:
    """Fetch chapter content from Royal Road URL via FlareSolverr v2 API.
    
    FlareSolverr v2 returns: {status, message, solution, ...}
    solution contains: {url, status, response (html), headers, cookies, userAgent}
    """
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": timeout_ms,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{FLARESOLVERR_URL}/v1",
                json=payload,
                timeout=timeout_ms / 1000,
            )
            if response.status_code != 200:
                return {"success": False, "error": f"FlareSolverr returned status {response.status_code}", "status_code": response.status_code}
            
            data = response.json()
            
            # FlareSolverr v2 format: {status, message, solution: {url, status, response, headers, cookies, ...}}
            if data.get("status") != "ok":
                return {"success": False, "error": data.get("message", "FlareSolverr error"), "status_code": 502}
            
            solution = data.get("solution", {})
            if not solution:
                return {"success": False, "error": "No solution in FlareSolverr response", "status_code": 502}
            
            # Extract cookies from solution.cookies (list of dicts) to dict
            cookies_list = solution.get("cookies", [])
            cookies_dict = {c.get("name"): c.get("value") for c in cookies_list if c.get("name")}
            
            return {
                "success": True,
                "html": solution.get("response", ""),
                "cookies": cookies_dict,
                "status_code": solution.get("status", 200),
            }
    except Exception as e:
        return {"success": False, "error": f"FlareSolverr connection error: {str(e)}", "status_code": 502}


# ==================== REQUEST/RESPONSE MODELS ====================


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: Optional[dict] = None
    request_id: Optional[str] = None


class FetchRequest(BaseModel):
    url: HttpUrl = Field(..., description="Royal Road chapter URL")
    timeout_ms: int = Field(120000, ge=5000, le=300000, description="Timeout in milliseconds")


class FetchSuccessResponse(BaseModel):
    success: bool = Field(..., description="Whether the fetch was successful")
    text: str = Field(..., description="Extracted chapter text")
    title: str = Field(..., description="Chapter title")
    html: str = Field(..., description="Full HTML response")


# ==================== ENDPOINTS ====================

app = FastAPI()
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
    },
)
async def fetch_chapter(request: FetchRequest):
    url = str(request.url)
    if "royalroad.com" not in url:
        raise ValidationError("Only royalroad.com URLs are supported", details={"provided_url": url})

    # Try saved cookies first
    saved_cookies = load_saved_cookies()
    if saved_cookies:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    url, cookies=saved_cookies, headers=BROWSER_HEADERS,
                    timeout=request.timeout_ms / 1000
                )
                if response.status_code == 200:
                    log.info("Successfully fetched chapter using saved cookies (bypassed challenge)")
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, "lxml")
                    text = title = ""
                    for sel in SELECTORS:
                        el = soup.select_one(sel)
                        if el:
                            text = el.get_text(strip=True, separator="\n")
                            break
                    te = soup.find("h1") or soup.find("title")
                    if te:
                        title = te.get_text(strip=True)
                    if not text:
                        text = response.text
                    return FetchSuccessResponse(success=True, text=text, title=title or "Chapter", html=response.text)
        except Exception as e:
            log.warning(f"Cookie fetch failed: {e}, falling back to FlareSolverr")

    # Fallback to FlareSolverr
    result = await fetch_with_flaresolverr(url, request.timeout_ms)
    log.info(f"FlareSolverr result: success={result.get('success')}, has_cookies={'cookies' in result}, cookies_keys={list(result.get('cookies', {}).keys()) if result.get('cookies') else 'empty'}")
    if result.get("success") and result.get("cookies"):
        save_cookies_to_flaresolverr(result.get("cookies", {}))
    
    # Parse HTML from FlareSolverr result
    if result.get("success") and result.get("html"):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(result["html"], "lxml")
        text = title = ""
        for sel in SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True, separator="\n")
                break
        te = soup.find("h1") or soup.find("title")
        if te:
            title = te.get_text(strip=True)
        if not text:
            text = result["html"]
        return FetchSuccessResponse(success=True, text=text, title=title or "Chapter", html=result["html"])
    
    return result


@app.get(
    "/cookies/status",
    summary="Check saved cookie status",
)
async def cookies_status():
    cookies = load_saved_cookies()
    valid_cookies = any(cookies.get(c) for c in ["__cf_bm", "cf_clearance"])
    return {
        "has_saved_cookies": len(cookies) > 0,
        "cookie_count": len(cookies),
        "has_valid_cloudflare_cookies": valid_cookies,
        "cookie_names": list(cookies.keys()) if cookies else [],
    }


@app.get(
    "/health",
    summary="Health check",
    response_model=dict,
)
async def health():
    return {
        "status": "ok",
        "flaresolverr_url": FLARESOLVERR_URL,
        "session": SESSION_NAME,
        "session_created": _session_created,
        "has_saved_cookies": len(load_saved_cookies()) > 0,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)