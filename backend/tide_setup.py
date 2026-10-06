"""Tide discovery and idempotent realm configuration used by embedded setup."""
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr
import httpx

from backend import tide_config

router = APIRouter(prefix='/api/service/tide')
_probe = (0, False)


def public_url():
    return tide_config.local_origin(os.environ.get('TIDECLOAK_PUBLIC_URL', 'http://localhost:8080').rstrip('/'))


def internal_url():
    return os.environ.get('TIDECLOAK_INTERNAL_URL', 'http://localhost:8080').rstrip('/')


def status():
    global _probe
    if time.monotonic() - _probe[0] > 5:
        try:
            r = httpx.get(internal_url() + '/realms/master/.well-known/openid-configuration', timeout=2,
                          follow_redirects=False, trust_env=False)
            reachable = r.status_code == 200 and 'authorization_endpoint' in r.json()
        except (httpx.HTTPError, ValueError):
            reachable = False
        _probe = (time.monotonic(), reachable)
    config = tide_config.load()
    return {'state': ('ready' if _probe[1] else 'unavailable') if config else ('needs-setup' if _probe[1] else 'stopped'),
            'configured': bool(config), 'reachable': _probe[1], 'url': public_url(),
            'managed': urlsplit(internal_url()).hostname == 'tidecloak'}


@router.get('/status')
def installation_status():
    return status()


@router.get('/config')
def runtime_config():
    config = tide_config.load()
    if not config:
        raise HTTPException(503, 'Secure history is not configured.')
    return config


class Configure(BaseModel):
    model_config = ConfigDict(extra='forbid')
    realm: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=1000)
    allow_registration: bool = False


class Pending(Exception):
    pass


class Admin:
    def __init__(self, client, realm):
        self.client, self.prefix = client, f'/admin/realms/{realm}'

    def call(self, method, path, *, expect_json=True, **kwargs):
        r = self.client.request(method, self.prefix + path, **kwargs)
        if r.status_code == 404 and method == 'GET':
            return None
        # TideCloak can lock an entity while another governed write is pending.
        # Report the real approval instead of turning that conflict into a 502.
        if r.status_code == 409 and method != 'GET':
            self.wait_for_approvals()
            # Embedded bootstrap can apply the blocking change immediately.
            # Retry the rejected write once; a persistent conflict must surface.
            r = self.client.request(method, self.prefix + path, **kwargs)
        if r.status_code not in range(200, 300):
            logging.getLogger(__name__).warning('Tide setup request failed: %s %s HTTP %s', method, path, r.status_code)
            raise HTTPException(502, f'TideCloak could not complete the setup step (HTTP {r.status_code}). Retry this step to resume your saved progress.')
        return r.json() if expect_json and r.content else None

    def wait_for_approvals(self, stage='realm changes'):
        pending = self.call('GET', '/iga/change-requests', params={'status': 'PENDING'})
        if pending is None:
            raise HTTPException(409, 'This server does not expose Tide governance. Check the TideCloak version.')
        if pending:
            raise Pending(f'{len(pending)} change request(s) need approval for {stage}. '
                          'Approve them in TideCloak, then return here and click Check approvals and continue.')

    def require_applied(self, condition, stage):
        if not condition:
            self.wait_for_approvals(stage)
            raise HTTPException(409, f'TideCloak did not confirm {stage}, but reports no pending approvals. '
                                'Setup could not finish. Check the realm configuration and retry.')


def client_matches(current, desired):
    """Keycloak represents redirect URIs and web origins as unordered sets."""
    if not isinstance(current, dict):
        return False
    for key, value in desired.items():
        actual = current.get(key)
        if key in {'redirectUris', 'webOrigins'}:
            if not isinstance(actual, list) or sorted(actual) != sorted(value):
                return False
        elif key == 'attributes':
            if not isinstance(actual, dict) or any(actual.get(k) != v for k, v in value.items()):
                return False
        elif actual != value:
            return False
    return True


def mapper_matches(current, desired):
    # TideCloak adds defaults such as introspection.token.claim on readback.
    # Compare required settings without treating those defaults as a change.
    if not isinstance(current, dict):
        return False
    return all(current.get(k) == v for k, v in desired.items() if k != 'config') and all(
        (current.get('config') or {}).get(k) == v for k, v in desired['config'].items())


def configure_realm(body, app_origin, frontend_dir, *, admin_factory=Admin, install=True, setup_callback=False, owner_auth=None):
    if body.realm == 'master':
        raise HTTPException(400, 'Create a dedicated Redacted realm in the realm wizard first.')
    with httpx.Client(base_url=internal_url(), timeout=45, trust_env=False, follow_redirects=False,
                      headers={'Host': public_url().split('://', 1)[1]}, auth=owner_auth) as client:
        if owner_auth is None:
            token = client.post('/realms/master/protocol/openid-connect/token', data={
                'client_id': 'admin-cli', 'grant_type': 'password', 'username': body.username,
                'password': body.password.get_secret_value(),
            })
            if token.status_code != 200:
                raise HTTPException(403, 'TideCloak rejected the local owner username or password. Run bash scripts/tidecloak.sh credentials in your Redacted project folder and copy those credentials. Use the owner password, not the setup code or your Tide account password.')
            client.headers['Authorization'] = 'Bearer ' + token.json()['access_token']
        a = admin_factory(client, body.realm)
        realm = a.call('GET', '')
        if not realm:
            raise HTTPException(409, 'Create and license the realm in the TideCloak wizard, then link your Tide admin account.')
        attrs = realm.get('attributes') or {}
        if attrs.get('isIGAEnabled') != 'true' or attrs.get('iga.attestor') != 'tide':
            raise HTTPException(409, 'Complete Tide realm setup with QEA enabled before connecting Redacted.')
        a.wait_for_approvals()
        # Creation and default-role grants are separate governed steps. Always
        # read back and stop for the owner; never sign or auto-approve requests.
        roles = ['_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']
        resolved = []
        for name in roles:
            role = a.call('GET', '/roles/' + name)
            if not role:
                a.call('POST', '/roles', json={'name': name})
                role = a.call('GET', '/roles/' + name)
            if role:
                resolved.append(role)
        a.wait_for_approvals('personal encryption roles')
        a.require_applied(len(resolved) == len(roles), 'personal encryption roles')
        default = realm.get('defaultRole')
        if not default or not default.get('id'):
            raise HTTPException(409, 'The realm default role is missing. Check Roles in the console.')
        associated = a.call('GET', '/roles-by-id/' + default['id'] + '/composites') or []
        missing = [r for r in resolved if r['id'] not in {x['id'] for x in associated}]
        if missing:
            a.call('POST', '/roles-by-id/' + default['id'] + '/composites', json=missing)
            a.wait_for_approvals('default history permissions')
            associated = a.call('GET', '/roles-by-id/' + default['id'] + '/composites') or []
            a.require_applied(set(roles).issubset({r['name'] for r in associated}), 'default history permissions')
        clients = a.call('GET', '/clients', params={'clientId': 'redacted'}) or []
        desired = {
            'clientId': 'redacted', 'name': 'Redacted', 'protocol': 'openid-connect',
            'enabled': True, 'publicClient': True, 'standardFlowEnabled': True,
            'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': False,
            'redirectUris': [app_origin + '/', app_origin + '/silent-check-sso.html'] +
                           ([app_origin + '/secure-history/setup', app_origin + '/secure-history/linked'] if setup_callback else []),
            'webOrigins': [app_origin], 'rootUrl': app_origin, 'baseUrl': app_origin + '/',
            'fullScopeAllowed': False,
            'attributes': {'pkce.code.challenge.method': 'S256', 'dpop.bound.access.tokens': 'true',
                           'post.logout.redirect.uris': app_origin + '/'},
        }
        if not clients:
            a.call('POST', '/clients', json=desired)
            a.wait_for_approvals('Redacted login client')
            clients = a.call('GET', '/clients', params={'clientId': 'redacted'}) or []
        a.require_applied(len(clients) == 1, 'Redacted login client')
        current = clients[0]
        if not client_matches(current, desired):
            a.call('PUT', '/clients/' + current['id'], json={**current, **desired,
                'attributes': {**current.get('attributes', {}), **desired['attributes']}})
            a.wait_for_approvals('Redacted login settings')
            check = a.call('GET', '/clients/' + current['id'])
            a.require_applied(client_matches(check, desired), 'Redacted login settings')
        cid = current['id']
        mappings = a.call('GET', '/clients/' + cid + '/protocol-mappers/models') or []
        audience = {'name': 'redacted-api-audience', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-audience-mapper',
                    'config': {'included.client.audience': 'redacted', 'id.token.claim': 'false', 'access.token.claim': 'true'}}
        existing = next((m for m in mappings if m['name'] == audience['name']), None)
        if existing is None:
            a.call('POST', '/clients/' + cid + '/protocol-mappers/models', json=audience)
        elif not mapper_matches(existing, audience):
            a.call('PUT', '/clients/' + cid + '/protocol-mappers/models/' + existing['id'], json={**existing, **audience, 'config': {**existing.get('config', {}), **audience['config']}})
        # These settings are independent once the client and roles exist.
        # Submit them together, then check actual approvals and applied values.
        scoped = a.call('GET', '/clients/' + cid + '/scope-mappings/realm') or []
        missing_scope = [role for role in resolved if role['id'] not in {r['id'] for r in scoped}]
        if missing_scope:
            a.call('POST', '/clients/' + cid + '/scope-mappings/realm', json=missing_scope)
        if bool(realm.get('registrationAllowed')) != body.allow_registration:
            a.call('PUT', '', json={'registrationAllowed': body.allow_registration})
        a.wait_for_approvals('login token permissions and sign-up settings')
        check = a.call('GET', '/clients/' + cid + '/protocol-mappers/models') or []
        a.require_applied(any(mapper_matches(m, audience) for m in check), 'login token audience')
        scoped = a.call('GET', '/clients/' + cid + '/scope-mappings/realm') or []
        a.require_applied(set(roles).issubset({r['name'] for r in scoped}), 'login token role permissions')
        a.require_applied(bool(a.call('GET', '').get('registrationAllowed')) == body.allow_registration, 'sign-up settings')
        branding = {}
        for kind, filename, field in [('LOGO', 'redacted-logo_stacked.jpg', 'logoUrl'),
                                      ('BACKGROUND_IMAGE', 'redacted-wallpaper.jpg', 'backgroundUrl')]:
            data = (frontend_dir / 'brand' / filename).read_bytes()
            if not data.startswith(b'\xff\xd8\xff') or len(data) > 5 * 1024 * 1024:
                raise HTTPException(409, 'Enclave branding requires JPEG files smaller than 5 MB.')
            uploaded = a.call('POST', '/tide-idp-admin-resources/images/upload',
                              files={'fileData': (filename, data, 'image/jpeg')},
                              data={'fileName': filename, 'fileType': kind})
            image_hash = uploaded.get('hash', '')
            if not re.fullmatch('[a-fA-F0-9]{64}', image_hash):
                raise HTTPException(502, 'TideCloak returned an invalid branding image reference.')
            branding[field] = f'{public_url()}/realms/{body.realm}/tide-idp-resources/images/{kind}?v={image_hash}'
            served = client.get(f'/realms/{body.realm}/tide-idp-resources/images/{kind}', params={'v': image_hash})
            if served.status_code != 200 or served.content != data:
                raise HTTPException(502, 'The uploaded enclave image could not be verified.')
        a.call('POST', '/vendorResources/set-branding', json=branding, expect_json=False)
        saved = a.call('GET', '/vendorResources/get-branding')
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in branding.items()):
            raise HTTPException(502, 'Enclave branding was not confirmed. Check TideCloak and retry.')
        # Re-sign after configuring client origins, even if branding already signed.
        a.call('POST', '/vendorResources/sign-idp-settings', expect_json=False)
        adapter = a.call('GET', '/vendorResources/get-installations-provider', params={
            'clientId': cid, 'providerId': 'keycloak-oidc-keycloak-json'})
        config = tide_config.validate(adapter, app_origin)
        if config['issuer'] != f'{public_url()}/realms/{body.realm}':
            raise HTTPException(409, 'TideCloak exported a different public issuer. Check its hostname configuration.')
        if install:
            tide_config.install(config)
            return {'state': 'configured', 'message': 'Realm connected. Sign in and test encrypted history.'}
        return config
