import json
import urllib.request
import urllib.error

data = json.dumps({
    'url': 'https://www.royalroad.com/fiction/21220/mother-of-learning/chapter/308538/1',
    'timeout_ms': 120000
}).encode()

req = urllib.request.Request(
    'http://localhost:5555/fetch',
    data=data,
    headers={'Content-Type': 'application/json'},
    method='POST'
)

try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = resp.read().decode()
        print(result[:1000])
except urllib.error.HTTPError as e:
    print('HTTP Error:', e.code, e.read().decode()[:1000])
except Exception as e:
    print('Error:', e)