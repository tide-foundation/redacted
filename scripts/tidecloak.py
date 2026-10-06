#!/usr/bin/env python3
"""Start TideCloak or connect an existing instance, then open authorized setup."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.tidecloak'
ENV = PRIVATE / 'bootstrap.env'


def write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as out:
        out.write(value)


def compose(*args):
    subprocess.run(['docker', 'compose', '--profile', 'secure-history', *args], cwd=ROOT, check=True)


def credentials():
    if not ENV.exists():
        write_private(ENV, 'KC_BOOTSTRAP_ADMIN_USERNAME=redacted-owner\nKC_BOOTSTRAP_ADMIN_PASSWORD=' + secrets.token_urlsafe(32) + '\n')


def read_env(path):
    return dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#') and '=' in line)


def call(origin, path, body=None, *, timeout=20):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            return None
    request = urllib.request.Request(origin + '/api/service/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def wait_for_app(origin, timeout=30):
    """Retry only discovery while Docker is starting/recreating the app."""
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f'Redacted is not ready at {origin}. Start the app with docker compose up -d app, then retry this command.')
        try:
            return call(origin, 'tide/status', timeout=min(5, remaining))
        except urllib.error.HTTPError as error:
            if error.code not in (502, 503, 504):
                raise
            error.close()
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        if not announced:
            print('Waiting for Redacted to become ready (up to 30 seconds)…', flush=True)
            announced = True
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def open_setup_browser(url):
    """Use the host browser on WSL; never interpolate the private URL as code."""
    try:
        wsl = bool(os.environ.get('WSL_DISTRO_NAME')) or 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower()
    except OSError:
        wsl = False
    commands = []
    if wsl:
        if executable := shutil.which('wslview'):
            commands.append(([executable, url], None))
        executable = shutil.which('powershell.exe')
        if not executable:
            candidate = Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
            if candidate.exists():
                executable = str(candidate)
        if executable:
            commands.append(([executable, '-NoProfile', '-NonInteractive', '-Command',
                '$ErrorActionPreference = "Stop"; $url = [Console]::In.ReadToEnd(); Start-Process -FilePath $url'], url))
    elif sys.platform == 'darwin':
        commands.append((['open', url], None))
    elif sys.platform.startswith('linux') and (executable := shutil.which('xdg-open')):
        commands.append(([executable, url], None))
    else:
        try:
            return webbrowser.open(url)
        except (webbrowser.Error, OSError):
            return False
    for command, data in commands:
        try:
            result = subprocess.run(command, input=data, text=True, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def handoff(origin, data_root, username, password, open_browser=True, public_origin=None):
    # The permit is a random digest only. The admin password travels directly to
    # the local backend, lives in RAM for at most an hour, and never enters a URL.
    token = secrets.token_urlsafe(32)
    path = data_root / 'tide/launch-permit.json'
    write_private(path, json.dumps({'digest': hashlib.sha256(token.encode()).hexdigest(), 'expires': time.time() + 60}))
    try:
        result = call(origin, 'tide/setup/v2/launch', {'permit': token, 'username': username, 'password': password})
    finally:
        path.unlink(missing_ok=True)
    if public_origin:
        from urllib.parse import urlsplit
        link = urlsplit(result['url'])
        result['url'] = public_origin + link.path + '#' + link.fragment
    print('\nContinue in Redacted. This private setup link works once and expires in one hour:\n' + result['url'])
    if open_browser and not open_setup_browser(result['url']):
        print('Could not open a browser automatically. Open a browser on this computer and paste the complete private setup link above, including #setup= and everything after it.')
    return result['url']


def check_port(port):
    running = subprocess.check_output(['docker', 'compose', '--profile', 'secure-history', 'ps', '--status', 'running', '-q', 'tidecloak'], cwd=ROOT, text=True).strip()
    if running:
        return
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'Port {port} is already in use. Nothing was stopped. To use that TideCloak server, run python3 scripts/tidecloak.py connect. For a new installation on another port, set TIDECLOAK_PORT in .env before starting.') from None


def main():
    settings = {**(read_env(ROOT / '.env') if (ROOT / '.env').exists() else {}), **os.environ}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'start', 'connect', 'stop', 'credentials', 'logs'])
    parser.add_argument('--app-url', default=settings.get('REDACTED_URL', 'http://localhost:' + settings.get('REDACTED_PORT', '3001')))
    parser.add_argument('--data-dir', type=Path, default=ROOT / settings.get('REDACTED_DATA_DIR', 'data'), help='Must match the running app’s data directory')
    parser.add_argument('--container', help='For connect: read bootstrap credentials from this existing Docker container')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    from urllib.parse import urlsplit
    origin = args.app_url.rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1') or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('--app-url must be a local app origin, such as http://localhost:3001')
    try:
        if args.action == 'prepare':
            credentials(); compose('pull', 'tidecloak'); compose('create', 'tidecloak')
            print('TideCloak is installed and stopped. Start with: python3 scripts/tidecloak.py start')
        elif args.action in ('start', 'connect'):
            # Check the app first so a port collision never starts a different
            # service or leaves the user staring at a dead setup link.
            installation = wait_for_app(origin)
            if args.action == 'start':
                check_port(int(settings.get('TIDECLOAK_PORT', '8080')))
                credentials(); compose('up', '-d', 'tidecloak')
                values = read_env(ENV)
            elif installation['configured']:
                print('Secure history is already configured. Open ' + origin + '/secure-history/setup')
                return
            elif args.container:
                c = json.loads(subprocess.check_output(['docker', 'inspect', args.container]))[0]
                values = dict(v.split('=', 1) for v in c['Config']['Env'] if '=' in v)
            else:
                values = {'KC_BOOTSTRAP_ADMIN_USERNAME': input('TideCloak owner username: '),
                          'KC_BOOTSTRAP_ADMIN_PASSWORD': getpass.getpass('TideCloak owner password: ')}
            if installation['configured']:
                print('Secure history is already configured. Open ' + origin + '/secure-history/setup')
                return
            username, password = values.get('KC_BOOTSTRAP_ADMIN_USERNAME'), values.get('KC_BOOTSTRAP_ADMIN_PASSWORD')
            if not username or not password:
                raise RuntimeError('No bootstrap credentials found. Use connect without --container to enter the current owner credentials.')
            handoff(origin, args.data_dir.resolve(), username, password, not args.no_browser)
        elif args.action == 'credentials':
            if not ENV.exists():
                parser.error('Run prepare or start first.')
            print(ENV.read_text().replace('KC_BOOTSTRAP_ADMIN_USERNAME=', 'Username: ').replace('KC_BOOTSTRAP_ADMIN_PASSWORD=', 'Password: '))
            print('Keep these owner credentials private.')
        elif args.action == 'stop':
            compose('stop', 'tidecloak')
        elif args.action == 'logs':
            compose('logs', '--tail=80', 'tidecloak')
    except urllib.error.HTTPError as e:
        try: message = json.load(e).get('detail', 'Redacted rejected the setup request.')
        except (ValueError, AttributeError): message = 'Redacted rejected the setup request. Rebuild it with the latest version.'
        parser.exit(1, str(message) + '\n')
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        parser.exit(1, f'The connection to Redacted at {origin} was interrupted. Wait for the app to finish starting, then rerun this command for a fresh setup link.\n')
    except subprocess.CalledProcessError:
        parser.exit(1, 'Docker could not complete the command. Check Docker Desktop and the container name, then retry.\n')
    except (RuntimeError, OSError, ValueError) as e:
        parser.exit(1, str(e) + '\n')


if __name__ == '__main__':
    main()
