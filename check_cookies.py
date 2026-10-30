import os
for root, dirs, files in os.walk('/'):
    for f in files:
        if 'flaresolverr' in f:
            print(os.path.join(root, f))

# Also check working directory
print("CWD:", os.getcwd())
print("Files in /app:", os.listdir('/app'))