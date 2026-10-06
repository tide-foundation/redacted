"""Locally installed public Tide adapter; never contains admin or decryption keys."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def directory():
    return Path(os.environ.get('PRIVACY_DATA_DIR', './data')).resolve() / 'tide'


def local_origin(value):
    if not isinstance(value, str):
        raise ValueError('Use an exact local HTTP origin.')
    p = urlsplit(value)
    if (p.scheme not in {'http', 'https'} or p.hostname not in {'localhost', '127.0.0.1'}
            or p.netloc != p.hostname + (f':{p.port}' if p.port else '') or p.path or p.query or p.fragment):
        raise ValueError('Use an exact local HTTP origin.')
    return value


def validate(adapter, app_origin):
    local_origin(app_origin)
    if not isinstance(adapter, dict):
        raise ValueError('Invalid Tide adapter.')
    # Only public adapter fields are allowed to reach the browser. A confidential
    # client export must never be silently exposed as a public SPA configuration.
    if any(k in adapter for k in ('credentials', 'client-secret', 'clientSecret')):
        raise ValueError('Use a public client adapter without credentials.')
    origin = local_origin(str(adapter.get('auth-server-url', '')).rstrip('/'))
    realm, client = adapter.get('realm'), adapter.get('resource')
    if not isinstance(realm, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', realm) or realm == 'master':
        raise ValueError('Choose a dedicated Redacted realm.')
    if client != 'redacted':
        raise ValueError('The adapter must use the redacted public client.')
    jwks = adapter.get('jwk')
    if isinstance(jwks, str):
        jwks = json.loads(jwks)
    if not isinstance(jwks, dict) or not isinstance(jwks.get('keys'), list) or not jwks['keys']:
        raise ValueError('The adapter needs trusted Tide signing keys.')
    for key in jwks['keys']:
        if 'd' in key or key.get('kty') != 'OKP' or key.get('crv') != 'Ed25519' or not key.get('x'):
            raise ValueError('Only public Ed25519 Tide signing keys are accepted.')
    if not isinstance(adapter.get('client-origin-auth-' + app_origin), str) or not adapter.get('client-origin-auth-' + app_origin):
        raise ValueError('The app origin must be signed by TideCloak before connecting.')
    if not adapter.get('vendorId') or not adapter.get('homeOrkUrl'):
        raise ValueError('The realm must be licensed and Tide-enabled.')
    public = {k: v for k, v in adapter.items() if k in {
        'realm', 'auth-server-url', 'resource', 'public-client', 'ssl-required',
        'confidential-port', 'vendorId', 'homeOrkUrl', 'jwk', 'realm-public-key', 'backgroundUrl', 'logoUrl',
    } or k.startswith('client-origin-auth-')}
    public['auth-server-url'] = origin + '/'
    public['jwk'] = jwks
    return {'version': 1, 'app_origin': app_origin, 'issuer': f'{origin}/realms/{realm}',
            'client_id': client, 'adapter': public}


def load():
    path = directory() / 'config.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return validate(data['adapter'], data['app_origin'])
    except (ValueError, KeyError, TypeError):
        # Invalid configuration disables protected operations, never guest mode.
        return None


def install(config):
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'config.json'
    temporary = root / 'config.tmp'
    with open(temporary, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
        json.dump(config, out)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)
