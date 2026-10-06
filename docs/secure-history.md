# Guest lifecycle and secure history

## Integration status

The application uses one FastAPI runtime and a static React/Vite frontend. Optional TideCloak setup, the browser encryption adapter and server-side token/proof verification are implemented. **Live realm licensing, admin account linking, governance approvals and real-account encryption tests still need to be completed on each installation.** See the [owner setup guide](tidecloak-setup.md) and [implementation brief](tidecloak-integration.md).

Guest mode needs no Tide infrastructure. There is no production demo identity, substitute local-account system, unverified JWT fallback or plaintext secure-history fallback. Raziel's MCP guidance and the installed SDK source informed the integration; the SDK assets and server image are pinned.

## Guest lifecycle

`GET /api/service/guest/current` establishes a random HttpOnly, SameSite=Strict session cookie and returns a CSRF token. The server retains only a digest of the session token. Each session can access one current job/result. Mutations require its CSRF token, and local Host/Origin/Fetch Metadata checks remain in place. HTTPS adds Secure to cookies; localhost HTTP is supported.

Source bytes, filenames and full detection manifests stay in transient server memory. The browser keeps the selected source `File` in memory until saving/cleanup. Guest outputs use Docker's 512 MiB tmpfs; direct Python uses a temporary directory with cleanup. Guest text and images may still contain sensitive information. Concealing values in the interface is not encryption.

The registry admits at most 64 sessions. Published outputs are capped at 128 MiB per result and 256 MiB total; the Docker tmpfs is the hard limit during rendering. Results expire one hour after upload, regardless of polling or processing duration. Replacement, trash, reset, expiry and restart remove them. A download already underway can finish before cleanup. Host swap, snapshots, browser-restored sessions and user downloads are outside the deletion guarantee.

The guest review projection excludes original values and exact offsets. One Reveal/Hide control requests all originals for the current session. Hide, close, tab hiding, expiry and identity changes cancel pending requests and clear displayed values. The individual-value endpoint remains for compatibility.

## Durable history and protection

SQLite is at `data/documents.sqlite3`. `history_documents` contains a verified internal owner identifier, UUID, timestamp, state, draft expiry, protection version and constrained metadata. `history_artifacts` stores opaque binary payloads; cascading deletion removes them with the document.

New documents use protection version 2 and require six protected artifacts: `filename`, `source`, `manifest`, `output_pdf`, `output_docx`, and `output_txt`. The browser applies Tide self-encryption with the `history` tag before uploading each one. Protected plaintext also binds the document ID, artifact kind and owner, so a swapped artifact is rejected after decryption. The filename is encrypted separately from the manifest, which includes originals, replacements and positions. No server decryption key is installed.

Unencrypted metadata is limited to source type, mode, sensitivity, category counts, layout status, warning codes and generated replacement values. The server validates replacement text against the app’s fixed Mask, Label and synthetic templates, and validates occurrence counts; it rejects originals, offsets and arbitrary text in that projection. Counts, timestamps and repeated synthetic values still reveal limited structure. The storage API treats artifacts as opaque; it cannot prove that an arbitrary authenticated client's bytes are ciphertext. Encryption enforcement is in the shipped browser provider, with a test that inspects every uploaded artifact and rejects plaintext persistence regressions.

Drafts are hidden from listings until all required artifacts commit. Completed artifacts are immutable. A narrow owner-scoped upgrade can add a protected filename to a version 1 document without modifying any existing artifact. Incomplete drafts expire after 15 minutes without renewal. Limits are 64 MiB per artifact, 192 MiB per document and 100 retained/draft documents per owner. Interrupted saves attempt to delete their draft. After a successful save, Redacted deletes the guest working copy; failed cleanup is shown explicitly.

The backend verifies EdDSA access tokens using trusted public keys installed by the owner. It checks issuer, audience, client, expiry and personal-history permissions. ES256 DPoP verification checks signature, key binding, method, URL, token hash, nonce and replay. Owner IDs are derived from verified issuer/subject, never supplied by the client. Every repository operation is owner-scoped.

## Browser behavior

Guest mode remains the primary upload/settings/result experience. The account icon opens the introduction at `/secure-history` while unconfigured. Owner instructions live separately at `/secure-history/setup`. Configured installations offer Sign in or Sign out; signed-in users have no redundant settings link. Login requires a fresh working session. Download a guest result before signing in, then upload again; plaintext is never persisted to bridge a full-page login.

Signed-in results save automatically into one Files list, newest first, alongside the current working file. A detection report expands immediately below its own record. Count badges use safe category metadata. Reprocessing is not supported.

Listing metadata and opening Review detections do not decrypt the detection manifest. Each visible filename decrypts its own small artifact. Review uses category counts and generated replacement metadata without decrypting originals. The replacement column stays visible when originals are hidden. Reveal values decrypts the complete manifest for that document in one Tide call, returning all original values together; it does not decrypt every record in the history. Original and each output download decrypt only their requested artifact. Filename and value skeletons occupy the eventual text positions while waiting; download actions show an in-place progress animation.

A per-tab memory cache retains decrypted blobs for the signed-in session, with a 256 MiB least-recently-used limit. Concurrent requests for the same artifact share a decryption; repeat actions reuse it until eviction. HTTP downloads may run in parallel, but Tide SDK encryption/decryption calls use one queue per provider. The pinned RequestEnclave routes replies by operation type rather than a unique request ID, so overlapping decrypt calls can consume the same response and plaintext buffer. Each detection manifest still decrypts all originals for its document in one operation. Queued work is checked for cancellation and identity changes before entering the enclave; failed operations do not block later requests. Hide, close and tab hiding clear displayed values and cancel the UI action without discarding the session cache. Logout, identity changes, authentication failure and reload discard the cache; deletion discards that document's entries. Nothing is saved to browser storage. Cancelled identity operations cannot repopulate the cache with late decryption results.

Version 1 documents remain readable. Their combined manifest contains the filename and detected originals, so they initially show a document ID instead of automatically decrypting that manifest. The first explicit Reveal can add a separate encrypted filename and upgrade the format to version 2, preserving existing ciphertext. If that optional upgrade fails, Reveal still works and a later Reveal can retry it. Old Mask and Label replacements are reconstructed directly from their categories. Older Replace documents need one explicit Reveal before their actual synthetic values can be published as validated metadata; no automatic manifest decryption is used to obtain them.

Access tokens and document plaintext are not saved to browser persistence. The SDK uses browser storage for its OAuth state and DPoP key lifecycle, not document content. Logout is propagated across tabs and clears identity state; SDK logout clears its proof keys and enclave. Revealed data and pending operations are fenced against identity changes. JavaScript/Python memory release is not guaranteed physical erasure.

Health checks run once on load and every 60 seconds while visible. Working-document checks run every 2 seconds during processing and every 30 seconds otherwise. Polling pauses in hidden tabs and refreshes immediately on returning, focus or reconnect. Failures back off to 15, 30 and then 60 seconds; a slow request never creates overlapping requests. Explicit upload/delete/reset actions and local expiry still update immediately.

## Relevant APIs

All API routes use `/api/service` and return `Cache-Control: no-store`.

| Route | Boundary |
| --- | --- |
| `GET /health`, `/capabilities` | Local service status and configuration availability |
| `GET /tide/status`, `/tide/config` | Optional-service status and installed public adapter |
| `POST /tide/setup/unlock` | One-use terminal code, 30-minute expiry |
| `POST /tide/setup/configure` | Owner setup cookie + CSRF; disabled after installation |
| `GET /identity` | Verified Tide identity and request proof |
| `GET/DELETE /guest/current` | Session read/reset; CSRF on reset |
| `POST /guest/documents` | Session + CSRF; bounded raw file upload |
| `GET /guest/documents/{id}/preview`, `/review`, `/revealed-detections`, `/download/{format}` | Current-session result only |
| `GET /guest/documents/{id}/protected-manifest` | Verified Tide identity plus current guest session |
| `DELETE /guest/documents/{id}` | Current-session deletion + CSRF |
| `GET/POST /history` | Verified owner listing / bounded draft creation |
| `GET/DELETE /history/{id}` | Verified owner read/deletion |
| `PUT/GET /history/{id}/artifacts/{kind}` | Verified owner opaque artifact transfer |
| `POST /history/{id}/commit` | Verified owner; six artifacts required for version 2, five for version 1 |
| `PUT /history/{id}/filename` | Verified owner; add a bounded protected filename to a completed version 1 record |
| `PUT /history/{id}/replacements` | Verified owner; add validated generated replacements to an older completed record, once |

The SDK's enclave relay has a separate, issuer/client-bound route and its exact upstream CSP. Only that embed and top-level app navigation may cross the usual Fetch Metadata boundary; cross-site API requests remain rejected.

## Validation and limits

Automated verification includes the existing document/guest/history suite, real-signature JWT/DPoP rejection tests, owner isolation, one-use setup tests and browser lifecycle tests. A separate browser fixture uses actual WebCrypto to test the application's storage boundary, swapped-artifact rejection, interrupted-save cleanup and late-decryption cancellation. That fixture is not a live Tide cryptography test.

Real Tide sign-in, default-role approval, cross-user encryption isolation, enclave branding, recovery after sign-out and second-user self-registration remain live acceptance checks. Never present fixture success as proof of these. The app remains local-only; exposing it remotely requires explicit origin, HTTPS, proxy, quota and operational changes.

Legacy unowned history is removed on upgrade and is never assigned to a new user or claimed to be encrypted. Back up current owned ciphertext and TideCloak state together; see the setup guide. The [storage ADR](adr/001-secure-history-storage.md) records why source and cached outputs are protected independently.

## Review corrections

Open **Review detections → Review and correct**. Original shows the extractable text with dotted underlines. Select text and choose **Hide selected text**, or click/focus an underline and press Enter/Space to unhide only that occurrence. Overlapping detections are absorbed by a manual selection. **Added by you** groups manual spans. Distinct values share numbered labels; names with exactly one unambiguous longer detected name share its label. Mask, Label and Replace modes remain available. Nothing is sent until **Save changes**.

Review spans use UTF-16 browser string offsets. The browser validates ordering, bounds, non-overlap and surrogate-pair boundaries whenever reading or saving a correction, and converts legacy detector code-point offsets explicitly. Older manifests can reconstruct their original text from their exact replacements and the decrypted text output; inconsistent data is rejected.

Guest corrections remain in the expiring session. The server checks the original text and span boundaries, regenerates downloads, and swaps the result only after all exports succeed. A download in progress blocks replacement until it finishes. Guest review does not make a durable history record.

History corrections are encrypted in the browser with the same Tide `history` tag and owner/document binding as the source. One encrypted bundle contains the review (original text, spans, labels and redacted text) and regenerated PDF/DOCX/TXT outputs. The server atomically replaces this opaque bundle in `history_corrections`. Only distinct-detail count, revision, verified updater and timestamp are additional plaintext metadata. `history_review_audit` records each save's revision and count, never contents. Original artifacts remain immutable and recoverable; download endpoints in the browser adapter use the latest reviewed bundle. Saved corrections rebuild a text layout, without original images or formatting. The browser PDF export rejects unsupported font characters instead of silently losing them; a failed export/encryption leaves the previous revision untouched.

`GET /history/{id}/correction` requires verified ownership and decrypt permission. `PUT /history/{id}/correction?detail_count=N&revision=N` additionally requires encrypt/write permission, an opaque binary body, and the current revision. Stale writes return 409. The optional `only_if_missing=true` path leaves an existing correction unchanged, protecting manual work from automation. New automatic document ingestion never updates an existing history ID. Read-only accounts can open Original/Redacted and see counts; mutation controls are hidden and the backend rejects writes independently. Clearing a document deletes its correction and count-only audit rows.
