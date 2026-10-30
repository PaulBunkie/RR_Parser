import urllib.request
import json

data = json.dumps({
    'url': 'https://www.royalroad.com/fiction/21220/mother-of-learning/chapter/308538/1',
    'max_timeout': 60000
}).encode()

# Test /v1/resolve
for path in ['/v1/resolve', '/v1/fetch', '/resolve', '/fetch']:
    try:
        req = urllib.request.Request(
            f'http://localhost:8191{path}',
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            result = r.read().decode()
            print(f"{path}: {result[:500]}")
            break
    except urllib.error.HTTPError as e:
        print(f"{path}: HTTP {e.code} - {e.read().decode()[:200]}")
    except Exception as e:
        print(f"{path}: Error: {e}")