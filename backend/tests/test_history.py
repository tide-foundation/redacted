import sqlite3
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from backend import app as service
from backend.auth import VerifiedOwner, require_owner
from backend.history import HistoryMetadata, HistoryStore, KINDS, LEGACY_KINDS

BASE = 'http://127.0.0.1:3001'
METADATA = {'source_type': 'docx', 'mode': 'redact', 'sensitivity': 50,
            'counts': {'private_person': 1}, 'layout_preserved': True, 'protection_version': 2}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setenv('PRIVACY_DATA_DIR', str(tmp_path))
    with TestClient(service.app, base_url=BASE) as client:
        yield client
    service.app.dependency_overrides.clear()


def inject_verified_test_principals():
    # Tests inject the trusted boundary, never a production header-to-owner adapter.
    principals = {'Bearer fixture-a': VerifiedOwner('owner-a'), 'Bearer fixture-b': VerifiedOwner('owner-b')}
    def verified(request: Request):
        owner = principals.get(request.headers.get('authorization'))
        if owner is None:
            raise HTTPException(401, 'Invalid test credential.')
        return owner
    service.app.dependency_overrides[require_owner] = verified


def test_history_disabled_without_real_auth_and_invalid_credentials_fail_closed(client):
    assert client.get('/api/service/capabilities').json()['secure_history']['available'] is False
    assert client.get('/api/service/history').status_code == 503
    for credential in ('Bearer malformed', 'Basic fake', 'Bearer', 'owner-a'):
        assert client.get('/api/service/history', headers={'Authorization': credential}).status_code == 401
    assert client.get('/api/service/history', headers={'X-Owner-Id': 'owner-a'}).status_code == 503
    assert client.get('/api/service/guest/current').status_code == 200


def test_owner_scoping_for_every_operation_and_transactional_opaque_retention(client):
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    response = client.post('/api/service/history', json=METADATA)
    assert response.status_code == 201
    identifier = response.json()['id']
    path = f'/api/service/history/{identifier}'
    assert client.get('/api/service/history').json() == []
    assert client.post(path + '/commit').status_code == 409
    # Deliberately opaque fixture bytes, not a pretend encryption implementation.
    opaque = b'\x00\x80\xffopaque-protected-test-payload\x00'
    for kind in KINDS:
        assert client.put(path + f'/artifacts/{kind}', content=opaque, headers={'Content-Type': 'application/octet-stream'}).status_code == 200
        response = client.get(path + f'/artifacts/{kind}')
        assert response.content == opaque
        assert response.headers['cache-control'] == 'no-store'
    assert client.post(path + '/commit').status_code == 200
    assert len(client.get('/api/service/history').json()) == 1
    assert client.put(path + '/artifacts/source', content=opaque, headers={'Content-Type': 'application/octet-stream'}).status_code == 409
    with service.history.connection() as connection:
        row = connection.execute('SELECT owner_id FROM history_documents WHERE id=?', (identifier,)).fetchone()
        assert row['owner_id'] == 'owner-a'
    client.headers['Authorization'] = 'Bearer fixture-b'
    assert client.get('/api/service/history').json() == []
    assert client.get(path).status_code == 404
    assert client.delete(path).status_code == 404
    assert client.post(path + '/commit').status_code == 404
    for kind in KINDS:
        assert client.get(path + f'/artifacts/{kind}').status_code == 404
        assert client.put(path + f'/artifacts/{kind}', content=opaque, headers={'Content-Type': 'application/octet-stream'}).status_code == 404
    client.headers['Authorization'] = 'Bearer fixture-a'
    assert client.delete(path).status_code == 200
    with service.history.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM history_documents').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM history_artifacts').fetchone()[0] == 0
    assert b'Alice' not in service.history.path.read_bytes()


def test_safe_metadata_rejects_owners_originals_filenames_and_arbitrary_warnings(client):
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    for extra in ({'owner_id': 'other'}, {'filename': 'Alice.docx'}, {'original': 'Alice'},
                  {'counts': {'Alice': 1}}, {'warning_codes': ['Private value Alice']},
                  {'sensitivity': 51}):
        assert client.post('/api/service/history', json=METADATA | extra).status_code == 422
    assert b'Alice' not in service.history.path.read_bytes()


def test_draft_expiry_cascades_artifacts_and_owner_is_required(tmp_path):
    now = [1000.0]
    store = HistoryStore(tmp_path, clock=lambda: now[0])
    owner = VerifiedOwner('a')
    with pytest.raises(TypeError):
        store.listing('unverified-a')
    identifier = store.create(owner, HistoryMetadata(**METADATA))['id']
    store.put(owner, identifier, 'source', b'opaque')
    now[0] += 901
    store.cleanup()
    with pytest.raises(HTTPException) as error:
        store.get(owner, identifier)
    assert error.value.status_code == 404
    with store.connection() as connection:
        assert connection.execute('SELECT COUNT(*) FROM history_artifacts').fetchone()[0] == 0


def test_legacy_unowned_data_purged_without_touching_other_storage(tmp_path):
    identifier = str(uuid4())
    folder = tmp_path / identifier
    folder.mkdir()
    (folder / 'sanitized.txt').write_text('legacy plaintext')
    preserve = tmp_path / 'models'
    preserve.mkdir()
    (preserve / 'keep').write_text('model')
    with sqlite3.connect(tmp_path / 'documents.sqlite3') as connection:
        connection.execute('CREATE TABLE documents (id TEXT PRIMARY KEY)')
        connection.execute('INSERT INTO documents VALUES (?)', (identifier,))
    store = HistoryStore(tmp_path)
    assert not folder.exists()
    assert (preserve / 'keep').read_text() == 'model'
    with store.connection() as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='documents'").fetchone() is None
    assert b'legacy plaintext' not in store.path.read_bytes()


def test_artifact_bound_and_empty_content_rejected(client, monkeypatch):
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    identifier = client.post('/api/service/history', json=METADATA).json()['id']
    path = f'/api/service/history/{identifier}/artifacts/source'
    assert client.put(path, content=b'raw without correct content type').status_code == 415
    assert client.put(path, content=b'', headers={'Content-Type': 'application/octet-stream'}).status_code == 400
    monkeypatch.setattr(service, 'MAX_ARTIFACT', 4)
    assert client.put(path, content=b'12345', headers={'Content-Type': 'application/octet-stream'}).status_code == 413


def test_future_database_version_fails_closed_without_deleting_legacy_data(tmp_path):
    with sqlite3.connect(tmp_path / 'documents.sqlite3') as connection:
        connection.execute('PRAGMA user_version=999')
        connection.execute('CREATE TABLE documents (id TEXT)')
    with pytest.raises(RuntimeError, match='newer application'):
        HistoryStore(tmp_path)
    with sqlite3.connect(tmp_path / 'documents.sqlite3') as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='documents'").fetchone()


def test_restart_sweeps_legacy_orphans_even_after_migration_committed(tmp_path):
    store = HistoryStore(tmp_path)
    orphan = tmp_path / str(uuid4())
    orphan.mkdir()
    (orphan / 'sanitized.txt').write_text('orphan plaintext')
    HistoryStore(tmp_path)
    assert not orphan.exists()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_version_two_requires_separate_filename_and_legacy_upgrade_preserves_artifacts(client):
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    headers = {'Content-Type': 'application/octet-stream'}
    for version in (1, 2):
        record = client.post('/api/service/history', json=METADATA | {'protection_version': version}).json()
        path = '/api/service/history/' + record['id']
        originals = {kind: b'opaque-existing-' + kind.encode() for kind in LEGACY_KINDS}
        for kind, payload in originals.items():
            assert client.put(path + '/artifacts/' + kind, content=payload, headers=headers).status_code == 200
        if version == 2:
            assert client.post(path + '/commit').status_code == 409
            assert client.put(path + '/artifacts/filename', content=b'opaque-name', headers=headers).status_code == 200
        else:
            assert client.put(path + '/filename', content=b'opaque-name', headers=headers).status_code == 409
        assert client.post(path + '/commit').status_code == 200
        client.headers['Authorization'] = 'Bearer fixture-b'
        assert client.put(path + '/filename', content=b'opaque-name', headers=headers).status_code == 404
        client.headers['Authorization'] = 'Bearer fixture-a'
        assert client.put(path + '/filename', content=b'opaque-name').status_code == 415
        assert client.put(path + '/filename', content=b'x' * (64 * 1024 + 1), headers=headers).status_code == 413
        assert client.put(path + '/filename', content=b'opaque-name', headers=headers).json()['protection_version'] == 2
        assert client.put(path + '/filename', content=b'overwrite-attempt', headers=headers).status_code == 200
        assert client.get(path + '/artifacts/filename').content == b'opaque-name'
        assert client.get(path).json()['protection_version'] == 2
        for kind, payload in originals.items():
            assert client.get(path + '/artifacts/' + kind).content == payload
        assert client.put(path + '/artifacts/manifest', content=b'overwrite', headers=headers).status_code == 409


@pytest.mark.parametrize('mode', ['redact', 'placeholder', 'synthetic'])
def test_public_replacements_match_generated_output_without_original_values(client, mode):
    from types import SimpleNamespace
    from backend.documents import LABELS, replacement_plan
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    text = ' '.join('PRIVATE' + str(i) for i in range(len(LABELS)))
    spans = [SimpleNamespace(start=i*9, end=i*9+8, label=category) for i, category in enumerate(LABELS)]
    _, counts, edits = replacement_plan(SimpleNamespace(text=text, detected_spans=spans), mode)
    projection = [{'category': span.label, 'occurrence': 1, 'replacement': edit.text} for span, edit in zip(spans, edits)]
    response = client.post('/api/service/history', json=METADATA | {'mode':mode, 'counts':counts, 'replacements':projection})
    assert response.status_code == 201
    record = client.get('/api/service/history/' + response.json()['id']).json()
    assert record['replacements'] == projection
    assert 'PRIVATE' not in str(record)
    assert b'PRIVATE' not in service.history.path.read_bytes()


def test_public_replacement_validation_and_owned_legacy_upgrade(client):
    inject_verified_test_principals()
    client.headers['Authorization'] = 'Bearer fixture-a'
    item = {'category':'private_person', 'occurrence':1, 'replacement':'Alex Example 1'}
    data = METADATA | {'mode':'synthetic'}
    for bad in ([item | {'replacement':'Alice PRIVATE'}], [item | {'original':'Alice PRIVATE'}],
                [item | {'occurrence':2}], [item, item], [], [item | {'category':'private_email'}]):
        assert client.post('/api/service/history', json=data | {'replacements':bad}).status_code == 422
    record = client.post('/api/service/history', json=data).json()
    path = '/api/service/history/' + record['id']
    assert client.put(path + '/replacements', json={'replacements':[item]}).status_code == 409
    for kind in KINDS:
        assert client.put(path + '/artifacts/' + kind, content=b'original-ciphertext', headers={'Content-Type':'application/octet-stream'}).status_code == 200
    assert client.post(path + '/commit').status_code == 200
    client.headers['Authorization'] = 'Bearer fixture-b'
    assert client.put(path + '/replacements', json={'replacements':[item]}).status_code == 404
    client.headers['Authorization'] = 'Bearer fixture-a'
    assert client.put(path + '/replacements', json={'replacements':[item | {'replacement':'Alice PRIVATE'}]}).status_code == 422
    assert client.put(path + '/replacements', json={'replacements':[item]}).json()['replacements'] == [item]
    assert client.put(path + '/replacements', json={'replacements':[item | {'replacement':'Alex Example 2'}]}).json()['replacements'] == [item]
    for kind in KINDS:
        assert client.get(path + '/artifacts/' + kind).content == b'original-ciphertext'
    assert b'Alice PRIVATE' not in service.history.path.read_bytes()
