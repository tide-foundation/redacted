"""Regression checks for resuming approved setup against TideCloak responses."""
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
import jwt

from backend import tide_config, tide_setup

ORIGIN = 'http://localhost:3001'
NAMES = ['_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']


@pytest.fixture
def setup_server(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('TIDECLOAK_INTERNAL_URL', 'http://localhost:8080')
    monkeypatch.setenv('TIDECLOAK_PUBLIC_URL', 'http://localhost:8080')
    roles = [{'id': 'role-' + str(i), 'name': name} for i, name in enumerate(NAMES)]
    client = {'id': 'client-id', 'clientId': 'redacted', 'name': 'Redacted', 'protocol': 'openid-connect',
              'enabled': True, 'publicClient': True, 'standardFlowEnabled': True,
              'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': False,
              'redirectUris': [ORIGIN + '/silent-check-sso.html', ORIGIN + '/'],
              'webOrigins': [ORIGIN], 'rootUrl': ORIGIN, 'baseUrl': ORIGIN + '/', 'fullScopeAllowed': False,
              'attributes': {'pkce.code.challenge.method': 'S256', 'dpop.bound.access.tokens': 'true',
                             'post.logout.redirect.uris': ORIGIN + '/', 'server.extra': 'retained'}}
    audience = {'id': 'mapper-id', 'name': 'redacted-api-audience', 'protocol': 'openid-connect',
                'protocolMapper': 'oidc-audience-mapper', 'config': {'included.client.audience': 'redacted',
                'id.token.claim': 'false', 'access.token.claim': 'true',
                'introspection.token.claim': 'true', 'userinfo.token.claim': 'false'}}
    realm = {'attributes': {'isIGAEnabled': 'true', 'iga.attestor': 'tide'},
             'defaultRole': {'id': 'default-role'}, 'registrationAllowed': True}
    state = {'client': client, 'realm': realm, 'scoped': roles.copy(), 'mappers': [audience],
             'pending': [], 'govern_writes': False, 'writes': []}
    brand = tmp_path / 'brand'; brand.mkdir()
    image_bytes = b'\xff\xd8\xff-test-brand'
    for name in ['redacted-logo_stacked.jpg', 'redacted-wallpaper.jpg']:
        (brand / name).write_bytes(image_bytes)
    signing = ed25519.Ed25519PrivateKey.generate()
    jwk = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(signing.public_key()))
    adapter = {'auth-server-url': 'http://localhost:8080/', 'realm': 'test-realm', 'resource': 'redacted',
               'vendorId': 'fixture', 'homeOrkUrl': 'https://example.test', 'jwk': {'keys': [jwk]},
               'client-origin-auth-' + ORIGIN: 'fixture-signed-origin'}

    def handle(request):
        path = request.url.path.removeprefix('/admin/realms/test-realm')
        if request.method != 'GET':
            state['writes'].append((request.method, path))
        if path == '/realms/master/protocol/openid-connect/token':
            return httpx.Response(200, json={'access_token': 'fixture-token'})
        if request.method == 'GET':
            if path == '/iga/change-requests': return httpx.Response(200, json=state['pending'])
            if path == '': return httpx.Response(200, json=realm)
            if path.startswith('/roles/'): return httpx.Response(200, json=next(r for r in roles if r['name'] == path.split('/')[-1]))
            if path == '/roles-by-id/default-role/composites': return httpx.Response(200, json=roles)
            if path == '/clients': return httpx.Response(200, json=[client])
            if path == '/clients/client-id': return httpx.Response(200, json=client)
            if path.endswith('/protocol-mappers/models'): return httpx.Response(200, json=state['mappers'])
            if path.endswith('/scope-mappings/realm'): return httpx.Response(200, json=state['scoped'])
            if path == '/vendorResources/get-branding': return httpx.Response(200, json=state['branding'])
            if path == '/vendorResources/get-installations-provider': return httpx.Response(200, json=adapter)
            if path.startswith('/realms/test-realm/tide-idp-resources/images/'): return httpx.Response(200, content=image_bytes)
        if path == '/clients/client-id' and request.method == 'PUT':
            # Simulate a server that did not apply a change and has no pending request.
            return httpx.Response(204)
        if state['govern_writes'] and (path.endswith('/protocol-mappers/models') or path.endswith('/scope-mappings/realm') or path == ''):
            state['pending'].append({'id': str(len(state['pending'])), 'status': 'PENDING'})
            return httpx.Response(202)
        if path == '/tide-idp-admin-resources/images/upload':
            return httpx.Response(200, json={'hash': hashlib.sha256(image_bytes).hexdigest()})
        if path == '/vendorResources/set-branding':
            state['branding'] = json.loads(request.content); return httpx.Response(200, text='Branding updated')
        if path == '/vendorResources/sign-idp-settings': return httpx.Response(200, text='Settings signed')
        raise AssertionError((request.method, path))

    real_client = httpx.Client
    monkeypatch.setattr(tide_setup.httpx, 'Client', lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)))
    body = tide_setup.Configure(realm='test-realm', username='owner', password='fixture-password', allow_registration=True)
    return state, lambda: tide_setup.configure_realm(body, ORIGIN, tmp_path)


def test_approved_client_with_reordered_urls_and_mapper_defaults_finishes_setup(setup_server):
    state, configure = setup_server
    assert configure()['state'] == 'configured'
    assert not any(path.startswith('/clients') for _, path in state['writes'])
    assert tide_config.load()['issuer'] == 'http://localhost:8080/realms/test-realm'


def test_unapplied_change_with_empty_queue_is_an_error_not_fake_approval(setup_server):
    state, configure = setup_server
    state['client']['redirectUris'] = [ORIGIN + '/*']
    with pytest.raises(tide_setup.HTTPException) as caught:
        configure()
    assert caught.value.status_code == 409
    assert 'no pending approvals' in caught.value.detail
    assert tide_config.load() is None


def test_remaining_independent_changes_are_prepared_in_one_round(setup_server):
    state, configure = setup_server
    state.update(mappers=[], scoped=[], govern_writes=True)
    state['realm']['registrationAllowed'] = False
    with pytest.raises(tide_setup.Pending, match='3 change request'):
        configure()
    assert ('POST', '/clients/client-id/protocol-mappers/models') in state['writes']
    assert ('POST', '/clients/client-id/scope-mappings/realm') in state['writes']
    assert ('PUT', '') in state['writes']
    assert tide_config.load() is None


@pytest.mark.parametrize('change', [
    {'redirectUris': [ORIGIN + '/*']}, {'webOrigins': ['*']},
    {'fullScopeAllowed': True}, {'publicClient': False},
    {'attributes': {'pkce.code.challenge.method': 'plain', 'dpop.bound.access.tokens': 'true'}},
])
def test_client_comparison_still_rejects_security_setting_changes(change):
    desired = {'redirectUris': [ORIGIN + '/', ORIGIN + '/silent-check-sso.html'], 'webOrigins': [ORIGIN],
               'fullScopeAllowed': False, 'publicClient': True,
               'attributes': {'pkce.code.challenge.method': 'S256', 'dpop.bound.access.tokens': 'true'}}
    assert not tide_setup.client_matches({**desired, **change}, desired)


@pytest.mark.parametrize('pending', [[], [{'id': 'pending-mapper', 'status': 'PENDING'}]])
def test_governance_conflict_only_reports_approval_when_queue_exists(pending):
    def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json=pending)
        return httpx.Response(409, json={'error': 'Entity has pending changes'})
    with httpx.Client(base_url='http://localhost:8080', transport=httpx.MockTransport(handle)) as client:
        admin = tide_setup.Admin(client, 'test-realm')
        with pytest.raises(tide_setup.Pending if pending else tide_setup.HTTPException):
            admin.call('POST', '/clients/client-id/scope-mappings/realm', json=[])


@pytest.mark.parametrize('change', [
    {'protocolMapper': 'oidc-hardcoded-claim-mapper'},
    {'config': {'included.client.audience': 'wrong', 'id.token.claim': 'false', 'access.token.claim': 'true'}},
    {'config': {'included.client.audience': 'redacted', 'id.token.claim': 'false', 'access.token.claim': 'false'}},
])
def test_mapper_comparison_rejects_wrong_mapper_or_audience(change):
    desired = {'name': 'redacted-api-audience', 'protocol': 'openid-connect', 'protocolMapper': 'oidc-audience-mapper',
               'config': {'included.client.audience': 'redacted', 'id.token.claim': 'false', 'access.token.claim': 'true'}}
    assert not tide_setup.mapper_matches({**desired, **change}, desired)
