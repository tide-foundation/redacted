"""Bounded, expiring guest working state. Nothing in this registry is durable."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ntpath
from pathlib import Path
import secrets
import re
import shutil
from threading import RLock
import time
import unicodedata
from urllib.parse import unquote_to_bytes
from uuid import uuid4

from fastapi import HTTPException

COOKIE = 'redacted_guest'
TTL_SECONDS = 3600
MAX_SESSIONS = 64
MAX_RESULT_BYTES = 128 * 1024 * 1024
MAX_RETAINED_BYTES = 256 * 1024 * 1024


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def document_name(header: str | None) -> str | None:
    """Decode browser encodeURIComponent metadata without retaining a path.

    Names are display-only transient state, never filesystem keys, URLs, log
    fields or durable history metadata. Older callers may omit the header.
    """
    if header is None:
        return None
    invalid = 'The document filename is invalid or too long.'
    if (not header or len(header) > 3072 or not header.isascii()
            or re.search(r'%(?![0-9a-fA-F]{2})', header)):
        raise HTTPException(400, invalid)
    try:
        decoded = unquote_to_bytes(header).decode('utf-8', errors='strict')
    except UnicodeDecodeError:
        raise HTTPException(400, invalid) from None
    if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} for char in decoded):
        raise HTTPException(400, invalid)
    name = unicodedata.normalize('NFC', ntpath.basename(decoded)).strip()
    if not name or name in {'.', '..'} or len(name) > 255 or len(name.encode('utf-8')) > 1024:
        raise HTTPException(400, invalid)
    return name


@dataclass
class Session:
    key: str
    csrf: str
    expires: float
    document: dict | None = None
    manifest: dict | None = None


class GuestStore:
    def __init__(self, root: Path, *, ttl=TTL_SECONDS, maximum=MAX_SESSIONS, clock=time.time):
        self.root, self.ttl, self.maximum, self.clock = root, ttl, maximum, clock
        self.lock = RLock()
        self.sessions = {}
        self.running = set()
        self.leases = {}
        self.discard = set()
        self.sizes = {}
        self.max_result_bytes = MAX_RESULT_BYTES
        self.max_retained_bytes = MAX_RETAINED_BYTES
        if root.is_symlink():
            root.unlink()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        self._clear_files()

    def _clear_files(self):
        # The directory itself may be a Docker tmpfs mount and cannot be removed.
        for path in self.root.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()

    def _remove(self, identifier):
        self.discard.add(identifier)
        if identifier not in self.running and not self.leases.get(identifier):
            folder = self.root / identifier
            shutil.rmtree(folder, ignore_errors=True)
            # Do not forget files when deletion encounters a transient filesystem
            # error. They remain inaccessible, counted against quota, and retried.
            if not folder.exists():
                self.discard.discard(identifier)
                self.sizes.pop(identifier, None)

    def cleanup(self):
        with self.lock:
            for key, session in list(self.sessions.items()):
                if session.expires <= self.clock():
                    self.sessions.pop(key)
                    if session.document:
                        self._remove(session.document['id'])
                    session.manifest = None
            for identifier in tuple(self.discard):
                self._remove(identifier)

    def session(self, token, *, create=False):
        with self.lock:
            self.cleanup()
            key = hashlib.sha256(token.encode()).hexdigest() if token else ''
            if key in self.sessions:
                return self.sessions[key], None
            if not create:
                raise HTTPException(404, 'The guest session has expired. Add a file to start again.')
            if len(self.sessions) >= self.maximum:
                raise HTTPException(429, 'Temporary storage is busy. Please try again later.')
            token = secrets.token_urlsafe(32)
            key = hashlib.sha256(token.encode()).hexdigest()
            session = Session(key, secrets.token_urlsafe(32), self.clock() + self.ttl)
            self.sessions[key] = session
            return session, token

    def current(self, session):
        with self.lock:
            self._check_session(session)
            return dict(session.document) if session.document else None

    def _check_session(self, session):
        self.cleanup()
        if self.sessions.get(session.key) is not session:
            raise HTTPException(404, 'The guest session has expired. Add a file to start again.')

    def csrf(self, session, token):
        if not token or not secrets.compare_digest(token, session.csrf):
            raise HTTPException(403, 'Invalid session request. Refresh and try again.')

    def check_available(self, session):
        with self.lock:
            self._check_session(session)
            if session.document and session.document['status'] in {'queued', 'processing'}:
                raise HTTPException(409, 'Wait for the current document to finish.')

    def new_document(self, session, mode, sensitivity, suffix, filename=None):
        with self.lock:
            self.check_available(session)
            if session.document:
                self._remove(session.document['id'])
            identifier = str(uuid4())
            session.expires = self.clock() + self.ttl
            session.document = {'id': identifier, 'created': timestamp(self.clock()), 'mode': mode, 'filename': filename,
                                'sensitivity': sensitivity, 'source_type': suffix[1:], 'status': 'queued',
                                'counts': {}, 'error': None, 'warning': None, 'layout_preserved': None,
                                'expires_at': timestamp(session.expires)}
            session.manifest = None
            return identifier

    def document(self, session, identifier, *, ready=False):
        with self.lock:
            self._check_session(session)
            if not session.document or session.document['id'] != identifier:
                raise HTTPException(404, 'Document not found.')
            if ready and session.document['status'] != 'complete':
                raise HTTPException(409, 'This document is not ready.')
            return dict(session.document)

    def start(self, key, identifier):
        with self.lock:
            self.cleanup()
            session = self.sessions.get(key)
            if not session or not session.document or session.document['id'] != identifier:
                return False
            self.running.add(identifier)
            session.document['status'] = 'processing'
            return True

    def finish(self, key, identifier, *, manifest=None, error=None, counts=None, warning=None, layout=None):
        with self.lock:
            self.cleanup()
            session = self.sessions.get(key)
            valid = session and session.document and session.document['id'] == identifier
            if valid and not error:
                size = sum(path.stat().st_size for path in (self.root / identifier).iterdir() if path.is_file())
                if size > self.max_result_bytes or sum(self.sizes.values()) + size > self.max_retained_bytes:
                    error = 'Temporary output storage is full. Delete the current result or try a smaller document.'
                else:
                    self.sizes[identifier] = size
            if valid:
                session.document.update(status='failed' if error else 'complete', error=error,
                                        counts=counts or {}, warning=warning, layout_preserved=layout)
                session.manifest = manifest if not error else None
            if not valid or error:
                self._remove(identifier)
            self.running.discard(identifier)
            if identifier in self.discard:
                self._remove(identifier)

    def get_manifest(self, session, identifier):
        with self.lock:
            self.document(session, identifier, ready=True)
            return session.manifest

    def save_correction(self, session, identifier, value):
        from backend.corrections import validate
        from backend.documents import export_files
        with self.lock:
            self.document(session, identifier, ready=True)
            manifest = session.manifest
            counts = validate(value, manifest.get('text'))
            revision = (manifest.get('correction') or {}).get('revision', 0)
            if value['revision'] != revision:
                raise HTTPException(409, 'This review changed in another tab. Reopen it before saving.')
        temporary = self.root / ('review-' + str(uuid4()))
        try:
            export_files(value['redacted'], temporary)
            size = sum(p.stat().st_size for p in temporary.iterdir())
            with self.lock:
                self.document(session, identifier, ready=True)
                if session.manifest is not manifest or (manifest.get('correction') or {}).get('revision', 0) != revision:
                    raise HTTPException(409, 'The document changed. Reopen the review.')
                if self.leases.get(identifier):
                    raise HTTPException(409, 'Wait for the current download to finish, then save again.')
                if size > self.max_result_bytes or sum(self.sizes.values()) - self.sizes.get(identifier, 0) + size > self.max_retained_bytes:
                    raise HTTPException(413, 'The corrected output exceeds temporary storage limits.')
                folder = self.root / identifier
                backup = self.root / ('previous-' + str(uuid4()))
                folder.rename(backup)
                try:
                    temporary.rename(folder)
                except BaseException:
                    backup.rename(folder)
                    raise
                shutil.rmtree(backup)
                saved = {**value, 'revision': revision + 1}
                manifest['correction'] = saved
                session.document.update(counts=counts, layout_preserved=False, warning='Corrected downloads use a clean text layout.')
                self.sizes[identifier] = size
                return saved
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def revealed_detections(self, session, identifier):
        with self.lock:
            self.document(session, identifier, ready=True)
            if session.manifest.get('correction'):
                value = session.manifest['correction']
                raw = value['text'].encode('utf-16-le')
                counts, result = {}, []
                for d in value['details']:
                    counts[d['category']] = counts.get(d['category'], 0) + 1
                    result.append({'category': d['category'], 'occurrence': counts[d['category']],
                                   'original': raw[d['start'] * 2:d['end'] * 2].decode('utf-16-le'),
                                   'replacement': d['replacement']})
                return result
            # Explicitly project only the values requested by Reveal. Never copy
            # exact spans or future sensitive manifest fields into this response.
            return [{'category': item['category'], 'occurrence': item['occurrence'],
                     'original': item['original']} for item in session.manifest['detections']]

    def original(self, session, identifier, category, occurrence):
        with self.lock:
            self.document(session, identifier, ready=True)
            for item in session.manifest['detections']:
                if item['category'] == category and item['occurrence'] == occurrence:
                    return item['original']
            raise HTTPException(404, 'Detection not found.')

    def delete(self, session, identifier):
        with self.lock:
            self.document(session, identifier)
            self.check_available(session)
            session.document = None
            session.manifest = None
            self._remove(identifier)

    def lease(self, session, identifier):
        with self.lock:
            self.document(session, identifier, ready=True)
            self.leases[identifier] = self.leases.get(identifier, 0) + 1
            return self.root / identifier

    def release(self, identifier):
        with self.lock:
            self.leases[identifier] -= 1
            if not self.leases[identifier]:
                self.leases.pop(identifier)
            if identifier in self.discard:
                self._remove(identifier)

    def reset(self, session):
        with self.lock:
            self._check_session(session)
            self.sessions.pop(session.key)
            if session.document:
                self._remove(session.document['id'])
            session.manifest = None
            session.document = None

    def close(self):
        with self.lock:
            self.sessions.clear()
            self._clear_files()
            self.sizes.clear()
            self.discard.clear()
