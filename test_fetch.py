import json
import time
import urllib.request
import urllib.error

data = json.dumps({
    'url': 'https://www.royalroad.com/fiction/198845/baby-archmage-opmc-litrpg-reincarnator-no-ai-writing',
    'timeout_ms': 120000
}).encode()

req = urllib.request.Request(
    'http://localhost:5555/fetch',
    data=data,
    headers={
        'Content-Type': 'application/json',
        'X-API-Key': 'MfN4l9DjJ7OBrXHjJodjUdKToE0hJ8O_RYKI8t-b-0A'
    },
    method='POST'
)

start = time.time()
print("Sending fetch request for second fiction...")
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = resp.read().decode()
        duration = time.time() - start
        print(f"Status: {resp.status} (took {duration:.1f}s)")
        res_json = json.loads(result)
        print("Success:", res_json.get("success"))
        print("Title:", res_json.get("title"))
        print("Text length:", len(res_json.get("text", "")))
        print("Snippet:", res_json.get("text", "")[:300])
except urllib.error.HTTPError as e:
    duration = time.time() - start
    print(f"HTTP Error {e.code} (took {duration:.1f}s):", e.read().decode()[:1000])
except Exception as e:
    duration = time.time() - start
    print(f"Error (took {duration:.1f}s):", e)
