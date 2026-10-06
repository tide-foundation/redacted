"""Embedded, owner-authorized Tide setup with browser-bound resumable authority.

The terminal hands authority to this process once. Public guest endpoints never
receive bootstrap credentials or arbitrary access to the Tide admin API.
"""
from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import secrets
from threading import Lock, Thread, Timer
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from cryptography.fernet import Fernet, InvalidToken

from backend import tide_config, tide_setup
from backend.tide_signup import configure_signup

router = APIRouter(prefix='/api/service/tide/setup/v2')
_guard = Lock()
_work = Lock()
_authority = None
_COOKIE = 'redacted_owner_setup'
TTL = 3600
RESUME_TTL = 7 * 24 * 3600


def resume_path():
    return tide_config.directory() / 'owner-resume.enc'


def resume_cipher(token):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        ('redacted-owner-resume:' + token).encode()).digest()))


def save_authority(a, token, request):
    payload = {**a, 'password': a['password'].get_secret_value(),
               'origin': tide_config.local_origin(str(request.base_url).rstrip('/')),
               'tide_url': tide_setup.public_url()}
    path = resume_path()
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'wb', opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
        out.write(resume_cipher(token).encrypt(json.dumps(payload).encode()))
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def restore_authority(token, request):
    if not token:
        return None
    try:
        a = json.loads(resume_cipher(token).decrypt(resume_path().read_bytes()))
        if a['expires'] <= time.time():
            resume_path().unlink(missing_ok=True)
            return None
        if (a['origin'] != tide_config.local_origin(str(request.base_url).rstrip('/'))
                or a['tide_url'] != tide_setup.public_url()):
            return None
        a['password'] = SecretStr(a['password'])
        timer = Timer(a['expires'] - time.time(), expire, args=(a['id'],))
        timer.daemon = True
        timer.start()
        return a
    except (OSError, InvalidToken, ValueError, KeyError, TypeError):
        return None


def read_progress():
    try:
        return json.loads((tide_config.directory() / 'onboarding.json').read_text())
    except FileNotFoundError:
        return None


def save_progress(state):
    root = tide_config.directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'onboarding.tmp'
    with open(path, 'w', opener=lambda p, flags: os.open(p, flags, 0o600)) as out:
        json.dump(state, out)
        out.flush()
        os.fsync(out.fileno())
    path.replace(root / 'onboarding.json')


def closed():
    if tide_config.load():
        raise HTTPException(409, 'Secure history is already configured.')


def expire(identifier):
    global _authority
    with _guard:
        if _authority and _authority['id'] == identifier:
            _authority = None
            resume_path().unlink(missing_ok=True)


def active():
    global _authority
    if _authority and _authority['expires'] <= time.time():
        _authority = None
        resume_path().unlink(missing_ok=True)
    return _authority


def owner(request: Request):
    global _authority
    closed()
    with _guard:
        a = active()
        token = request.cookies.get(_COOKIE, '')
        if not a:
            _authority = a = restore_authority(token, request)
        cookie = hashlib.sha256(token.encode()).hexdigest()
        if not a or not a.get('cookie') or not hmac.compare_digest(cookie, a['cookie']):
            raise HTTPException(401, 'Reopen setup from the terminal to resume. Your progress is saved.')
        if request.method != 'GET' and not hmac.compare_digest(request.headers.get('X-Setup-CSRF', ''), a['csrf']):
            raise HTTPException(403, 'Setup request could not be verified. Reload this page.')
        return a


class Launch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    permit: SecretStr = Field(min_length=24, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: SecretStr = Field(min_length=1, max_length=1000)


@router.post('/launch')
def launch(body: Launch, request: Request):
    """Only the terminal can write the filesystem permit; never a guest API."""
    global _authority
    closed()
    path = tide_config.directory() / 'launch-permit.json'
    with _guard:
        try:
            permit = json.loads(path.read_text())
            valid = permit['expires'] > time.time() and hmac.compare_digest(
                permit['digest'], hashlib.sha256(body.permit.get_secret_value().encode()).hexdigest())
            if not valid:
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(403, 'Run the local setup script to authorize this installation.') from None
        path.unlink()
        if _work.locked():
            raise HTTPException(409, 'Setup is working. Wait before reopening it.')
        secret = secrets.token_urlsafe(32)
        identifier = secrets.token_hex(16)
        resume_path().unlink(missing_ok=True)
        _authority = {'id': identifier, 'expires': time.time() + TTL,
                      'link': hashlib.sha256(secret.encode()).hexdigest(), 'cookie': None,
                      'csrf': secrets.token_urlsafe(32), 'username': body.username, 'password': body.password}
    # An exchanged link extends this authority. The original link timer must
    # not revoke the longer browser session.
    def expire_link():
        with _guard:
            active()
    timer = Timer(TTL, expire_link); timer.daemon = True; timer.start()
    origin = tide_config.local_origin(str(request.base_url).rstrip('/'))
    return {'url': origin + '/secure-history/setup#setup=' + secret}


class Exchange(BaseModel):
    token: SecretStr = Field(min_length=24, max_length=200)


@router.post('/exchange')
def exchange(body: Exchange, request: Request, response: Response):
    closed()
    with _guard:
        a = active()
        digest = hashlib.sha256(body.token.get_secret_value().encode()).hexdigest()
        if not a or not a.get('link') or not hmac.compare_digest(digest, a['link']):
            raise HTTPException(401, 'This setup link has expired or was already used. Reopen setup from the terminal.')
        token = secrets.token_urlsafe(32)
        a['link'] = None
        a['cookie'] = hashlib.sha256(token.encode()).hexdigest()
        a['expires'] = time.time() + RESUME_TTL
        save_authority(a, token, request)
        csrf = a['csrf']
    timer = Timer(RESUME_TTL, expire, args=(a['id'],)); timer.daemon = True; timer.start()
    response.set_cookie(_COOKIE, token, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', path='/api/service/tide/setup/v2', max_age=RESUME_TTL)
    return {'csrf': csrf}


def public_progress():
    state = read_progress()
    if not state:
        return {'stage': 'details', 'completed': []}
    return {k: v for k, v in state.items() if k in {
        'realm', 'stage', 'completed', 'error', 'allow_registration', 'email', 'pending', 'message'}}


@router.get('/session')
def session(request: Request):
    if tide_config.load():
        return {'unlocked': False, 'configured': True}
    try:
        a = owner(request)
    except HTTPException:
        return {'unlocked': False, 'configured': False, 'saved': read_progress() is not None}
    return {'unlocked': True, 'csrf': a['csrf'], 'busy': _work.locked(), 'progress': public_progress()}


class Details(BaseModel):
    model_config = ConfigDict(extra='forbid')
    realm: str = Field(default='redacted', pattern=r'^[A-Za-z0-9_-]{1,80}$')
    email: str = Field(min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    allow_registration: bool = True
    accept_terms: bool


@contextmanager
def admin_connection(a):
    with httpx.Client(base_url=tide_setup.internal_url(), timeout=60, trust_env=False,
                      follow_redirects=False, auth=OwnerAuth(a),
                      headers={'Host': urlsplit(tide_setup.public_url()).netloc}) as c:
        yield c


class OwnerAuth(httpx.Auth):
    """Mint and refresh short-lived master tokens without persisting them."""
    requires_response_body = True

    def __init__(self, authority):
        self.authority = authority
        self.token = None
        self.expires = 0

    def auth_flow(self, request):
        with _guard:
            if active() is not self.authority:
                raise HTTPException(401, 'Reopen setup from the terminal to resume. Your progress is saved.')
        if not self.token or self.expires <= time.monotonic() + 10:
            response = yield httpx.Request('POST', tide_setup.internal_url() + '/realms/master/protocol/openid-connect/token',
                headers={'Host': urlsplit(tide_setup.public_url()).netloc}, data={
                    'client_id': 'admin-cli', 'grant_type': 'password', 'username': self.authority['username'],
                    'password': self.authority['password'].get_secret_value()})
            if response.status_code != 200:
                raise HTTPException(403, 'The terminal credentials do not match this TideCloak server. Reopen setup with its owner credentials.')
            result = response.json()
            self.token = result['access_token']
            self.expires = time.monotonic() + min(int(result.get('expires_in', 60)), 60)
        request.headers['Authorization'] = 'Bearer ' + self.token
        yield request


def checked(c, method, path, **kwargs):
    r = c.request(method, path, **kwargs)
    if r.status_code == 404 and method == 'GET':
        return None
    if not 200 <= r.status_code < 300:
        raise HTTPException(502, f'TideCloak could not complete this step (HTTP {r.status_code}). Retry to resume.')
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text


def provisional_config():
    """Public relay binding while the owner completes account linking/signing."""
    state = read_progress()
    if not state or state.get('stage') not in {'link', 'admin', 'verify'} or not state.get('adapter'):
        return None
    config = state['adapter']
    return tide_config.validate(config['adapter'], config['app_origin'])


def pending(a):
    data = a.call('GET', '/iga/change-requests', params={'status': 'PENDING'})
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        raise HTTPException(502, 'This TideCloak version did not return a change-request list.')
    return data


def public_requests(items):
    return [{k: p.get(k) for k in ('id', 'entityType', 'actionType', 'threshold', 'authorizationCount', 'blocked')}
            for p in items]


class SetupAdmin(tide_setup.Admin):
    def wait_for_approvals(self, stage='setup'):
        # Only our freshly created realm enters this class. /approve itself
        # enforces firstAdmin versus enclave signing; never disable governance.
        for _ in range(8):
            items = pending(self)
            if not items:
                return
            progressed = False
            for item in items:
                r = self.client.post(self.prefix + '/iga/change-requests/' + item['id'] + '/approve', json={})
                if r.status_code not in (200, 409, 412):
                    raise HTTPException(502, f'Approval preparation failed (HTTP {r.status_code}).')
                result = r.json()
                if r.status_code == 200 and result.get('mode') == 'recorded':
                    progressed = True
                # Only commit when the server reports that quorum is satisfied.
                current = self.call('GET', '/iga/change-requests/' + item['id'])
                if current and current.get('status') == 'PENDING' and current.get('readyToCommit'):
                    commit = self.client.post(self.prefix + '/iga/change-requests/' + item['id'] + '/commit', json={})
                    if commit.status_code not in (200, 409, 412):
                        raise HTTPException(502, 'TideCloak could not apply an approved setup change.')
                    progressed |= commit.status_code == 200
            if not progressed:
                break
        items = pending(self)
        if items:
            raise tide_setup.Pending(f'{len(items)} setup change(s) need your Tide approval.')


def realm_template(state):
    # Stock mappers only. setUpTideRealm installs the Tide identity provider and
    # tide-claims scope; importing a duplicate IdP breaks realm provisioning.
    role_names = ['_tide_enabled', '_tide_history.selfencrypt', '_tide_history.selfdecrypt']
    default = 'default-roles-' + state['realm']
    return {'realm': state['realm'], 'enabled': True, 'sslRequired': 'external',
        'registrationAllowed': state['allow_registration'], 'duplicateEmailsAllowed': True,
        'attributes': {'redacted.setup.id': state['id']},
        'roles': {'realm': [{'name': n} for n in role_names] + [
            {'name': default, 'composite': True, 'composites': {'realm': role_names}}]},
        'defaultRole': {'name': default},
        'components': {'org.keycloak.userprofile.UserProfileProvider': [{
            'providerId': 'declarative-user-profile', 'config': {'kc.user.profile.config': [json.dumps({
                'attributes': [{'name': n, 'permissions': {'view': ['admin', 'user'], 'edit': ['admin', 'user']}}
                               for n in ['username', 'email', 'firstName', 'lastName']],
                'unmanagedAttributePolicy': 'ENABLED'})]}}]},
        'authenticationFlows': [{'alias': 'tidebrowser', 'providerId': 'basic-flow', 'topLevel': True,
            'authenticationExecutions': [
                {'authenticator': 'auth-cookie', 'authenticatorFlow': False, 'requirement': 'ALTERNATIVE', 'priority': 10},
                {'authenticator': 'identity-provider-redirector', 'authenticatorConfig': 'tide browser',
                 'authenticatorFlow': False, 'requirement': 'ALTERNATIVE', 'priority': 25}]}],
        'authenticatorConfig': [{'alias': 'tide browser', 'config': {'defaultProvider': 'tide'}}],
        'browserFlow': 'tidebrowser', 'requiredActions': [
            {'alias': 'link-tide-account-action', 'providerId': 'link-tide-account-action', 'enabled': True},
            {'alias': 'UPDATE_PASSWORD', 'providerId': 'UPDATE_PASSWORD', 'enabled': False},
            {'alias': 'VERIFY_PROFILE', 'providerId': 'VERIFY_PROFILE', 'enabled': False}]}


def select_setup_realm(c, state):
    """Reuse only our own interrupted setup; leave unrelated realms untouched."""
    saved = SetupAdmin(c, state['realm'])
    existing = saved.call('GET', '')
    if existing and (existing.get('attributes') or {}).get('redacted.setup.id') == state['id']:
        return saved, existing
    base = state.setdefault('realm_base', state['realm'])
    for index in range(1, 101):
        name = base if index == 1 else f'{base[:74]}-{index}'
        admin = SetupAdmin(c, name)
        existing = admin.call('GET', '')
        if not existing or (existing.get('attributes') or {}).get('redacted.setup.id') == state['id']:
            state['realm'] = name
            save_progress(state)
            return admin, existing
    raise HTTPException(409, 'Could not find an available Redacted installation name. Contact the person managing TideCloak.')


def assert_realm(a, state):
    realm = a.call('GET', '')
    if not realm or (realm.get('attributes') or {}).get('redacted.setup.id') != state['id']:
        raise HTTPException(409, 'This realm is not the one created by this setup. Existing realms will not be changed.')
    return realm


def next_stage(state, stage):
    if state['stage'] not in state['completed']:
        state['completed'].append(state['stage'])
    state.update(stage=stage, error='', pending=[])
    save_progress(state)


def run_setup(authority):
    state = read_progress()
    try:
        if state['tide_url'] != tide_setup.public_url():
            raise HTTPException(409, 'Setup belongs to a different TideCloak installation.')
        state.update(error='', message='', pending=[])
        save_progress(state)
        while state['stage'] not in ('link', 'ready'):
            with _guard:
                if active() is not authority:
                    raise HTTPException(401, 'Reopen setup from the terminal to resume. Your progress is saved.')
            with admin_connection(authority) as c:
                a = SetupAdmin(c, state['realm'])
                stage = state['stage']
                if stage == 'realm':
                    a, existing = select_setup_realm(c, state)
                    if not existing:
                        checked(c, 'POST', '/admin/realms', json=realm_template(state))
                    assert_realm(a, state)
                    next_stage(state, 'license')
                else:
                    realm = assert_realm(a, state)
                    if stage == 'license':
                        components = a.call('GET', '/components') or []
                        if not any(x.get('providerId') == 'tide-vendor-key' for x in components):
                            # TideCloak returns plain-text CREATED on success.
                            # No response payload is needed; later steps verify
                            # the licensed realm and its signed adapter.
                            a.call('POST', '/vendorResources/setUpTideRealm', expect_json=False,
                                   data={'email': state['email'], 'isRagnarokEnabled': 'true'})
                        configure_signup(a)
                        if (realm.get('attributes') or {}).get('isIGAEnabled') != 'true':
                            a.call('POST', '/tide-admin/toggle-iga', data={'isIGAEnabled': 'true'})
                        realm = a.call('GET', '')
                        attrs = realm.get('attributes') or {}
                        if not attrs.get('iga.attestor'):
                            a.call('PUT', '', json={**realm, 'attributes': {**attrs, 'iga.attestor': 'tide'}})
                        elif attrs['iga.attestor'] != 'tide':
                            raise HTTPException(409, 'The realm must use Tide approval signatures.')
                        a.wait_for_approvals()
                        next_stage(state, 'configure')
                    elif stage == 'configure':
                        # The client, roles, defaults and branding precede the first
                        # admin grant, avoiding avoidable multiAdmin approvals.
                        body = tide_setup.Configure(realm=state['realm'], username=authority['username'],
                            password=authority['password'], allow_registration=state['allow_registration'])
                        tide_setup.configure_realm(body, state['app_origin'],
                            Path(os.environ.get('PRIVACY_FRONTEND_DIR', 'dist')), admin_factory=SetupAdmin,
                            install=False, setup_callback=True, owner_auth=OwnerAuth(authority))
                        clients = a.call('GET', '/clients', params={'clientId': 'redacted'})
                        client = clients[0]
                        idp = a.call('GET', '/identity-provider/instances/tide')
                        desired = {**idp, 'config': {**idp.get('config', {}), 'CustomAdminUIDomain': state['app_origin']}}
                        if desired != idp:
                            a.call('PUT', '/identity-provider/instances/tide', json=desired)
                            a.wait_for_approvals()
                        a.call('POST', '/vendorResources/sign-idp-settings', expect_json=False)
                        adapter = a.call('GET', '/vendorResources/get-installations-provider', params={
                            'clientId': client['id'], 'providerId': 'keycloak-oidc-keycloak-json'})
                        state['adapter'] = tide_config.validate(adapter, state['app_origin'])
                        users = a.call('GET', '/users', params={'username': 'redacted-setup-admin', 'exact': 'true'}) or []
                        if not users:
                            a.call('POST', '/users', json={'username': 'redacted-setup-admin',
                                'email': state['email'], 'emailVerified': False,
                                'firstName': 'Redacted', 'lastName': 'Admin', 'enabled': True,
                                'attributes': {'tideInvitable': ['true']}})
                            a.wait_for_approvals()
                            users = a.call('GET', '/users', params={'username': 'redacted-setup-admin', 'exact': 'true'}) or []
                        if len(users) != 1:
                            raise HTTPException(502, 'TideCloak did not confirm the setup administrator.')
                        if 'true' not in (users[0].get('attributes') or {}).get('tideInvitable', []):
                            raise HTTPException(409, 'TideCloak did not confirm that the administrator can link a Tide account.')
                        state['admin_id'] = users[0]['id']
                        save_progress(state)
                        next_stage(state, 'link')
                    elif stage == 'admin':
                        user = a.call('GET', '/users/' + state['admin_id'])
                        if not (user.get('attributes') or {}).get('tideUserKey'):
                            next_stage(state, 'link'); break
                        management = a.call('GET', '/clients', params={'clientId': 'realm-management'})[0]['id']
                        role = a.call('GET', '/clients/' + management + '/roles/tide-realm-admin')
                        if not role:
                            a.wait_for_approvals()
                            role = a.call('GET', '/clients/' + management + '/roles/tide-realm-admin')
                        if not role:
                            raise HTTPException(502, 'TideCloak has not created its administrator role.')
                        path = '/users/' + state['admin_id'] + '/role-mappings/clients/' + management
                        current = a.call('GET', path) or []
                        if not any(r['id'] == role['id'] for r in current):
                            a.call('POST', path, json=[role])
                        a.wait_for_approvals()
                        current = a.call('GET', path) or []
                        a.require_applied(any(r['id'] == role['id'] for r in current), 'administrator permissions')
                        next_stage(state, 'verify')
                    elif stage == 'verify':
                        a.wait_for_approvals()
                        # Login and the browser encryption round trip are explicitly
                        # separate from successful server provisioning.
                        client = a.call('GET', '/clients', params={'clientId': 'redacted'})[0]
                        adapter = a.call('GET', '/vendorResources/get-installations-provider', params={
                            'clientId': client['id'], 'providerId': 'keycloak-oidc-keycloak-json'})
                        config = tide_config.validate(adapter, state['app_origin'])
                        if config['issuer'] != state['tide_url'] + '/realms/' + state['realm']:
                            raise HTTPException(409, 'The realm returned a different issuer. Check the TideCloak address.')
                        tide_config.install(config)
                        next_stage(state, 'ready')
                        expire(authority['id'])
                    else:
                        raise HTTPException(409, 'Unknown setup step. Reopen setup to recover.')
    except tide_setup.Pending as e:
        state['message'] = str(e)
        try:
            with admin_connection(authority) as c:
                a = tide_setup.Admin(c, state['realm'])
                state['pending'] = public_requests(pending(a))
        except (httpx.HTTPError, HTTPException):
            state['pending'] = []
        save_progress(state)
    except (HTTPException, httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as e:
        state['error'] = e.detail if isinstance(e, HTTPException) else 'TideCloak could not complete this step. Check its connection, then retry; your progress is saved.'
        save_progress(state)
    finally:
        _work.release()


def begin(a, *, acquired=False):
    if not acquired and not _work.acquire(blocking=False):
        return
    thread = Thread(target=run_setup, args=(a,), daemon=True)
    try:
        thread.start()
    except RuntimeError:
        _work.release()
        raise


@router.post('/begin')
def start(body: Details, request: Request, a=Depends(owner)):
    if body.realm == 'master' or not body.accept_terms:
        raise HTTPException(400, 'Accept Tide’s terms before continuing; the administrator namespace is reserved.')
    origin = tide_config.local_origin(str(request.base_url).rstrip('/'))
    state = {'id': secrets.token_hex(16), 'realm': body.realm, 'email': body.email,
             'allow_registration': body.allow_registration, 'terms_accepted_at': int(time.time()),
             'app_origin': origin, 'tide_url': tide_setup.public_url(), 'stage': 'realm', 'completed': ['details']}
    with _guard:
        previous = read_progress()
        if (previous and previous['stage'] != 'details') or _work.locked():
            raise HTTPException(409, 'A setup is already in progress. Resume it instead of creating another realm.')
        save_progress(state)
        begin(a)
    return {'started': True}


def progress_for(request):
    state = read_progress()
    origin = tide_config.local_origin(str(request.base_url).rstrip('/'))
    if not state or state['tide_url'] != tide_setup.public_url() or state['app_origin'] != origin:
        raise HTTPException(409, 'Resume setup at the same Redacted and TideCloak addresses used when it started.')
    return state


@contextmanager
def setup_action():
    if not _work.acquire(blocking=False):
        raise HTTPException(409, 'Setup is already working. Please wait.')
    try:
        yield
    except httpx.HTTPError:
        raise HTTPException(502, 'TideCloak is unreachable. Reconnect and retry; your progress is saved.') from None
    finally:
        _work.release()


@router.post('/continue')
def resume(request: Request, a=Depends(owner)):
    if not _work.acquire(blocking=False):
        return {'started': True}
    handed_off = False
    try:
        state = progress_for(request)
        if state.get('pending') or state['stage'] == 'link':
            with admin_connection(a) as c:
                admin = SetupAdmin(c, state['realm'])
                assert_realm(admin, state)
                if state.get('pending'):
                    items = pending(admin)
                    if items:
                        state['pending'] = public_requests(items)
                        save_progress(state)
                        return {'waiting': True}
                if state['stage'] == 'link':
                    # Linking can create a governed user-attribute change. Apply
                    # allowed bootstrap approvals before looking for the key;
                    # otherwise an uncommitted key looks like linking never ended.
                    try:
                        admin.wait_for_approvals()
                    except tide_setup.Pending as e:
                        state['message'] = str(e)
                        state['pending'] = public_requests(pending(admin))
                        save_progress(state)
                        return {'waiting': True}
                    user = admin.call('GET', '/users/' + state['admin_id'])
                    if not (user.get('attributes') or {}).get('tideUserKey'):
                        return {'waiting': True}
            if state['stage'] == 'link':
                next_stage(state, 'admin')
        handed_off = True
        begin(a, acquired=True)
        return {'started': True}
    except httpx.HTTPError:
        raise HTTPException(502, 'TideCloak is unreachable. Reconnect and retry; your progress is saved.') from None
    finally:
        if not handed_off:
            _work.release()


@router.post('/link')
def link_account(request: Request, a=Depends(owner)):
    state = progress_for(request)
    if not state or state['stage'] != 'link':
        raise HTTPException(409, 'Account linking is not ready yet.')
    with setup_action(), admin_connection(a) as c:
        admin = tide_setup.Admin(c, state['realm'])
        assert_realm(admin, state)
        client = admin.call('GET', '/clients', params={'clientId': 'redacted'})[0]
        callback = state['app_origin'] + '/secure-history/linked'
        if callback not in client.get('redirectUris', []):
            setup_admin = SetupAdmin(c, state['realm'])
            setup_admin.call('PUT', '/clients/' + client['id'], json={**client, 'redirectUris': client.get('redirectUris', []) + [callback]})
            setup_admin.wait_for_approvals()
        value = checked(c, 'POST', admin.prefix + '/tideAdminResources/get-required-action-link',
            params={'userId': state['admin_id'], 'client_id': client['clientId'],
                    'redirect_uri': callback, 'lifespan': 1800},
            json=['link-tide-account-action'])
        url = value.strip() if isinstance(value, str) else value.get('link') or value.get('url')
        if not isinstance(url, str) or not url.startswith(tide_setup.public_url() + '/'):
            raise HTTPException(502, 'TideCloak returned an unexpected account-link URL.')
        return {'url': url}


@router.get('/adapter')
def setup_adapter(a=Depends(owner)):
    state = read_progress()
    if not state or not state.get('adapter'):
        raise HTTPException(409, 'Login configuration is not ready yet.')
    return state['adapter']


class Approval(BaseModel):
    request_model: str | None = Field(default=None, max_length=2_000_000)


@router.post('/approvals/{identifier}')
def approve(identifier: str, body: Approval, request: Request, a=Depends(owner)):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise HTTPException(400, 'Invalid approval identifier.')
    state = read_progress()
    if not state or identifier not in {p['id'] for p in state.get('pending', [])}:
        raise HTTPException(404, 'This is not a pending setup approval.')
    state = progress_for(request)
    with setup_action(), admin_connection(a) as c:
        admin = tide_setup.Admin(c, state['realm'])
        assert_realm(admin, state)
        # Server returns an enclave challenge when required; a master token
        # cannot manufacture the owner's signature or bypass quorum.
        return admin.call('POST', '/iga/change-requests/' + identifier + '/approve',
                          json={'requestModel': body.request_model} if body.request_model else {})
