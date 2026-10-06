from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sqlite3
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from docx import Document
import pymupdf
from backend import app as service
from backend.auth import VerifiedOwner, require_owner
from backend.history import HistoryStore, HistoryMetadata, KINDS
from backend.guest import GuestStore


def value():
    return {'v': 1, 'text': '😀 Alice and Alice.', 'mode': 'placeholder', 'revision': 0,
            'details': [{'start': 3, 'end': 8, 'category': 'private_person', 'label': '[NAME 1]', 'replacement': '[NAME 1]'}],
            'redacted': '😀 [NAME 1] and Alice.', 'detailCount': 1}


def completed(store, owner):
    row = store.create(owner, HistoryMetadata(source_type='docx', mode='placeholder', layout_preserved=True, protection_version=2))
    for kind in KINDS:
        store.put(owner, row['id'], kind, b'opaque-original')
    store.commit(owner, row['id'])
    return row['id']


def test_encrypted_correction_atomic_revision_audit_and_automation(tmp_path):
    store = HistoryStore(tmp_path); owner = VerifiedOwner('owner'); identifier = completed(store, owner)
    first = store.save_correction(owner, identifier, b'encrypted-review-and-outputs-v1', 2, 0)
    assert first['review_revision'] == 1
    with pytest.raises(HTTPException) as error:
        store.save_correction(owner, identifier, b'stale', 3, 0)
    assert error.value.status_code == 409
    store.save_correction(owner, identifier, b'encrypted-review-and-outputs-v2', 1, 1)
    store.save_correction(owner, identifier, b'automated-regeneration', 50, 0, True)
    assert store.correction(owner, identifier) == b'encrypted-review-and-outputs-v2'
    assert store.get(owner, identifier)['detail_count'] == 1
    assert store.listing(owner)[0]['review_revision'] == 2
    with store.connection() as conn:
        rows = conn.execute('SELECT * FROM history_review_audit').fetchall()
        assert len(rows) == 2 and rows[-1]['updated_by'] == 'owner' and rows[-1]['detail_count'] == 1
    with pytest.raises(HTTPException):
        store.correction(VerifiedOwner('someone-else'), identifier)
    with pytest.raises(HTTPException) as error:
        store.save_correction(VerifiedOwner('owner', can_write=False), identifier, b'forbidden', 1, 2)
    assert error.value.status_code == 403
    assert HistoryStore(tmp_path).correction(owner, identifier) == b'encrypted-review-and-outputs-v2'
    store.delete(owner, identifier)
    with store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM history_corrections').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM history_review_audit').fetchone()[0] == 0


def test_read_only_put_refused_and_json_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    principal = VerifiedOwner('owner')
    service.app.dependency_overrides[require_owner] = lambda: principal
    try:
        with TestClient(service.app, base_url='http://localhost:3001') as client:
            identifier = completed(service.history, principal)
            path = f'/api/service/history/{identifier}/correction?detail_count=1&revision=0'
            assert client.put(path, json={'text': 'must not be accepted'}).status_code == 415
            principal = VerifiedOwner('owner', can_write=False)
            assert client.get(f'/api/service/history/{identifier}').status_code == 200
            assert client.put(path, content=b'opaque', headers={'Content-Type':'application/octet-stream'}).status_code == 403
            assert client.delete(f'/api/service/history/{identifier}').status_code == 403
            principal = VerifiedOwner('owner')
            assert client.put(path, content=b'opaque', headers={'Content-Type':'application/octet-stream'}).status_code == 200
    finally:
        service.app.dependency_overrides.clear()


def test_guest_unicode_validation_revision_and_exports(tmp_path):
    store = GuestStore(tmp_path)
    session, _ = store.session(None, create=True)
    identifier = store.new_document(session, 'placeholder', 50, '.docx')
    folder = tmp_path / identifier; folder.mkdir(); (folder/'original').write_bytes(b'fixture')
    store.start(session.key, identifier)
    store.finish(session.key, identifier, manifest={'text':value()['text'], 'detections':[]})
    edited = value()
    for mutate in [lambda x:x['details'][0].update(start=1), lambda x:x['details'].append(dict(x['details'][0])), lambda x:x.update(text='changed original'), lambda x:x.update(redacted='wrong output')]:
        invalid=deepcopy(edited); mutate(invalid)
        with pytest.raises(HTTPException): store.save_correction(session, identifier, invalid)
    # Use plain supported text to check all file contents; Unicode offset
    # validation above uses a non-BMP prefix independently of PDF font support.
    edited['text']='Hi Alice and Alice.'; session.manifest['text']=edited['text']
    edited['redacted']='Hi [NAME 1] and Alice.'
    saved=store.save_correction(session, identifier, edited)
    assert saved['revision']==1
    assert store.get_manifest(session, identifier)['correction']==saved
    assert (folder/'sanitized.txt').read_text()==edited['redacted']
    assert '\n'.join(p.text for p in Document(folder/'sanitized.docx').paragraphs)==edited['redacted']
    with pymupdf.open(folder/'sanitized.pdf') as pdf:
        assert '[NAME 1] and Alice.' in ''.join(page.get_text() for page in pdf)
    assert not store.current(session)['layout_preserved']
    with pytest.raises(HTTPException): store.save_correction(session, identifier, edited)
