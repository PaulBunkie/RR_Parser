import urllib.request, json, time
url = 'https://www.royalroad.com/fiction/63759/super-supportive/chapter/3532530/two-hundred-ninety-two-when-wishing-was-having'
data = json.dumps({'url': url, 'timeout_ms': 120000}).encode()
for i in range(1, 11):
    req = urllib.request.Request('http://94.118.23.18:5555/fetch', data=data, headers={'Content-Type': 'application/json', 'X-API-Key': 'MfN4l9DjJ7OBrXHjJodjUdKToE0hJ8O_RYKI8t-b-0A'})
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=180)
        body = resp.read().decode()
        print(f'апрос {i}: HTTP {resp.status} ремя: {time.time()-start:.1f}s лина: {len(body)}')
    except Exception as e:
        print(f'апрос {i}: ERROR {type(e).__name__} {str(e)[:100]}')
