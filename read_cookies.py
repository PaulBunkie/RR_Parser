import json
import os

path = '/app/flaresolverr_cookies.json'
if os.path.exists(path):
    with open(path, 'r') as f:
        print(f.read())
else:
    print("File does not exist at", path)