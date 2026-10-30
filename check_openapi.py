import urllib.request
import json

# Check OpenAPI/docs
for path in ['/docs', '/openapi.json', '/swagger.json', '/api/docs', '/api/openapi.json']:
    try:
        with urllib.request.urlopen(f'http://localhost:8191{path}', timeout=10) as r:
            result = r.read().decode()
            print(f"{path}: {result[:500]}")
            break
    except Exception as e:
        print(f"{path}: {e}")

# Also try to get all routes
try:
    with urllib.request.urlopen('http://localhost:8191/', timeout=10) as r:
        print("Root full:", r.read().decode())
except Exception as e:
    print("Root error:", e)