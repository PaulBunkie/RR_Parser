with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old2 = open('debug_old2.txt', 'r', encoding='utf-8').read()
import ast
old2 = ast.literal_eval(old2.strip())

new2 = '''async def do_request() -> dict:
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
    except SessionError:
        log.warning("Session not found, recreating...")
        await _session_manager._destroy_session_internal()
        await _session_manager._create_session_internal()
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
    if isinstance(cookies_list, list):
        cookies = {c.get("name"): c.get("value") for c in cookies_list if c.get("name")}
    else:
        cookies = cookies_list if isinstance(cookies_list, dict) else {}
    if cookies:
        save_cookies_to_flaresolverr(cookies)
    
    return {"success": True, "text": text, "title": title, "html": html}
'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Replacement 2 OK')
else:
    print('OLD2 NOT FOUND')
    idx = content.find('async def do_request() -> dict:')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx:idx+200]))

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)