import os, sys, time, socket, subprocess, webbrowser, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOG = BASE / 'server.log'

def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(('127.0.0.1', port)) != 0

def health_ok(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1.0) as r:
            import json
            data=json.loads(r.read().decode('utf-8'))
            return r.status == 200 and data.get('version') == '2.7'
    except Exception:
        return False

# Always start this build on a free port so an older Harmonizer cannot be reused.
# Chrome blocks some ports (notably 5060/5061 as SIP ports).
# Use only browser-safe local development ports.
SAFE_PORTS = list(range(5070, 5100)) + list(range(5055, 5060))
port = next((p for p in SAFE_PORTS if port_free(p)), None)
if port is None:
    print('ERROR: No browser-safe local port is available (5055-5059 or 5070-5099).')
    input('Press Enter to close...')
    sys.exit(1)

env = os.environ.copy()
env['PORT'] = str(port)
creationflags = 0
if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
    creationflags = subprocess.CREATE_NO_WINDOW

with open(LOG, 'w', encoding='utf-8', errors='replace') as log:
    proc = subprocess.Popen(
        [sys.executable, str(BASE / 'app.py')],
        cwd=str(BASE), env=env,
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

for _ in range(40):
    if proc.poll() is not None:
        break
    if health_ok(port):
        url = f'http://127.0.0.1:{port}'
        webbrowser.open(url + '/?build=2.7')
        print(f'Website started successfully: {url}')
        print('You may close this window. The website will keep running.')
        sys.exit(0)
    time.sleep(0.5)

print('\nSERVER FAILED TO START. Error details:\n')
try:
    print(LOG.read_text(encoding='utf-8', errors='replace'))
except Exception as e:
    print(f'Could not read server.log: {e}')
print(f'\nLog file: {LOG}')
input('\nPress Enter to close...')
sys.exit(1)
