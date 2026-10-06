# Contributing to Redacted

Redacted is a Tide community project. Useful contributions come in many forms, and you can get started without running the model.

## Ways to help

- **Try it:** use invented sample documents and report missed detections, unwanted detections, formatting problems or confusing interactions.
- **Improve the experience:** suggest clearer wording, accessibility fixes, keyboard interactions or mobile layout improvements.
- **Make setup easier:** test the instructions on your machine, clarify an error or improve the documentation.
- **Add a focused regression:** turn a reproducible PDF, DOCX, session-isolation or UI bug into a small synthetic fixture and test.
- **Work on document handling:** help with layout preservation, metadata cleanup, export fidelity or future OCR coverage reporting.
- **Evaluate detection:** compare settings on representative synthetic documents and report the method and limitations alongside the results.

Use the repository's [issues](https://github.com/tide-foundation/redacted/issues) and [pull requests](https://github.com/tide-foundation/redacted/pulls) to discuss work. For larger changes, explain the problem and proposed behavior before committing to an implementation.

## Report a useful problem

Include what you expected, what happened, your operating system, Docker or direct setup, selected mode and sensitivity, and the relevant safe error message. A small **invented** PDF or DOCX that reproduces the problem is especially helpful.

Do not upload real confidential documents, real credentials, session cookies or private detected values. Check screenshots and logs before sharing them. For a security-sensitive issue, ask the maintainers for a private reporting route before publishing exploitable details.

## Work on the interface without the model

Use Node.js 22.12+ or 24 and run from the repository root:

```bash
npm ci
npm run build
npx playwright install chromium
npm run preview
```

Keep the preview running, then in another terminal:

```bash
npm run test:browser
```

The four browser scripts use fixture API responses at **http://127.0.0.1:4173**. They cover upload delays, settings, previews, reveal/hide cancellation, expiry, deletion, mobile layout, account states and navigation. They do not need Python or downloaded model weights. Prepared provider states in these tests are fixtures, not a working authentication integration.

For interactive changes, `npm run dev` starts Vite at **http://127.0.0.1:5173**. Real uploads require the Python backend and its explicit development origin; follow the [development setup](docs/development.md#frontend-hot-reload). `npm run build` includes TypeScript validation and refreshes the production files used by preview.

## Work on the backend

Follow the [Python dependency setup](docs/development.md#run-without-docker), then install the test dependencies and run:

```bash
.venv/bin/pip install -c backend/requirements.lock.txt -r backend/requirements.txt
.venv/bin/python -m pytest backend/tests -q
```

The complete suite needs the pinned OPF Python package, but **does not need downloaded model weights**. Tests inject deterministic model results and check parsing, replacements, layout, storage and API boundaries. They do not establish model accuracy.

For changes that need real inference, download the model and start the app as documented, then run:

```bash
BASE_URL=http://127.0.0.1:3001 .venv/bin/python scripts/model-smoke.py
```

Use port `8000` for a direct Python server. The script processes invented PDF/DOCX inputs, checks downloads and session boundaries, and removes its own guest work. It is an integration check, not an accuracy benchmark.

## Keep changes easy to review

Explain the concrete problem and resulting behavior in your pull request, including the checks you ran and any limits. Keep unrelated cleanup separate. Add a regression when it meaningfully protects against the bug; reuse existing fixtures and test scripts where practical.

Preserve the small upload-first interface and keyboard/accessibility behavior. Keep the production architecture simple: React is built into static files, and one FastAPI worker serves them and runs local inference. Do not introduce cloud processing or browser persistence for documents as a shortcut.

Guest originals, filenames and detection manifests are temporary. Changes to logging, persistence, expiry or account transitions must preserve that boundary. Never commit documents, model weights, generated outputs, database files or secrets. For secure-history work, read the [storage decision](docs/adr/001-secure-history-storage.md) and [integration status](docs/secure-history.md); do not substitute a fake account or plaintext persistence for Tide authentication and protection.

Use free, appropriately licensed fonts and assets, and include their notices. Contributions to Redacted-original code use the project's [MIT licence](LICENSE); dependencies retain their own terms. In particular, the current PyMuPDF/MuPDF runtime has AGPL obligations. Read [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before changing or redistributing dependencies.
