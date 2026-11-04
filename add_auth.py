with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add API_KEY after SESSION_NAME
content = content.replace(
    'SESSION_NAME = "rr_session"',
    'SESSION_NAME = "rr_session"\n\n# API Key authentication\nAPI_KEY = os.getenv("API_KEY", "MfN4l9DjJ7OBrXHjJodjUdKToE0hJ8O_RYKI8t-b-0A")'
)

# 2. Update imports to add Depends, Header
content = content.replace(
    'from fastapi import FastAPI, HTTPException, Request, status',
    'from fastapi import FastAPI, HTTPException, Request, status, Depends, Header'
)

# 3. Add verify_api_key after pydantic import
content = content.replace(
    'from pydantic import BaseModel, HttpUrl, Field',
    '''from pydantic import BaseModel, HttpUrl, Field

# API Key verification dependency
async def verify_api_key(x_api_key: str = Header(None, alias="X-API-Key")):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"}
        )'''
)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Step 1-3 done')