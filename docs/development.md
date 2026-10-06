# Development and operation

Start with the [README](../README.md) for Docker setup, or [CONTRIBUTING.md](../CONTRIBUTING.md) for ways to help and focused test workflows. This guide covers local configuration, storage and document-processing behavior.

## Updates and existing installations

**Before upgrading, export the results you want to keep.** Startup removes the old unowned `documents` table and UUID-named output folders from the original global-history version. These records are not assigned to the first guest or account. The model volume is unaffected.

Current guest results are temporary and clear on restart, replacement, deletion or expiry. After updating the checkout, rebuild with `docker compose up --build -d`. Keep `data/` and the `redacted-model` volume; removing volumes is not part of an ordinary update. Existing backups, snapshots and browser downloads are outside application cleanup.

## Docker settings

Run `mkdir -p data/guest` as your host user before the first Compose command. The container uses UID/GID 1000 by default. On Linux, use your account's IDs if different:

```bash
LOCAL_UID="$(id -u)" LOCAL_GID="$(id -g)" docker compose up --build -d
```

Copy `.env.example` to `.env` to retain `LOCAL_UID`, `LOCAL_GID` or `REDACTED_PORT` overrides. Apply the same IDs when building, running one-off commands and starting the app. Changing IDs after creating a model volume may also require correcting that volume's ownership.

Compose publishes the app only on `127.0.0.1:3001` by default. One Uvicorn worker serves the UI and API on container port 8000. Node is used only during the frontend build; the runtime image has no Node server or model weights.

The required Compose `model-init` service downloads missing assets into `redacted-model` and validates them before the app can start. App startup only checks existing assets and enables Hugging Face offline mode; it never downloads weights. Use `docker compose up --build --wait` to wait for setup and app health. Valid caches are reused without checking the hub. The health check reports HTTP availability, not model accuracy or successful inference. The model loads lazily on the first document.

### Reuse an existing model download

If `models/privacy-filter/` in this checkout already contains its `original/` checkpoint and `tiktoken/` cache, seed the volume before first startup:

```bash
mkdir -p data/guest
docker compose build
docker compose run --rm --no-deps --entrypoint python \
  --volume "$(pwd)/models/privacy-filter:/seed:ro" \
  app -c 'import shutil; shutil.copytree("/seed", "/models/privacy-filter", dirs_exist_ok=True)'
docker compose up -d
```

Use your UID/GID overrides for these commands if customized. Startup validates the copied assets. Model files are not included in application builds or committed to Git.

## Run without Docker

Use Python 3.11, Git and Node.js 22.12+ or 24. These Bash commands run from the repository root; Windows users can use WSL. Node builds the interface; Python serves it.

```bash
npm ci
npm run build
python3.11 -m venv .venv
.venv/bin/pip install -c backend/requirements.lock.txt torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -c backend/requirements.lock.txt -r backend/requirements-runtime.txt -r backend/requirements-model.txt
```

For backend tests, stop here and follow [the test instructions](../CONTRIBUTING.md#work-on-the-backend); no model weights are needed. To process real documents:

```bash
.venv/bin/python scripts/download-model.py

export OPF_CHECKPOINT="$PWD/models/privacy-filter/original"
export TIKTOKEN_CACHE_DIR="$PWD/models/privacy-filter/tiktoken"
export OPF_DEVICE=cpu
export HF_HUB_OFFLINE=1
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Open **http://127.0.0.1:8000**. The downloader defaults to `models/privacy-filter/` and reuses valid files. Set `PRIVACY_MODEL_DIR` or pass `--target` to change its destination, then point `OPF_CHECKPOINT` and `TIKTOKEN_CACHE_DIR` at the corresponding subdirectories. Use `scripts/download-model.py --check` to validate existing assets without downloading. Enable `HF_HUB_OFFLINE` after the initial download; unset it first if missing assets need fetching later.

Python reads shell environment variables; it does not automatically load `.env` or `.env.local`. `PRIVACY_DATA_DIR` overrides `data/`; `PRIVACY_FRONTEND_DIR` overrides `dist/`. The constraints reproduce the current Linux CPU dependency environment. CUDA needs a separately compatible PyTorch/CUDA installation and `OPF_DEVICE=cuda`; the supplied image and commands use CPU.

### Frontend hot reload

Keep the model environment variables above and start Python with an explicitly allowed development origin:

```bash
PRIVACY_DEV_ORIGIN=http://127.0.0.1:5173 \
  .venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

In another terminal, run `npm run dev` and open **http://127.0.0.1:5173**. Vite proxies `/api/service/*` to port 8000. Use that exact origin; production does not need `PRIVACY_DEV_ORIGIN`. Do not enable Python reload while processing documents: it resets temporary sessions and the loaded model.

### Checks

[CONTRIBUTING.md](../CONTRIBUTING.md) has the full build, backend, browser and real-model test commands. Browser scripts default to a Vite preview on port 4173; they can also run against built assets served by Docker with `BASE_URL=http://127.0.0.1:3001 npm run test:browser`. Their API responses are fixtures in either case.

The real-model smoke script defaults to port 3001 and accepts `BASE_URL` and `SENSITIVITY` overrides. OPF is pinned to commit `f7f00ca7fb869683eb732c010299d901457f19c3`. Fixture tests and a passing real-model smoke test do not validate detection accuracy across documents.

## Storage and privacy boundaries

| Content | Compose location | Lifetime |
| --- | --- | --- |
| Guest sessions, filenames and full detection manifests | Python process memory | Temporary session |
| Generated guest files | `/app/data/guest/<document-id>/` on tmpfs | Temporary session |
| Owner-scoped encrypted history database | Host `data/documents.sqlite3` | Persistent when TideCloak is configured |
| Sensitivity calibration values | Host `data/.calibration/` | Persistent; no document content |
| Model and tokenizer | Docker volume `redacted-model`, mounted at `/models/` | Persistent |

Original uploads are held temporarily in memory and are not intentionally saved as source files. The selected browser file is also held only in memory, never in localStorage or IndexedDB. Full manifests include detected original values and exact edits; they remain in bounded temporary server memory. The review interface fetches original values only on **Reveal values** and clears them when hidden, closed or expired.

Health polling runs every 60 seconds in a visible tab. Working-document polling runs every 2 seconds during processing and every 30 seconds while idle, pauses in hidden tabs, and refreshes on return/reconnect. Failed requests back off to 15/30/60 seconds.

A guest has one current document. Its one-hour deadline starts when an upload is accepted; polling does not renew it. Browser tabs sharing the session cookie share the working document. Closing a browser is not a reliable cleanup signal because browsers may restore session cookies. Replacement, deletion, expiry and server startup clear guest work.

Docker limits the guest tmpfs to 512 MiB; this is a ceiling, not reserved memory. The application limits retained results to 128 MiB per document and 256 MiB in total, with at most 64 guest sessions. Rendering can temporarily exceed retained-result limits before publication. Direct Python uses ordinary `data/guest/` filesystem storage with the same cleanup lifecycle; use an appropriately protected temporary filesystem if needed. Memory-backed storage can be swapped by the host, and cleanup is not a forensic-erasure guarantee.

Up to three jobs can be admitted, processed sequentially by one worker sharing one model. A guest cannot submit a second active job. Use one Uvicorn worker: extra workers would duplicate model memory and separate in-memory sessions and queues. Normal shutdown waits for admitted jobs and clears guest work; Compose allows ten minutes before forced termination. Expired or reset jobs cannot republish their results.

The Docker build applies `scripts/patch-opf.py` to the pinned OPF dependency. CPU loading retains safetensors-backed parameter storage instead of copying every weight into anonymous memory. CPU expert operations use batches of four tokens instead of 32; the context window, selected experts and weight precision are unchanged. The patch fails closed if the expected upstream code changes. Direct Python installations can apply the same patch with `.venv/bin/python scripts/patch-opf.py`. Run `scripts/model-smoke.py` against the service to check real inference and exports after changing this patch.

The local server and model see plaintext while processing. Generated files may retain missed sensitive text, private images or confidential body content. Concealment in the UI is not encryption. No external font service, analytics or document-processing API is used. Local Host and same-origin checks are enforced, and data/model directories are not served as static files. This release is intended for a trusted local machine, not a public or shared-network service.

The optional Tide integration stores filename, source, manifest and cached outputs as separately protected, owner-scoped artifacts. **Durable-history endpoints reject access until the owner completes Tide setup and the request passes authentication and proof verification.** There is no plaintext saving fallback. See the [storage decision](adr/001-secure-history-storage.md) and [current integration status](secure-history.md). The [architecture audit](architecture-audit.md) records an earlier baseline, not current operating instructions.

## Document fidelity and limits

The matching-format download attempts to preserve the source: DOCX for a Word upload and PDF for a PDF upload. The other format is a rebuilt text conversion. TXT and the preview contain extracted text. Hover a download button to see which export preserves layout.

- **Word:** edits existing XML text nodes, retaining paragraph/run styling, page setup, tables, headers, footers, notes, text boxes and images. Replacements spanning differently styled runs inherit the first run's style; longer text can reflow. Comments, deleted revision text, field instructions, external hyperlink destinations, custom XML and document properties are removed. Embedded objects, macros, charts, SmartArt and embedded HTML are rejected.
- **PDF:** physically removes detected glyphs with redactions, then inserts replacements at their locations. Page geometry and unaffected text/vector artwork remain. Replacement text approximates the original font family, style and color using standard fonts; exact embedded-font matching is not guaranteed. Text shrinks as needed to a 6 pt minimum. Unsupported rotated text, fit failures or other native-export failures fall back to a clean rewrite. Flatten interactive forms first. Metadata, annotations, attachments, scripts, links and bookmarks are removed; output is saved afresh with garbage collection rather than appended revisions.
- **Images:** native exports preserve images, which are not scanned. The detailed scan report warns about them. PDF image pixels beneath detected text redactions are blanked, but other visual information and image metadata may remain. Image-only scans need separate local OCR first; this app performs no OCR.
- **Limits:** 20 MiB per upload, 200,000 extracted characters and 250 source PDF pages. DOCX packages are limited to 100 MiB expanded and 10,000 entries. Legacy `.doc` needs conversion to `.docx`; encrypted PDFs are unsupported. Rebuilt PDFs are capped at 1,000 output pages.

Document properties are stripped independently of model detection in every mode. Word core, extended and custom property parts are removed; PDF document Info (including custom keys) and XMP metadata are removed. The Original download remains unchanged. This does not scan or strip metadata inside preserved image payloads.

A complete clean export is created before native-format editing. Native edits use a temporary file and replace the clean matching-format output only on success, preserving the fallback if layout editing fails. This applies to Mask, Label and Replace. Fixed masks hide original character counts in the replacement text, but preserved layouts can still reveal dimensions or spacing. Read the [full disclaimer](../DISCLAIMER.md).

## Sensitivity and fictional replacements

Sensitivity is an app-defined 0–100 scale in steps of 5. **50 preserves the model's default calibration**; higher values favor more detections and can flag ordinary text. It is not a confidence percentage or an independently validated operating point.

For other settings, `d = (sensitivity - 50) / 25` adjusts Viterbi transition biases: background-stay decreases by `d`, background-to-start increases by `d`, and inside-to-continue increases by `d / 2`. Each call selects its decoder while sharing the loaded model. Cached calibration files contain bias values only.

The model identifies spans; the app makes the replacements. All eight categories are used: `private_person`, `private_address`, `private_email`, `private_phone`, `private_date`, `private_url`, `account_number` and `secret`. Unsupported, overlapping or misaligned spans prevent an output being saved.

Replace mode maps `(category, original_text.casefold())` to an invented value. Repeated detections with that key receive the same replacement throughout the document, including headers and footers. Templates produce `Alex Example N`, `N Example Street, Sampletown`, reserved example email/URL domains, fictional-range US phone numbers, January 2000 dates, and `DEMO-…` or `SYNTHETIC-…` accounts and secrets. Email, URL, account and secret replacements include a random per-document token; the model does not generate identities.

These templates do not preserve locale, checksums, date intervals, identity relationships or name aliases. Date and phone sequences can cycle and collide. Case differences are ignored; whitespace differences are not. The temporary manifest records exact replacements and is cleared with the result. Internal mode keys remain `redact`, `placeholder` and `synthetic` for Mask, Label and Replace respectively.
