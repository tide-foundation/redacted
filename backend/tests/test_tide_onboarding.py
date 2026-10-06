"""Owner handoff and bootstrap boundaries; no live server or network required."""
import hashlib
import json
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from backend import app, tide_config, tide_onboarding as setup, tide_setup

BASE = 'http://localhost:3001'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(setup, '_authority', None)
    monkeypatch.setattr(setup, 'begin', lambda a: None)
    with TestClient(app.app, base_url=BASE) as c:
        yield c


def permit():
    root = tide_config.directory(); root.mkdir(parents=True, exist_ok=True)
    (root / 'launch-permit.json').write_text(json.dumps({
        'digest': hashlib.sha256(b'x' * 32).hexdigest(), 'expires': time.time() + 60}))


def unlock(c):
    permit()
    response = c.post('/api/service/tide/setup/v2/launch', json={
        'permit': 'x' * 32, 'username': 'owner', 'password': 'private-master-password'})
    assert response.status_code == 200
    url = response.json()['url']
    assert '?setup=' not in url and '#setup=' in url
    assert 'private-master-password' not in response.text
    r = c.post('/api/service/tide/setup/v2/exchange', json={'token': url.split('#setup=')[1]})
    assert r.status_code == 200
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert f'Max-Age={setup.RESUME_TTL}' in r.headers['set-cookie']
    return {'X-Setup-CSRF': r.json()['csrf']}, url.split('#setup=')[1]


def test_terminal_permit_and_browser_link_are_single_use(client):
    c = client
    body = {'permit': 'x' * 32, 'username': 'owner', 'password': 'private-master-password'}
    assert c.post('/api/service/tide/setup/v2/launch', json=body).status_code == 403
    headers, link = unlock(c)
    assert c.post('/api/service/tide/setup/v2/launch', json=body).status_code == 403
    assert c.post('/api/service/tide/setup/v2/exchange', json={'token': link}).status_code == 401
    assert c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    assert not (tide_config.directory() / 'launch-permit.json').exists()
    assert not any('private-master-password' in f.read_text() for f in tide_config.directory().glob('*.json'))


def test_csrf_origin_and_expired_authority_are_rejected(client):
    c = client; headers, _ = unlock(c)
    body = {'realm': 'redacted-new', 'email': 'owner@example.test', 'accept_terms': True}
    assert c.post('/api/service/tide/setup/v2/begin', json=body).status_code == 403
    assert c.post('/api/service/tide/setup/v2/begin', json=body, headers={**headers, 'Origin': 'https://evil.test'}).status_code == 403
    assert c.get('/api/service/tide/setup/v2/session', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert c.post('/api/service/tide/setup/v2/begin', json=body, headers=headers).status_code == 200
    setup._authority['expires'] = time.time() - 1
    assert c.get('/api/service/tide/setup/v2/session').json()['unlocked'] is False
    assert setup._authority is None
    assert c.post('/api/service/tide/setup/v2/continue', headers=headers).status_code == 401
    assert setup.read_progress()['realm'] == 'redacted-new'


def test_restart_and_reopened_browser_resume_without_terminal(client, monkeypatch):
    c = client; headers, _ = unlock(c)
    body = {'realm': 'redacted-new', 'email': 'owner@example.test', 'accept_terms': True}
    c.post('/api/service/tide/setup/v2/begin', json=body, headers=headers).raise_for_status()
    state = setup.read_progress(); state['stage'] = 'configure'; state['completed'] += ['realm', 'license']; setup.save_progress(state)
    monkeypatch.setattr(setup, '_authority', None)
    # A new browser session retains persistent cookies but no in-memory state.
    cookies = dict(c.cookies)
    c.cookies.clear()
    c.cookies.update(cookies)
    resumed = c.get('/api/service/tide/setup/v2/session').json()
    assert resumed['unlocked']
    assert resumed['progress']['stage'] == 'configure'
    assert 'password' not in json.dumps(resumed)
    assert 'private-master-password' not in (tide_config.directory() / 'onboarding.json').read_text()
    sealed = setup.resume_path().read_bytes()
    assert b'private-master-password' not in sealed
    assert cookies[setup._COOKIE].encode() not in sealed
    assert setup.resume_path().stat().st_mode & 0o777 == 0o600
    assert c.post('/api/service/tide/setup/v2/begin', json=body, headers={'X-Setup-CSRF': resumed['csrf']}).status_code == 409


def test_resume_requires_original_cookie_and_rejects_tampering(client, monkeypatch):
    c = client
    unlock(c)
    original = dict(c.cookies)
    monkeypatch.setattr(setup, '_authority', None)
    c.cookies.clear()
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    c.cookies.set(setup._COOKIE, 'wrong-cookie')
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    assert setup.resume_path().exists()
    c.cookies.clear(); c.cookies.update(original)
    sealed = setup.resume_path().read_bytes()
    setup.resume_path().write_bytes(sealed[:-10] + b'tampered!!')
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    setup.resume_path().write_bytes(sealed)
    assert c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    setup.expire(setup._authority['id'])
    assert not setup.resume_path().exists()
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']


def test_resume_expires_even_after_server_restart(client, monkeypatch):
    c = client
    unlock(c)
    cookie = c.cookies[setup._COOKIE]
    monkeypatch.setattr(setup, '_authority', None)
    now = time.time()
    monkeypatch.setattr(setup.time, 'time', lambda: now + setup.RESUME_TTL + 1)
    # Send the expired cookie explicitly: a normal browser already drops it.
    assert not c.get('/api/service/tide/setup/v2/session', headers={
        'Cookie': f'{setup._COOKIE}={cookie}'}).json()['unlocked']
    assert not setup.resume_path().exists()


def test_new_terminal_handoff_revokes_previous_resume_cookie(client, monkeypatch):
    c = client
    unlock(c)
    old = dict(c.cookies)
    unlock(c)
    new = dict(c.cookies)
    monkeypatch.setattr(setup, '_authority', None)
    c.cookies.clear(); c.cookies.update(old)
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']
    c.cookies.clear(); c.cookies.update(new)
    assert c.get('/api/service/tide/setup/v2/session').json()['unlocked']


def test_resume_is_bound_to_original_tide_server(client, monkeypatch):
    c = client
    unlock(c)
    monkeypatch.setattr(setup, '_authority', None)
    monkeypatch.setenv('TIDECLOAK_PUBLIC_URL', 'http://localhost:9999')
    assert not c.get('/api/service/tide/setup/v2/session').json()['unlocked']


def test_master_and_unaccepted_terms_cannot_start(client):
    c = client; headers, _ = unlock(c)
    for realm, terms in [('master', True), ('redacted', False)]:
        assert c.post('/api/service/tide/setup/v2/begin', headers=headers, json={
            'realm': realm, 'email': 'owner@example.test', 'accept_terms': terms}).status_code == 400
    assert setup.read_progress() is None


def test_setup_cannot_modify_an_existing_unrelated_realm():
    calls = []
    def handle(request):
        calls.append(request.method)
        return httpx.Response(200, json={'realm': 'other-app', 'attributes': {}})
    with httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(setup.HTTPException) as e:
            setup.assert_realm(tide_setup.Admin(c, 'other-app'), {'id': 'our-setup'})
    assert e.value.status_code == 409 and calls == ['GET']


def test_enclave_challenge_is_not_auto_signed():
    calls = []
    change = {'id': 'change-one', 'status': 'PENDING', 'readyToCommit': False}
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == 'GET':
            return httpx.Response(200, json=[change] if request.url.path.endswith('change-requests') else change)
        assert request.url.path.endswith('/approve')
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={'mode': 'enclave', 'requestModel': 'challenge'})
    with httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(tide_setup.Pending):
            setup.SetupAdmin(c, 'our-test').wait_for_approvals()
    assert not any(path.endswith(('/authorize', '/commit', '/toggle-iga')) for _, path in calls)


def test_first_admin_changes_commit_only_when_ready():
    change = {'id': 'change-one', 'status': 'PENDING', 'readyToCommit': False}
    def handle(request):
        if request.url.path.endswith('/approve'):
            change['readyToCommit'] = True
            return httpx.Response(200, json={'mode': 'recorded'})
        if request.url.path.endswith('/commit'):
            assert change['readyToCommit']; change['status'] = 'APPROVED'
            return httpx.Response(200, json={})
        if request.url.path.endswith('/change-requests'):
            return httpx.Response(200, json=[change] if change['status'] == 'PENDING' else [])
        return httpx.Response(200, json=change)
    with httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle)) as c:
        setup.SetupAdmin(c, 'our-test').wait_for_approvals()
    assert change['status'] == 'APPROVED'


def test_rejected_setup_write_retries_after_blocking_bootstrap_approval():
    change = {'id': 'blocking-change', 'status': 'PENDING', 'readyToCommit': False}
    writes = []
    def handle(request):
        if request.url.path.endswith('/scope-mappings/realm'):
            writes.append(request.content)
            return httpx.Response(409 if change['status'] == 'PENDING' else 204)
        if request.url.path.endswith('/approve'):
            change['readyToCommit'] = True
            return httpx.Response(200, json={'mode': 'recorded'})
        if request.url.path.endswith('/commit'):
            assert change['readyToCommit']
            change['status'] = 'APPROVED'
            return httpx.Response(200, json={})
        if request.url.path.endswith('/change-requests'):
            return httpx.Response(200, json=[change] if change['status'] == 'PENDING' else [])
        return httpx.Response(200, json=change)
    with httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle)) as c:
        setup.SetupAdmin(c, 'our-test').call('POST', '/clients/test/scope-mappings/realm', json=[{'id': 'history-role'}])
    assert change['status'] == 'APPROVED'
    assert len(writes) == 2 and writes[0] == writes[1]


def test_persistent_write_conflict_is_bounded_and_signed_approval_still_stops_setup():
    for needs_signature in (False, True):
        writes = []
        change = {'id': 'needs-signature', 'status': 'PENDING', 'readyToCommit': False}
        def handle(request):
            if request.url.path.endswith('/scope-mappings/realm'):
                writes.append(request.content)
                return httpx.Response(409)
            if request.url.path.endswith('/approve'):
                return httpx.Response(200, json={'mode': 'enclave', 'requestModel': 'challenge'})
            if request.url.path.endswith('/change-requests'):
                return httpx.Response(200, json=[change] if needs_signature else [])
            return httpx.Response(200, json=change)
        with httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle)) as c:
            with pytest.raises(tide_setup.Pending if needs_signature else setup.HTTPException):
                setup.SetupAdmin(c, 'our-test').call('POST', '/clients/test/scope-mappings/realm', json=[])
        assert len(writes) == (1 if needs_signature else 2)


def test_realm_template_has_only_personal_defaults_and_no_fake_mapper():
    realm = setup.realm_template({'realm': 'example', 'id': 'our-setup', 'allow_registration': True})
    assert realm['roles']['realm'][-1]['composites']['realm'] == [
        '_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']
    assert 'tide-realm-admin' not in json.dumps(realm['defaultRole'])
    assert 'tide-roles-mapper' not in json.dumps(realm)
    assert 'identityProviders' not in realm


def test_unlisted_approval_cannot_be_submitted(client):
    c = client; headers, _ = unlock(c)
    assert c.post('/api/service/tide/setup/v2/approvals/some-other-app-change', headers=headers, json={}).status_code == 404


@pytest.mark.parametrize('license_response', ['text', 'json', 'failure'])
def test_provisioning_resumes_from_server_state_and_never_grants_admin_before_link(client, monkeypatch, tmp_path, license_response):
    """Exercise the actual worker through a simulated Tide HTTP API."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    import jwt
    root = tide_config.directory()
    state = {'id': 'our-job', 'realm': 'new-test', 'email': 'owner@example.test',
             'allow_registration': True, 'app_origin': BASE, 'tide_url': 'http://localhost:8080',
             'stage': 'realm', 'completed': ['details']}
    setup.save_progress(state)
    authority = {'id': 'owner-job', 'expires': time.time() + 3600, 'username': 'owner', 'password': setup.SecretStr('private-master-password')}
    monkeypatch.setattr(setup, '_authority', authority)
    monkeypatch.setenv('TIDECLOAK_INTERNAL_URL', 'http://localhost:8080')
    monkeypatch.setenv('TIDECLOAK_PUBLIC_URL', 'http://localhost:8080')
    brand = tmp_path / 'front/brand'; brand.mkdir(parents=True)
    image = b'\xff\xd8\xff-brand-fixture'
    for name in ['redacted-wallpaper.jpg', 'redacted-logo_stacked.jpg']:
        (brand / name).write_bytes(image)
    monkeypatch.setenv('PRIVACY_FRONTEND_DIR', str(brand.parent))
    keys = {'keys': [json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(ed25519.Ed25519PrivateKey.generate().public_key()))]}
    roles = [{'id': n, 'name': n} for n in ['_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']]
    db = {'realm': None, 'clients': [], 'mappers': [], 'scopes': [], 'licensed': False, 'users': [], 'idp': {'config': {}}, 'writes': []}
    def handle(request):
        path = request.url.path.removeprefix('/admin/realms/new-test')
        method = request.method
        if method != 'GET': db['writes'].append(path)
        if path.endswith('/protocol/openid-connect/token'): return httpx.Response(200, json={'access_token': 'fixture'})
        if path == '/admin/realms' and method == 'POST':
            db['realm'] = {**json.loads(request.content), 'defaultRole': {'id': 'default-role'}}
            return httpx.Response(201)
        if path == '':
            if method == 'PUT': db['realm'].update(json.loads(request.content)); return httpx.Response(204)
            return httpx.Response(200, json=db['realm']) if db['realm'] else httpx.Response(404)
        if path == '/components': return httpx.Response(200, json=[{'providerId': 'tide-vendor-key'}] if db['licensed'] else [])
        if path == '/vendorResources/setUpTideRealm':
            assert b'email=owner%40example.test' in request.content
            assert request.headers['content-type'].startswith('application/x-www-form-urlencoded')
            if license_response == 'failure':
                return httpx.Response(500, text='Licence activation failed')
            db['licensed'] = True; db['realm']['attributes']['iga.attestor'] = 'tide'
            return httpx.Response(200, text='CREATED') if license_response == 'text' else httpx.Response(200, json={})
        if path == '/tide-admin/toggle-iga':
            assert request.content == b'isIGAEnabled=true'
            db['realm']['attributes']['isIGAEnabled'] = 'true'; return httpx.Response(200, json={'enabled': True})
        if path == '/iga/change-requests': return httpx.Response(200, json=[])
        if path == '/users/profile': return httpx.Response(200, json={'attributes': []})
        if path == '/authentication/required-actions': return httpx.Response(200, json=[])
        if path == '/authentication/flows/first broker login/executions': return httpx.Response(200, json=[])
        if path.startswith('/roles/'): return httpx.Response(200, json=next(r for r in roles if r['name'] == path.split('/')[-1]))
        if path == '/roles-by-id/default-role/composites': return httpx.Response(200, json=roles)
        if path == '/clients':
            if method == 'POST': db['clients'] = [{**json.loads(request.content), 'id': 'client'}]; return httpx.Response(201)
            return httpx.Response(200, json=db['clients'])
        if path == '/clients/client': return httpx.Response(200, json=db['clients'][0])
        if path.endswith('/protocol-mappers/models'):
            if method == 'POST': db['mappers'].append({**json.loads(request.content), 'id': 'mapper'}); return httpx.Response(201)
            return httpx.Response(200, json=db['mappers'])
        if path.endswith('/scope-mappings/realm'):
            if method == 'POST': db['scopes'] = json.loads(request.content); return httpx.Response(204)
            return httpx.Response(200, json=db['scopes'])
        if path == '/tide-idp-admin-resources/images/upload': return httpx.Response(200, json={'hash': hashlib.sha256(image).hexdigest()})
        if path.startswith('/realms/new-test/tide-idp-resources/images'): return httpx.Response(200, content=image)
        if path == '/vendorResources/set-branding': db['branding'] = json.loads(request.content); return httpx.Response(200, text='Signed')
        if path == '/vendorResources/get-branding': return httpx.Response(200, json=db['branding'])
        if path == '/vendorResources/sign-idp-settings': return httpx.Response(200, text='Signed')
        if path == '/vendorResources/get-installations-provider': return httpx.Response(200, json={
            'auth-server-url': 'http://localhost:8080/', 'realm': 'new-test', 'resource': 'redacted',
            'vendorId': 'fixture', 'homeOrkUrl': 'https://example.test', 'jwk': keys, 'client-origin-auth-' + BASE: 'fixture-signed-origin'})
        if path == '/identity-provider/instances/tide':
            if method == 'PUT': db['idp'] = json.loads(request.content); return httpx.Response(204)
            return httpx.Response(200, json=db['idp'])
        if path == '/users':
            if method == 'POST':
                user = json.loads(request.content)
                if not user.get('email'):
                    return httpx.Response(400, json={'field': 'email', 'errorMessage': 'error-user-attribute-required'})
                assert user['email'] == 'owner@example.test'
                assert user['emailVerified'] is False
                db['users'] = [{**user, 'id': 'owner-user'}]
                return httpx.Response(201)
            return httpx.Response(200, json=db['users'])
        raise AssertionError((method, path))
    real = httpx.Client
    monkeypatch.setattr(setup.httpx, 'Client', lambda **kw: real(**kw, transport=httpx.MockTransport(handle)))
    setup._work.acquire(); setup.run_setup(authority)
    progress = setup.read_progress()
    if license_response == 'failure':
        assert progress['stage'] == 'license'
        assert 'HTTP 500' in progress['error']
        assert not db['clients'] and not db['users']
        assert tide_config.load() is None
        return
    assert not progress.get('error'), progress
    assert progress['stage'] == 'link'
    assert progress['admin_id'] == 'owner-user'
    assert tide_config.load() is None, 'Server provisioning alone must not declare secure history ready.'
    assert not any('/role-mappings/clients/' in path for path in db['writes'])
    assert BASE + '/secure-history/setup' in db['clients'][0]['redirectUris']
    assert 'private-master-password' not in (root / 'onboarding.json').read_text()
    # A retry of configure reads the already-provisioned entities, keeps the
    # setup callback and does not license or create the realm/user a second time.
    progress['stage'] = 'configure'; setup.save_progress(progress)
    before = len(db['writes'])
    setup._work.acquire(); setup.run_setup(authority)
    assert setup.read_progress()['stage'] == 'link'
    assert not any(p in ['/admin/realms', '/users', '/vendorResources/setUpTideRealm', '/clients'] for p in db['writes'][before:])


def test_invalid_handoff_never_echoes_credentials(client):
    private = 'private-password-' * 100
    response = client.post('/api/service/tide/setup/v2/launch', json={
        'permit': 'x' * 32, 'username': 'owner', 'password': private})
    assert response.status_code == 422
    assert 'private-password' not in response.text
    assert response.headers['referrer-policy'] == 'no-referrer'


def test_admin_tokens_refresh_before_expiring_and_authority_expiry_stops_writes(client, monkeypatch):
    unlock(client)
    auth = setup.OwnerAuth(setup._authority)
    minted = 0
    def handle(request):
        nonlocal minted
        if request.url.path.endswith('/token'):
            minted += 1
            return httpx.Response(200, json={'access_token': f'token-{minted}', 'expires_in': 60})
        assert request.headers['Authorization'] == f'Bearer token-{minted}'
        return httpx.Response(200, json={})
    with httpx.Client(base_url='http://localhost:8080', auth=auth, transport=httpx.MockTransport(handle)) as c:
        c.get('/admin/realms/test'); c.get('/admin/realms/test')
        assert minted == 1
        auth.expires = 0
        c.put('/admin/realms/test', json={})
        assert minted == 2
        setup._authority['expires'] = 0
        with pytest.raises(setup.HTTPException) as error:
            c.put('/admin/realms/test', json={})
        assert error.value.status_code == 401
        assert minted == 2


def test_busy_worker_prevents_approval_or_account_link_mutations(client):
    c = client; headers, _ = unlock(c)
    setup.save_progress({'id': 'job', 'realm': 'new-test', 'stage': 'link', 'completed': [], 'admin_id': 'user',
        'app_origin': BASE, 'tide_url': 'http://localhost:8080', 'pending': [{'id': 'pending'}]})
    setup._work.acquire()
    try:
        assert c.post('/api/service/tide/setup/v2/link', headers=headers).status_code == 409
        assert c.post('/api/service/tide/setup/v2/approvals/pending', headers=headers, json={}).status_code == 409
        assert c.post('/api/service/tide/setup/v2/continue', headers=headers).json() == {'started': True}
    finally:
        setup._work.release()


def test_resume_rejects_another_app_origin_before_contacting_tide(client):
    c = client; headers, _ = unlock(c)
    setup.save_progress({'realm': 'new-test', 'stage': 'configure', 'completed': [],
        'app_origin': 'http://localhost:9999', 'tide_url': 'http://localhost:8080'})
    assert c.post('/api/service/tide/setup/v2/continue', headers=headers).status_code == 409
    assert not setup._work.locked()


@pytest.mark.parametrize('registered', [False, True])
def test_account_link_uses_shipped_server_query_names_and_plain_text_response(client, monkeypatch, registered):
    c = client; headers, _ = unlock(c)
    setup.save_progress({'id': 'job', 'realm': 'new-test', 'stage': 'link', 'completed': [], 'admin_id': 'user',
        'app_origin': BASE, 'tide_url': 'http://localhost:8080'})
    approvals = []
    monkeypatch.setattr(setup.SetupAdmin, 'wait_for_approvals', lambda self: approvals.append(True))
    def handle(request):
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={'access_token': 'fixture'})
        if request.url.path == '/admin/realms/new-test':
            return httpx.Response(200, json={'attributes': {'redacted.setup.id': 'job'}})
        if request.url.path.endswith('/clients'):
            return httpx.Response(200, json=[{'id': 'client-id', 'clientId': 'redacted', 'redirectUris': [BASE + '/secure-history/setup'] + ([BASE + '/secure-history/linked'] if registered else [])}])
        if request.method == 'PUT' and request.url.path.endswith('/clients/client-id'):
            assert json.loads(request.content)['redirectUris'] == [BASE + '/secure-history/setup', BASE + '/secure-history/linked']
            return httpx.Response(204)
        assert approvals == ([] if registered else [True])
        assert request.url.path.endswith('/get-required-action-link')
        assert request.url.params['client_id'] == 'redacted'
        assert request.url.params['redirect_uri'] == BASE + '/secure-history/linked'
        assert 'clientId' not in request.url.params and 'redirectUri' not in request.url.params
        assert json.loads(request.content) == ['link-tide-account-action']
        return httpx.Response(200, text='http://localhost:8080/realms/new-test/login-actions/action-token?key=fixture\n')
    real = httpx.Client
    monkeypatch.setattr(setup.httpx, 'Client', lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handle)))
    result = c.post('/api/service/tide/setup/v2/link', headers=headers)
    assert result.status_code == 200
    assert result.json()['url'].endswith('key=fixture')


def test_link_checks_apply_pending_first_admin_attributes_before_reading_user(client, monkeypatch):
    c = client; headers, _ = unlock(c)
    setup.save_progress({'id': 'job', 'realm': 'new-test', 'stage': 'link', 'completed': [], 'admin_id': 'user',
        'app_origin': BASE, 'tide_url': 'http://localhost:8080'})
    calls = []
    def handle(request):
        path = request.url.path; calls.append(path)
        if path.endswith('/token'): return httpx.Response(200, json={'access_token': 'fixture'})
        if path == '/admin/realms/new-test': return httpx.Response(200, json={'attributes': {'redacted.setup.id': 'job'}})
        if path.endswith('/iga/change-requests'): return httpx.Response(200, json=[])
        assert path.endswith('/users/user')
        return httpx.Response(200, json={'attributes': {}})
    real = httpx.Client
    monkeypatch.setattr(setup.httpx, 'Client', lambda **kwargs: real(**kwargs, transport=httpx.MockTransport(handle)))
    result = c.post('/api/service/tide/setup/v2/continue', headers=headers)
    assert result.json() == {'waiting': True}
    assert calls.index('/admin/realms/new-test/iga/change-requests') < calls.index('/admin/realms/new-test/users/user')
    assert not setup._work.locked()


@pytest.mark.parametrize('occupied', [False, True])
def test_default_realm_selection_preserves_existing_installations(tmp_path, monkeypatch, occupied):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    requests = []
    def handle(request):
        requests.append((request.method, request.url.path))
        if occupied and request.url.path == '/admin/realms/redacted':
            return httpx.Response(200, json={'attributes': {'redacted.setup.id': 'another-installation'}})
        return httpx.Response(404)
    state = {'id': 'our-job', 'realm': 'redacted'}
    with httpx.Client(base_url='http://local', transport=httpx.MockTransport(handle)) as c:
        admin, existing = setup.select_setup_realm(c, state)
    assert state['realm'] == ('redacted-2' if occupied else 'redacted')
    assert setup.read_progress()['realm'] == state['realm']
    assert not existing
    assert all(method == 'GET' for method, _ in requests)


def test_realm_selection_resumes_only_its_own_saved_name(tmp_path, monkeypatch):
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    state = {'id': 'our-job', 'realm': 'redacted-2', 'realm_base': 'redacted'}
    def handle(request):
        owner = 'our-job' if request.url.path.endswith('redacted-2') else 'unrelated'
        return httpx.Response(200, json={'attributes': {'redacted.setup.id': owner}})
    with httpx.Client(base_url='http://local', transport=httpx.MockTransport(handle)) as c:
        _, existing = setup.select_setup_realm(c, state)
    assert state['realm'] == 'redacted-2'
    assert existing['attributes']['redacted.setup.id'] == 'our-job'
