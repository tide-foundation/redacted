from contextlib import asynccontextmanager, suppress
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from starlette.staticfiles import StaticFiles

from backend.auth import VerifiedOwner, require_owner, require_writer
from backend import tide_config
from backend.tide_setup import router as tide_router
from backend.tide_onboarding import router as onboarding_router
from backend.documents import DocumentError, replacement_plan, export_with_fallback
from backend.guest import COOKIE, GuestStore, document_name, timestamp
from backend.history import HistoryMetadata, HistoryStore, ReplacementProjection, KINDS, MAX_ARTIFACT
from backend.layout import load_document
from backend.manifest import build_manifest, concealed_review
from backend.sensitivity import redact

ROOT = Path(os.environ.get('PRIVACY_DATA_DIR', './data')).resolve()
FRONTEND_ROOT = Path(os.environ.get('PRIVACY_FRONTEND_DIR', Path(__file__).resolve().parents[1] / 'dist')).resolve()
LIMIT = 20 * 1024 * 1024
pool = None
slots = Lock()
active = 0
model = None
guests = None
history = None


@asynccontextmanager
async def lifespan(app):
    global pool, active, guests, history
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    history = HistoryStore(ROOT)
    guests = GuestStore(ROOT / 'guest')
    active = 0
    pool = ThreadPoolExecutor(max_workers=1)

    async def sweep():
        while True:
            await asyncio.sleep(30)
            guests.cleanup()
            await asyncio.to_thread(history.cleanup)

    sweeper = asyncio.create_task(sweep())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        worker, pool = pool, None
        await asyncio.to_thread(worker.shutdown, wait=True)
        guests.close()


api = APIRouter(prefix='/api/service')


def run_job(session_key, identifier, data, suffix, mode, sensitivity=50):
    global model, active
    source = None
    try:
        if not guests.start(session_key, identifier):
            return
        source = load_document(data, suffix)
        text = source.text
        del data
        if model is None:
            from opf import OPF
            model = OPF(device=os.environ.get('OPF_DEVICE', 'cpu'), output_mode='typed')
        result = redact(model, text, sensitivity, ROOT / '.calibration')
        if result.text != text:
            raise DocumentError('The model changed the input text during tokenization. No output was saved because document positions would be unreliable.')
        sanitized, counts, edits = replacement_plan(result, mode)
        layout_preserved = export_with_fallback(sanitized, guests.root / identifier, source, suffix, edits)
        warnings = [result.warning, source.warning]
        if not layout_preserved:
            warnings.append('Original layout unavailable; clean rewrite used.')
        manifest = build_manifest(result, edits, mode=mode, sensitivity=sensitivity,
                                  source_type=suffix[1:], layout_preserved=layout_preserved, warnings=warnings)
        report = concealed_review(manifest)['scan_report']
        guests.finish(session_key, identifier, manifest=manifest, counts=counts,
                      warning=' '.join(report['warnings']) or None, layout=layout_preserved)
    except Exception as exc:
        # Parser/model exception strings can contain private input. Only expose
        # our deliberately authored validation errors, never raw exception data.
        safe = str(exc) if isinstance(exc, DocumentError) else 'Processing failed. Check that the model is installed, its checkpoint is available, and the document is readable.'
        guests.finish(session_key, identifier, error=safe)
    finally:
        try:
            if source is not None:
                source.close()
        finally:
            with slots:
                active -= 1


def guest_session(request: Request):
    return guests.session(request.cookies.get(COOKIE))[0]


def guest_mutation(request: Request):
    session = guest_session(request)
    guests.csrf(session, request.headers.get('x-csrf-token'))
    return session


async def read_bounded(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise HTTPException(413, 'The upload exceeds the size limit.')
        data.extend(chunk)
    if not data:
        raise HTTPException(400, 'The file is empty.')
    return bytes(data)


@api.get('/health')
def health():
    return {'service': 'ready', 'model_installed': importlib.util.find_spec('opf') is not None,
            'model_loaded': model is not None and model._runtime is not None,
            'device': os.environ.get('OPF_DEVICE', 'cpu')}


@api.get('/capabilities')
def capabilities():
    return {'secure_history': {'available': tide_config.load() is not None}}


@api.get('/identity')
def identity(owner: VerifiedOwner = Depends(require_owner)):
    return {'owner_id': owner.owner_id, 'can_write': owner.can_write}


@api.get('/guest/current')
def current(request: Request, response: Response):
    session, token = guests.session(request.cookies.get(COOKIE), create=True)
    if token:
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict',
                            secure=request.scope['scheme'] == 'https', path='/api/service')
    document = guests.current(session)
    return {'document': document, 'csrf_token': session.csrf,
            'expires_at': timestamp(session.expires) if document else None}


@api.delete('/guest/current')
def clear_current(response: Response, session=Depends(guest_mutation)):
    guests.reset(session)
    response.delete_cookie(COOKIE, path='/api/service', httponly=True, samesite='strict')
    return {'deleted': True}


@api.post('/guest/documents', status_code=202)
async def upload(request: Request, session=Depends(guest_mutation)):
    global active
    filename = document_name(request.headers.get('x-document-name'))
    mode = request.query_params.get('mode', 'redact')
    suffix = request.query_params.get('type', '')
    if mode not in {'redact', 'placeholder', 'synthetic'} or suffix not in {'.docx', '.pdf'}:
        raise HTTPException(400, 'Choose a PDF or DOCX and a supported replacement mode.')
    try:
        sensitivity = int(request.query_params.get('sensitivity', '50'))
    except ValueError:
        raise HTTPException(400, 'Sensitivity must be 0–100 in steps of 5.') from None
    if sensitivity not in range(0, 101, 5):
        raise HTTPException(400, 'Sensitivity must be 0–100 in steps of 5.')
    if pool is None:
        raise HTTPException(503, 'The service is shutting down. Please try again after restarting.')
    if importlib.util.find_spec('opf') is None:
        raise HTTPException(503, 'The local model is unavailable.')
    guests.check_available(session)
    with slots:
        if active >= 3:
            raise HTTPException(429, 'The local queue is full. Wait for a document to finish.')
        active += 1
    identifier = None
    try:
        data = await read_bounded(request, LIMIT)
        # Recheck under the registry lock after reading the body to serialize
        # simultaneous submissions from two tabs sharing the same cookie.
        identifier = guests.new_document(session, mode, sensitivity, suffix, filename)
        pool.submit(run_job, session.key, identifier, data, suffix, mode, sensitivity)
        return {'id': identifier}
    except BaseException:
        if identifier:
            guests.finish(session.key, identifier, error='Processing could not start. Please upload again.')
        with slots:
            active -= 1
        raise


@api.get('/guest/documents/{identifier}/preview')
def preview(identifier: str, session=Depends(guest_session)):
    folder = guests.lease(session, identifier)
    try:
        return {'text': (folder / 'sanitized.txt').read_text(encoding='utf-8')}
    finally:
        guests.release(identifier)


@api.get('/guest/documents/{identifier}/review')
def review(identifier: str, session=Depends(guest_session)):
    return concealed_review(guests.get_manifest(session, identifier))


@api.get('/guest/documents/{identifier}/correction')
def guest_correction(identifier: str, session=Depends(guest_session)):
    return guests.get_manifest(session, identifier)


@api.put('/guest/documents/{identifier}/correction')
async def save_guest_correction(identifier: str, request: Request, session=Depends(guest_mutation)):
    import json
    try:
        value = json.loads(await read_bounded(request, 16 * 1024 * 1024))
    except ValueError:
        raise HTTPException(422, 'Invalid review document.') from None
    return await asyncio.to_thread(guests.save_correction, session, identifier, value)


@api.get('/guest/documents/{identifier}/revealed-detections')
def reveal_detections(identifier: str, session=Depends(guest_session)):
    return {'values': guests.revealed_detections(session, identifier)}


@api.get('/guest/documents/{identifier}/detections/{category}/{occurrence}')
def reveal_detection(identifier: str, category: str, occurrence: str, session=Depends(guest_session)):
    # Explicit one-value reveal for the current guest session. The manifest stays
    # in RAM; no original is added to the default review, a URL or durable storage.
    if not occurrence.isascii() or not occurrence.isdecimal() or len(occurrence) > 6:
        raise HTTPException(404, 'Detection not found.')
    return {'original': guests.original(session, identifier, category, int(occurrence))}


@api.get('/guest/documents/{identifier}/protected-manifest')
def protected_manifest(identifier: str, owner: VerifiedOwner = Depends(require_owner), session=Depends(guest_session)):
    # Transient handoff for browser encryption. Both verified Tide identity
    # and possession of this working session are required.
    return guests.get_manifest(session, identifier)


class GuestFileResponse(FileResponse):
    def __init__(self, *args, store, identifier, **kwargs):
        super().__init__(*args, **kwargs)
        self.store, self.identifier = store, identifier

    async def __call__(self, scope, receive, send):
        # FileResponse skips its background callback for some Range errors and
        # disconnects. Always release the cleanup lease, including those paths.
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.store.release(self.identifier)


@api.get('/guest/documents/{identifier}/download/{extension}')
def download(identifier: str, extension: str, session=Depends(guest_session)):
    if extension not in {'pdf', 'docx', 'txt'}:
        raise HTTPException(404, 'Output not found.')
    if guests.document(session, identifier)['status'] != 'complete':
        raise HTTPException(404, 'Output not found.')
    folder = guests.lease(session, identifier)
    return GuestFileResponse(folder / f'sanitized.{extension}', filename=f'sanitized-{identifier[:8]}.{extension}',
                             store=guests, identifier=identifier)


@api.delete('/guest/documents/{identifier}')
def delete(identifier: str, session=Depends(guest_mutation)):
    guests.delete(session, identifier)
    return {'deleted': True}


@api.get('/history')
def list_history(owner: VerifiedOwner = Depends(require_owner)):
    return history.listing(owner)


@api.post('/history', status_code=201)
def create_history(metadata: HistoryMetadata, owner: VerifiedOwner = Depends(require_writer)):
    return history.create(owner, metadata)


@api.get('/history/{identifier}')
def read_history(identifier: str, owner: VerifiedOwner = Depends(require_owner)):
    return history.get(owner, identifier)


@api.delete('/history/{identifier}')
def delete_history(identifier: str, owner: VerifiedOwner = Depends(require_writer)):
    history.delete(owner, identifier)
    return {'deleted': True}


@api.put('/history/{identifier}/artifacts/{kind}')
async def upload_artifact(identifier: str, kind: str, request: Request, owner: VerifiedOwner = Depends(require_writer)):
    history.get(owner, identifier)
    if kind not in KINDS:
        raise HTTPException(400, 'Unsupported artifact kind.')
    if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
        raise HTTPException(415, 'Send protected artifacts as opaque binary data.')
    payload = await read_bounded(request, MAX_ARTIFACT)
    await asyncio.to_thread(history.put, owner, identifier, kind, payload)
    return {'stored': True}


@api.get('/history/{identifier}/correction')
def read_history_correction(identifier: str, owner: VerifiedOwner = Depends(require_owner)):
    return Response(history.correction(owner, identifier), media_type='application/octet-stream')


@api.put('/history/{identifier}/correction')
async def save_history_correction(identifier: str, request: Request, detail_count: int, revision: int,
                                  only_if_missing: bool = False, owner: VerifiedOwner = Depends(require_writer)):
    history.get(owner, identifier)
    if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
        raise HTTPException(415, 'Send browser-encrypted correction data.')
    payload = await read_bounded(request, MAX_ARTIFACT)
    return await asyncio.to_thread(history.save_correction, owner, identifier, payload, detail_count, revision, only_if_missing)


@api.put('/history/{identifier}/filename')
async def upgrade_history_filename(identifier: str, request: Request, owner: VerifiedOwner = Depends(require_writer)):
    history.get(owner, identifier)
    if request.headers.get('content-type', '').split(';')[0] != 'application/octet-stream':
        raise HTTPException(415, 'Send the protected filename as opaque binary data.')
    payload = await read_bounded(request, 64 * 1024)
    return await asyncio.to_thread(history.upgrade_filename, owner, identifier, payload)


@api.put('/history/{identifier}/replacements')
def add_history_replacements(identifier: str, projection: ReplacementProjection, owner: VerifiedOwner = Depends(require_writer)):
    return history.add_replacements(owner, identifier, projection)


@api.get('/history/{identifier}/artifacts/{kind}')
def get_artifact(identifier: str, kind: str, owner: VerifiedOwner = Depends(require_owner)):
    return Response(history.artifact(owner, identifier, kind), media_type='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="protected-artifact.bin"'})


@api.post('/history/{identifier}/commit')
def commit_history(identifier: str, owner: VerifiedOwner = Depends(require_writer)):
    return history.commit(owner, identifier)


@api.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
def missing_api_route(path: str):
    raise HTTPException(404, 'Not found.')


def is_local_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (parsed.scheme in {'http', 'https'}
                and parsed.hostname in {'localhost', '127.0.0.1'}
                and parsed.netloc == parsed.hostname + (f':{parsed.port}' if parsed.port is not None else '')
                and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def create_app(frontend_dir: Path = FRONTEND_ROOT):
    application = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith('/api/service/tide/setup/'):
            # FastAPI normally echoes invalid inputs, including raw passwords
            # before SecretStr validation. Never return those from setup APIs.
            return JSONResponse({'detail': 'Check the setup fields and try again.'}, status_code=422)
        return await request_validation_exception_handler(request, error)
    dev_origin = os.environ.get('PRIVACY_DEV_ORIGIN')
    if dev_origin and not is_local_origin(dev_origin):
        raise ValueError('PRIVACY_DEV_ORIGIN must be an exact localhost or 127.0.0.1 origin, including its port.')

    @application.middleware('http')
    async def local_only(request: Request, call_next):
        # Use the actual request Host, never X-Forwarded-Host or similar headers.
        server_origin = f'{request.scope["scheme"]}://{request.headers.get("host", "")}'
        origin = request.headers.get('origin')
        if not is_local_origin(server_origin):
            response = JSONResponse({'detail': 'Host not allowed.'}, status_code=403)
        elif ((origin and origin not in {server_origin, dev_origin})
              or request.headers.get('sec-fetch-site') == 'cross-site') and not (
                  request.method in {'GET', 'HEAD'} and (request.url.path.startswith('/tide_dpop/iss/')
                  or (request.url.path in {'/', '/secure-history/setup', '/secure-history/linked'} and request.headers.get('sec-fetch-mode') == 'navigate'))):
            response = JSONResponse({'detail': 'Origin not allowed.'}, status_code=403)
        else:
            response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    application.include_router(tide_router)
    application.include_router(onboarding_router)
    application.include_router(api)

    @application.api_route('/tide_dpop/iss/{issuer}/aud/{client}/tide_dpop_auth.html', methods=['GET', 'HEAD'])
    def dpop_relay(issuer: str, client: str, request: Request):
        config = tide_config.load()
        if not config:
            from backend.tide_onboarding import provisional_config
            config = provisional_config()
        try:
            if (not config or len(issuer) > 2048 or len(client) > 256
                    or bytes.fromhex(issuer).decode() != config['issuer']
                    or bytes.fromhex(client).decode() != config['client_id']):
                raise ValueError
        except (ValueError, UnicodeError):
            raise HTTPException(403, 'Issuer or client mismatch.') from None
        return FileResponse(frontend_dir / 'tide_dpop_auth.html', media_type='text/html', headers={
            'Content-Security-Policy': (frontend_dir / 'tide-dpop-csp.txt').read_text(),
            'Allow-CSP-From': '*',
        })


    @application.get('/tide_dpop_auth.html')
    def unbound_relay():
        raise HTTPException(404, 'Not found.')

    @application.get('/secure-history/linked')
    @application.get('/secure-history/setup')
    @application.get('/secure-history')
    @application.get('/disclaimer')
    def information_page():
        return FileResponse(frontend_dir / 'index.html')
    # Only the compiled frontend is public. Data and model directories stay outside
    # this mount; StaticFiles also blocks traversal and escaping symlinks.
    application.mount('/', StaticFiles(directory=frontend_dir, html=True, check_dir=False), name='frontend')
    return application


app = create_app()
