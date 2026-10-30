import urllib.request
import json

# Check FlareSolverr root
try:
    with urllib.request.urlopen('http://localhost:8191/', timeout=10) as r:
        print("Root:", r.read().decode()[:500])
except Exception as e:
    print("Root Error:", e)

# Check v1
try:
    with urllib.request.urlopen('http://localhost:8191/v1', timeout=10) as r:
        print("v1:", r.read().decode()[:500])
except Exception as e:
    print("v1 Error:", e)

# Check health
try:
    with urllib.request.urlopen('http://localhost:8191/health', timeout=10) as r:
        print("health:", r.read().decode()[:500])
except Exception as e:
    print("health Error:", e)