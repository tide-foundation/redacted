#!/usr/bin/env python3
"""Container-side owner handoff; the host needs only Bash and Docker."""
import json
import os
from pathlib import Path
import secrets
import sys

from tidecloak import handoff, wait_for_app


def main():
    action = sys.argv[1]
    if action == 'generate':
        print('KC_BOOTSTRAP_ADMIN_USERNAME=redacted-owner')
        print('KC_BOOTSTRAP_ADMIN_PASSWORD=' + secrets.token_urlsafe(32))
        return
    origin = 'http://127.0.0.1:8000'
    public = os.environ.get('REDACTED_PUBLIC_URL', 'http://localhost:3001')
    status = wait_for_app(origin)
    if action == 'status':
        print('managed' if status.get('managed') else 'external-configured' if status['configured'] else 'external')
        return
    if status['configured']:
        print('Secure history is already configured.\n' + public + '/secure-history/setup')
        return
    if action == 'container-env':
        entries = json.load(sys.stdin)[0]['Config']['Env']
    elif action == 'env':
        entries = sys.stdin.read().splitlines()
    else:
        raise ValueError('Unknown credential input format')
    values = dict(line.split('=', 1) for line in entries if '=' in line)
    username = values.get('KC_BOOTSTRAP_ADMIN_USERNAME')
    password = values.get('KC_BOOTSTRAP_ADMIN_PASSWORD')
    if not username or not password:
        raise ValueError('No bootstrap credentials found. Run bash scripts/tidecloak.sh connect to enter current credentials.')
    handoff(origin, Path(os.environ.get('PRIVACY_DATA_DIR', '/app/data')),
            username, password, open_browser=False, public_origin=public)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print request bodies or credentials on failed handoffs.
        print(f'Setup failed: {error}', file=sys.stderr)
        sys.exit(1)
