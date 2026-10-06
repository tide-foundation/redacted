"""Real signatures and request proofs, no authentication dependency overrides."""
import hashlib
import json
import time
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from fastapi.testclient import TestClient
import jwt
import pytest

from backend import app as service, auth, tide_config, tide_setup

BASE = 'http://localhost:3001'
ISSUER = 'http://localhost:8080/realms/redacted'
ROLES = ['_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    signing = ed25519.Ed25519PrivateKey.generate()
    key = json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(signing.public_key()))
    key['kid'] = 'test-key'
    config = tide_config.validate({'auth-server-url': 'http://localhost:8080/', 'realm': 'redacted',
        'resource': 'redacted', 'vendorId': 'test-vendor', 'homeOrkUrl': 'https://example.test',
        'jwk': {'keys': [key]}, 'client-origin-auth-' + BASE: 'fixture-signed-origin'}, BASE)
    tide_config.install(config)
    proof_key = ec.generate_private_key(ec.SECP256R1())
    proof_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(proof_key.public_key()))
    thumb = {k: proof_jwk[k] for k in ('crv', 'kty', 'x', 'y')}
    thumbprint = auth.digest(json.dumps(thumb, sort_keys=True, separators=(',', ':')).encode())
    def token(**overrides):
        claims = {'iss': ISSUER, 'sub': 'alice', 'aud': 'redacted', 'azp': 'redacted', 'typ': 'DPoP',
                  'iat': int(time.time()), 'exp': int(time.time()) + 300, 'realm_access': {'roles': ROLES},
                  'cnf': {'jkt': thumbprint}, **overrides}
        return jwt.encode(claims, signing, algorithm='EdDSA', headers={'kid': 'test-key'})
    def headers(access=None, path='/api/service/history', method='GET', **overrides):
        access = access or token()
        data = {'jti': str(uuid4()), 'iat': int(time.time()), 'htu': BASE + path,
                'htm': method, 'ath': auth.digest(access.encode()), 'nonce': auth._nonce, **overrides}
        proof = jwt.encode(data, proof_key, algorithm='ES256', headers={'typ': 'dpop+jwt', 'jwk': proof_jwk})
        return {'Authorization': 'DPoP ' + access, 'DPoP': proof}
    auth._seen.clear()
    with TestClient(service.app, base_url=BASE) as client:
        yield client, token, headers


def test_real_auth_and_owner_isolation(fixture):
    c, token, headers = fixture
    path = '/api/service/history'
    assert c.get(path, headers=headers()).status_code == 200
    metadata = {'source_type': 'pdf', 'mode': 'redact', 'counts': {}, 'layout_preserved': True}
    response = c.post(path, json=metadata, headers=headers(method='POST'))
    assert response.status_code == 201
    doc = path + '/' + response.json()['id']
    assert c.get(doc, headers=headers(path=doc)).status_code == 200
    assert c.get(doc, headers=headers(token(sub='bob'), path=doc)).status_code == 404
    assert c.delete(doc, headers=headers(token(sub='bob'), path=doc, method='DELETE')).status_code == 404
    assert c.get(doc, headers={**headers(token(sub='bob'), path=doc), 'X-Owner-Id': 'alice'}).status_code == 404


@pytest.mark.parametrize('overrides', [
    {'iss': 'http://evil.test/realms/redacted'}, {'aud': 'other'}, {'azp': 'other'},
    {'exp': int(time.time()) - 60}, {'iat': int(time.time()) + 600}, {'typ': 'ID'},
    {'cnf': {}}, {'cnf': {'jkt': 'wrong'}}, {'sub': ''},
])
def test_reject_invalid_access_claims(fixture, overrides):
    c, token, headers = fixture
    assert c.get('/api/service/history', headers=headers(token(**overrides))).status_code == 401


def test_reject_missing_roles_and_unbound_or_unsigned_tokens(fixture):
    c, token, headers = fixture
    assert c.get('/api/service/history', headers=headers(token(realm_access={'roles': []}))).status_code == 403
    assert c.get('/api/service/history', headers={'Authorization': 'Bearer ' + token()}).status_code == 401
    assert c.get('/api/service/history', headers={'Authorization': 'DPoP ' + token()}).status_code == 401
    access = token(); access = access[:-8] + 'abcdefgh'
    assert c.get('/api/service/history', headers=headers(access)).status_code == 401


@pytest.mark.parametrize('overrides', [{'htm': 'DELETE'}, {'htu': BASE + '/api/service/identity'},
    {'ath': 'wrong'}, {'iat': int(time.time()) - 90}, {'jti': ''}])
def test_reject_wrong_or_stale_proofs(fixture, overrides):
    c, _, headers = fixture
    assert c.get('/api/service/history', headers=headers(**overrides)).status_code == 401


def test_nonce_retry_and_replay_rejection(fixture):
    c, _, headers = fixture
    response = c.get('/api/service/history', headers=headers(nonce='old-process-nonce'))
    assert response.status_code == 401
    assert 'use_dpop_nonce' in response.headers['WWW-Authenticate']
    valid = headers(nonce=response.headers['DPoP-Nonce'])
    assert c.get('/api/service/history', headers=valid).status_code == 200
    assert c.get('/api/service/history', headers=valid).status_code == 401


def test_trusted_config_rejects_secrets_and_remote_issuers(fixture):
    config = tide_config.load()
    for update in [{'credentials': {'secret': 'private'}}, {'auth-server-url': 'https://attacker.test'},
                   {'realm': 'master'}, {'jwk': {'keys': []}}]:
        with pytest.raises(ValueError):
            tide_config.validate({**config['adapter'], **update}, BASE)


def test_relay_pins_issuer_and_client_and_allows_enclave_embed(fixture, tmp_path):
    c, _, _ = fixture
    invalid = '/tide_dpop/iss/' + 'evil'.encode().hex() + '/aud/' + 'redacted'.encode().hex() + '/tide_dpop_auth.html'
    assert c.get(invalid, headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert c.get('/tide_dpop_auth.html').status_code == 404
    path = '/tide_dpop/iss/' + ISSUER.encode().hex() + '/aud/' + 'redacted'.encode().hex() + '/tide_dpop_auth.html'
    response = c.get(path, headers={'Sec-Fetch-Site': 'cross-site'})
    assert response.status_code == 200
    assert 'Content-Security-Policy' in response.headers
    assert 'X-Frame-Options' not in response.headers
    assert c.get('/api/service/guest/current', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_obsolete_manual_setup_routes_are_closed(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    with TestClient(service.app, base_url=BASE) as client:
        for path in ['unlock', 'configure']:
            assert client.post('/api/service/tide/setup/' + path, json={}).status_code == 404
        assert client.get('/api/service/tide/setup/session').status_code == 404


def test_tide_outage_keeps_guest_workflow_available(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(tide_setup, '_probe', (0, False))
    def offline(*args, **kwargs):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(tide_setup.httpx, 'get', offline)
    with TestClient(service.app, base_url=BASE) as client:
        response = client.get('/api/service/tide/status')
        assert response.status_code == 200
        assert response.json()['state'] == 'stopped'
        assert client.get('/api/service/guest/current').status_code == 200
        assert client.get('/api/service/capabilities').json()['secure_history']['available'] is False


def test_rejected_owner_credentials_explain_recovery_without_echoing_secrets(monkeypatch, tmp_path):
    import httpx
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={'error': 'invalid_grant'}))
    monkeypatch.setattr(tide_setup.httpx, 'Client', lambda **kwargs: real_client(**kwargs, transport=transport))
    body = tide_setup.Configure(realm='existing-realm', username='redacted-owner', password='private-password')
    with pytest.raises(tide_setup.HTTPException) as caught:
        tide_setup.configure_realm(body, BASE, tmp_path)
    assert caught.value.status_code == 403
    assert 'bash scripts/tidecloak.sh credentials' in caught.value.detail
    assert 'not the setup code' in caught.value.detail
    assert 'private-password' not in caught.value.detail
