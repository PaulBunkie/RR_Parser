with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 4. Protect /fetch endpoint
content = content.replace(
    '@app.post(\n    "/fetch",\n    summary="Fetch chapter text",\n    response_model=FetchSuccessResponse,\n    responses={\n        200: {"model": FetchSuccessResponse, "description": "Chapter fetched successfully"},\n        400: {"model": ErrorResponse, "description": "Validation Error"},\n        408: {"model": ErrorResponse, "description": "Request Timeout"},\n        502: {"model": ErrorResponse, "description": "Upstream Unavailable"},\n        503: {"model": ErrorResponse, "description": "Service Unavailable (Challenge/Session)"},\n        504: {"model": ErrorResponse, "description": "Upstream Timeout"},\n    }\n)\nasync def fetch_chapter(',
    '''@app.post(
    "/fetch",
    summary="Fetch chapter text",
    response_model=FetchSuccessResponse,
    dependencies=[Depends(verify_api_key)],
    responses={
        200: {"model": FetchSuccessResponse, "description": "Chapter fetched successfully"},
        400: {"model": ErrorResponse, "description": "Validation Error"},
        401: {"model": ErrorResponse, "description": "Unauthorized - Invalid API Key"},
        408: {"model": ErrorResponse, "description": "Request Timeout"},
        502: {"model": ErrorResponse, "description": "Upstream Unavailable"},
        503: {"model": ErrorResponse, "description": "Service Unavailable (Challenge/Session)"},
        504: {"model": ErrorResponse, "description": "Upstream Timeout"},
    }
)
async def fetch_chapter(''')

# 5. Protect /health endpoint
content = content.replace(
    '@app.get(\n    "/health",\n    summary="Health check",\n    response_model=dict\n)\nasync def health(',
    '''@app.get(
    "/health",
    summary="Health check",
    response_model=dict,
    dependencies=[Depends(verify_api_key)]
)
async def health(''')

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Step 4-5 done')