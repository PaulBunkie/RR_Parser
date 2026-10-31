with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()

old4 = '"session_created": _session_created,'
new4 = '"session_created": _session_manager.session_created if _session_manager else False,'

if old4 in content:
    content = content.replace(old4, new4)
    print('Replacement 4 OK')
else:
    print('OLD4 NOT FOUND')
    idx = content.find('session_created')
    if idx >= 0:
        print('Found at:', idx)
        print(repr(content[idx-50:idx+50]))

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(content)