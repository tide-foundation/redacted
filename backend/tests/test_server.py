from io import BytesIO
from pathlib import Path
from threading import Event, Timer
from types import SimpleNamespace

from docx import Document
from fastapi.testclient import TestClient
import pytest

from backend import app as service


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path / 'data')
    monkeypatch.delenv('PRIVACY_DEV_ORIGIN', raising=False)
    frontend = tmp_path / 'dist'
    frontend.mkdir()
    (frontend / 'index.html').write_text('<!doctype html><title>Redacted</title>')
    (frontend / 'assets').mkdir()
    (frontend / 'assets' / 'app.js').write_text('window.redacted = true;')
    # A static file with an API-looking path must not mask an unknown API route.
    (frontend / 'api' / 'service').mkdir(parents=True)
    (frontend / 'api' / 'service' / 'unknown').write_text('not an API response')
    (tmp_path / 'private.txt').write_text('private document content')
    (frontend / 'escape.txt').symlink_to(tmp_path / 'private.txt')
    return service.create_app(frontend)


def test_one_server_serves_frontend_and_api_without_exposing_private_files(server):
    with TestClient(server, base_url='http://127.0.0.1:3001') as client:
        page = client.get('/')
        assert page.status_code == 200
        assert '<title>Redacted</title>' in page.text
        assert page.headers['cache-control'] == 'no-store'
        assert page.headers['x-content-type-options'] == 'nosniff'
        asset = client.get('/assets/app.js')
        assert asset.status_code == 200
        assert asset.text == 'window.redacted = true;'
        assert client.get('/api/service/health').json()['service'] == 'ready'
        assert client.get('/api/service/guest/current').json()['document'] is None
        assert client.get('/api/service/documents').status_code == 404
        for route in ('/secure-history', '/secure-history/setup', '/disclaimer'):
            information_page = client.get(route)
            assert information_page.status_code == 200
            assert information_page.text == page.text
            assert information_page.headers['cache-control'] == 'no-store'
        for path in ('/api/service/unknown', '/api/service/documents/missing/preview'):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers['content-type'] == 'application/json'
        for path in ('/data/documents.sqlite3', '/models/checkpoint.pt', '/backend/app.py',
                     '/private.txt', '/%2e%2e/private.txt', '/assets/%2e%2e/%2e%2e/private.txt',
                     '/escape.txt', '/not-a-page'):
            response = client.get(path)
            assert response.status_code == 404, path
            assert 'private document content' not in response.text


@pytest.mark.parametrize('base_url', ['http://127.0.0.1:3001', 'http://localhost:3001', 'http://127.0.0.1:8000'])
def test_only_actual_same_origin_is_allowed(server, base_url):
    with TestClient(server, base_url=base_url) as client:
        assert client.get('/api/service/health', headers={'origin': base_url}).status_code == 200
        for origin in ('https://evil.example', 'null', 'http://127.0.0.1:5173', 'http://localhost:5173'):
            assert client.get('/api/service/health', headers={'origin': origin}).status_code == 403
        other_local_origin = 'http://localhost:3001' if base_url != 'http://localhost:3001' else 'http://127.0.0.1:3001'
        assert client.get('/api/service/health', headers={'origin': other_local_origin}).status_code == 403
        # Reject cross-site requests even when Origin is omitted or forged locally.
        for headers in ({'sec-fetch-site': 'cross-site'}, {'sec-fetch-site': 'cross-site', 'origin': base_url}):
            assert client.get('/api/service/documents', headers=headers).status_code == 403
        assert client.get('/', headers={'host': 'evil.example:3001'}).status_code == 403
        assert client.get('/', headers={'host': 'localhost.evil.example:3001'}).status_code == 403
        assert client.get('/', headers={'host': 'localhost:invalid'}).status_code == 403
        assert client.get('/api/service/health', headers={
            'origin': 'https://evil.example', 'x-forwarded-host': 'evil.example',
            'x-forwarded-proto': 'https', 'forwarded': 'host=evil.example;proto=https',
        }).status_code == 403
        assert client.get('/api/service/health', headers={
            'host': 'evil.example', 'x-forwarded-host': base_url.removeprefix('http://'),
        }).status_code == 403


def test_vite_origin_requires_explicit_exact_opt_in(server, monkeypatch):
    monkeypatch.setenv('PRIVACY_DEV_ORIGIN', 'http://127.0.0.1:5173')
    application = service.create_app(Path('/unused-frontend'))
    with TestClient(application, base_url='http://127.0.0.1:8000') as client:
        assert client.get('/api/service/health', headers={'origin': 'http://127.0.0.1:5173'}).status_code == 200
        assert client.get('/api/service/health', headers={'origin': 'http://localhost:5173'}).status_code == 403
        assert client.get('/api/service/health', headers={'origin': 'http://127.0.0.1:5174'}).status_code == 403
        assert client.get('/api/service/health', headers={
            'origin': 'http://127.0.0.1:5173', 'sec-fetch-site': 'cross-site',
        }).status_code == 403


@pytest.mark.parametrize('origin', ['https://evil.example', 'http://localhost:5173/path',
                                   'http://localhost:5173?test=1', 'http://localhost:5173/',
                                   'http://user@localhost:5173', 'http://localhost:invalid'])
def test_invalid_dev_origin_configuration_is_rejected(monkeypatch, origin):
    monkeypatch.setenv('PRIVACY_DEV_ORIGIN', origin)
    with pytest.raises(ValueError, match='PRIVACY_DEV_ORIGIN'):
        service.create_app()


def test_shutdown_finishes_accepted_work_and_restart_recreates_worker(server, monkeypatch):
    started, released = Event(), Event()

    class SlowDetector:
        _runtime = True

        def redact(self, text):
            started.set()
            assert released.wait(timeout=5)
            return SimpleNamespace(text=text, detected_spans=[], warning=None)

    monkeypatch.setattr(service, 'model', SlowDetector())
    original_find = service.importlib.util.find_spec
    monkeypatch.setattr(service.importlib.util, 'find_spec', lambda name: True if name == 'opf' else original_find(name))
    document = Document()
    document.add_paragraph('No personal details.')
    data = BytesIO()
    document.save(data)
    identifiers = []
    for _ in range(2):
        started.clear()
        released.clear()
        with TestClient(server, base_url='http://127.0.0.1:3001') as client:
            assert client.get('/api/service/guest/current').json()['document'] is None
            client.headers['X-CSRF-Token'] = client.get('/api/service/guest/current').json()['csrf_token']
            response = client.post('/api/service/guest/documents?type=.docx', content=data.getvalue())
            assert response.status_code == 202
            identifiers.append(response.json()['id'])
            assert started.wait(timeout=5)
            release = Timer(.1, released.set)
            release.start()
        release.join()
        assert service.pool is None
        assert service.active == 0
        assert not list((service.ROOT / 'guest').iterdir())
        assert service.guests.sessions == {}
        with service.history.connection() as connection:
            assert connection.execute('SELECT COUNT(*) FROM history_documents').fetchone()[0] == 0


def test_account_link_return_allows_navigation_but_not_cross_site_fetch(server):
    with TestClient(server, base_url='http://localhost:3001') as client:
        headers = {'sec-fetch-site': 'cross-site', 'origin': 'http://localhost:8080'}
        assert client.get('/secure-history/linked', headers=headers).status_code == 403
        headers['sec-fetch-mode'] = 'navigate'
        page = client.get('/secure-history/linked', headers=headers)
        assert page.status_code == 200
        assert '<title>Redacted</title>' in page.text
        assert page.headers['referrer-policy'] == 'no-referrer'
        assert client.post('/secure-history/linked', headers=headers).status_code == 403
        assert client.get('/api/service/tide/setup/v2/session', headers=headers).status_code == 403
