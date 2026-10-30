import flaresolverr
import os
print(flaresolverr.__file__)
src_dir = os.path.dirname(flaresolverr.__file__)
for root, dirs, files in os.walk(src_dir):
    for f in files:
        if f.endswith('.py'):
            print(os.path.join(root, f))