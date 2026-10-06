"""Tide EdDSA access tokens plus RFC 9449 request proof verification."""
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from threading import Lock
import time

from fastapi import Depends, HTTPException, Request
import jwt

from backend import tide_config


@dataclass(frozen=True)
class VerifiedOwner:
    owner_id: str
    can_write: bool = True

    def __post_init__(self):
        if not self.owner_id or len(self.owner_id) > 200:
            raise ValueError('A verified internal owner identifier is required.')


# Single-worker application. Bound memory and fail closed at capacity rather than
# evicting still-valid proofs, which would allow a replay. Restart invalidates
# prior nonces, so captured proofs cannot be replayed across process restarts.
_lock = Lock()
_seen: dict[tuple[str, str], float] = {}
import secrets
_nonce = secrets.token_urlsafe(32)


def digest(data):
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()


def failure(detail='Authentication could not be verified.'):
    return HTTPException(401, detail, headers={'WWW-Authenticate': 'DPoP', 'DPoP-Nonce': _nonce})


def verify_proof(request, token, claims, config):
    proof = request.headers.get('dpop', '')
    if not proof or len(proof) > 8192:
        raise failure()
    header = jwt.get_unverified_header(proof)
    key = header.get('jwk', {})
    if (header.get('typ') != 'dpop+jwt' or header.get('alg') != 'ES256'
            or key.get('kty') != 'EC' or key.get('crv') != 'P-256' or 'd' in key):
        raise failure()
    thumb = {k: key[k] for k in ('crv', 'kty', 'x', 'y')}
    thumbprint = digest(json.dumps(thumb, separators=(',', ':'), sort_keys=True).encode())
    if not hmac.compare_digest(thumbprint, claims.get('cnf', {}).get('jkt', '')):
        raise failure()
    decoded = jwt.decode(proof, jwt.PyJWK(key).key, algorithms=['ES256'],
                         options={'require': ['jti', 'iat', 'htm', 'htu', 'ath'], 'verify_aud': False}, leeway=5)
    now = time.time()
    if (type(decoded['iat']) not in (int, float) or abs(now - decoded['iat']) > 60
            or decoded['htm'] != request.method
            or decoded['htu'] != config['app_origin'] + request.url.path
            or not hmac.compare_digest(str(decoded['ath']), digest(token.encode()))):
        raise failure()
    if decoded.get('nonce') != _nonce:
        raise HTTPException(401, 'A fresh request proof is required.', headers={
            'WWW-Authenticate': 'DPoP error="use_dpop_nonce"', 'DPoP-Nonce': _nonce})
    jti = decoded['jti']
    if not isinstance(jti, str) or not 1 <= len(jti) <= 200:
        raise failure()
    with _lock:
        for item, expires in list(_seen.items()):
            if expires <= now:
                del _seen[item]
        identifier = (thumbprint, jti)
        if identifier in _seen or len(_seen) >= 20_000:
            raise failure()
        _seen[identifier] = now + 125


def require_owner(request: Request) -> VerifiedOwner:
    config = tide_config.load()
    if not config:
        if request.headers.get('authorization'):
            raise failure()
        raise HTTPException(503, 'Secure history is not configured.')
    auth = request.headers.get('authorization', '')
    if not auth.startswith('DPoP ') or len(auth) > 32768:
        raise failure()
    token = auth[5:]
    try:
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'EdDSA':
            raise failure()
        candidates = [k for k in config['adapter']['jwk']['keys'] if k.get('kid') == header.get('kid')]
        if len(candidates) != 1:
            raise failure()
        claims = jwt.decode(token, jwt.PyJWK(candidates[0]).key, algorithms=['EdDSA'],
                            audience=config['client_id'], issuer=config['issuer'], leeway=5,
                            options={'require': ['exp', 'iat', 'iss', 'sub', 'aud']})
        if (claims.get('azp') != config['client_id'] or claims.get('typ') not in {'Bearer', 'DPoP'}
                or not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 512):
            raise failure()
        roles = claims.get('realm_access', {}).get('roles', [])
        if not isinstance(roles, list) or not {'_tide_enabled', '_tide_history.selfdecrypt'}.issubset(roles):
            raise HTTPException(403, 'Personal-history permissions are not active. Complete setup and sign in again.')
        verify_proof(request, token, claims, config)
        owner = digest(json.dumps([claims['iss'], claims['sub']], separators=(',', ':')).encode())
        return VerifiedOwner(owner, can_write='_tide_history.selfencrypt' in roles)
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError):
        raise failure() from None


def require_writer(owner: VerifiedOwner = Depends(require_owner)) -> VerifiedOwner:
    if not owner.can_write:
        raise HTTPException(403, 'This account has read-only access.')
    return owner
