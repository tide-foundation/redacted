# Embedded TideCloak onboarding — live test handoff

Updated 2026-10-01 (Australia/Sydney). The shared-server test is complete and that server is now stopped. Redacted is back on its bundled, pinned TideCloak **0.14.38**, with a fresh **`redacted`** realm, app at **http://localhost:3001**, and data in **`./data`**. The fresh embedded setup completed all seven steps without a retry or console visit after the licence-response fix described below. All changes are saved locally, uncommitted, on branch `tidifcation`. Preserve the many earlier uncommitted app/history changes.

## User's requested outcome

After one initial terminal command, complete setup within Redacted via the TideCloak API; only the Tide identity/signing ceremony opens separately. Remove manual setup-code and owner-password entry from the browser. Keep owner authorization through a single-use, short-lived terminal-to-browser link. Show saved progress, automatically continue where possible and handle approvals inside Redacted.

The user authorized testing against their other, currently running TideCloak instance on port 8080, in a separate Redacted realm. Leave other applications' realms and services alone. They do not mind losing Redacted app data when switching instances later. The licensing email they supplied is `michael@tide.org`. They accepted licensing terms in the wizard and subsequently authorized Playwright to complete account linking and testing with their supplied test identity. Do not save its password here or in test scripts.

## Shared-server test environment (completed; now stopped)

- Running container `tidecloak`, image `tideorg/tidecloak-dev:latest`, version label **0.14.36**, port 8080, database mount `/home/mlo/medisure/data/keycloak`.
- Existing realms seen read-only: `eir`, `master`, `medisure`, `myrealm`.
- Admin authentication using this container's bootstrap environment succeeded. Never print or save those credentials; inspect/process them only in RAM when authorized.
- After Docker returned, the existing shared `tidecloak` container was started with its saved configuration. The updated `redacted-app-1` is deployed on port 3001. Its internal Tide URL is `http://host.docker.internal:8080`, public URL `http://localhost:8080`.
- The ignored local `.env` sets `REDACTED_DATA_DIR=./data/onboarding-demo` and the shared-server connection. This fresh test directory is mounted at `/app/data`; previous Redacted documents/configuration remain in the original `data` directory. The bundled `redacted-tidecloak-1` stays stopped.
- The user submitted the wizard and accepted licensing terms for `redacted-onboarding`. Playwright subsequently linked the supplied test identity, and the embedded setup automatically granted the first administrator and installed the adapter. Live progress is now **`ready`**, with all seven steps completed and no error or pending changes.
- A uniquely named `redacted-onboarding-probe-*` realm was created through the API to verify the new template, personal roles and login flow. It was then deleted and absence verified. Other realms unchanged. No licence, IGA, user linking or signing was invoked for the probe.
- Native Tide/Raziel MCP is available. Bootstrap and IGA canonical guidance was read. Some guidance contradicts the shipped API; the server's console JS was inspected read-only to verify actual approval behavior.

## Saved implementation

- `backend/tide_onboarding.py`: new `/api/service/tide/setup/v2` routes. A terminal-created filesystem permit authorizes a RAM-only bootstrap credential handoff; a separate URL-fragment capability is consumed into an HttpOnly/SameSite cookie and CSRF token. One-hour expiry, timer clears authority. JSON progress persists without credentials. Fresh realm creation guarded by a random `redacted.setup.id` marker so unrelated existing realms cannot be modified. Staged worker: realm → licence → app config/branding → account linking → first admin grant → verification/config install. Uses real API calls and supports enclave approval payloads; no production mock identity.
- `backend/tide_setup.py`: shared configuration function accepts an admin factory, optional deferred installation, setup callback URI and refreshing owner authentication. Old code-based routes and sessions have been removed.
- `backend/app.py`: includes new router, permits top-level navigation to setup callback from Tide, and uses validated provisional setup adapter for the bound DPoP relay before final installation.
- `src/TideSetup.tsx`: replaces old code/password form with a progress checklist, installation form, licensing terms acceptance, connect-account popup, automatic account-link checks, inline approval action, retry/resume and configured/offline views. Uses visible-only nonoverlapping polling. Consumes then removes setup capability from fragment.
- `src/tideSetupApproval.ts`: real installed TideCloak SDK, DPoP enabled, `requestTideOperatorApproval()` for pending request models, sends signed responses to backend. Not yet exercised against a live Tide identity.
- `scripts/tidecloak.py`: initial start/connect command performs credential handoff and opens a private setup link. `connect --container NAME` can read supplied Docker bootstrap credentials in memory; otherwise terminal username/password prompts. Port conflict check never stops another service. Removes old `setup-code` command. Configured installation prints ordinary setup link rather than a new setup code.
- `compose.yaml`: configurable `TIDECLOAK_PORT`, `TIDECLOAK_INTERNAL_URL` and `REDACTED_DATA_DIR`, plus host.docker.internal gateway mapping for another existing TideCloak container. The helper respects the data directory in `.env`.
- `src/globals.css`: checklist/checkmark/redaction-bar progress styling with reduced-motion support.
- New `backend/tests/test_tide_onboarding.py`; revised `scripts/browser-tide-setup.mjs` for new UI.

## Review fixes and verification completed

- Serialized provisioning, resume, link and approval mutations; bound resumes to the same app origin and owned realm; refreshed short-lived master tokens while retaining the one-hour owner-authority limit.
- Removed legacy code/password routes. Setup validation responses do not echo submitted credentials, and responses use `Referrer-Policy: no-referrer`.
- Checked the installed server console: account-link query parameters are `client_id` and `redirect_uri`, with a plain-text URL response. Fixed these and added a regression test. Confirmed create-time `tideInvitable` readback and supported first-admin attribute approvals before checking for the linked user key.
- Updated README, setup guide and integration brief. External-server UI offers the connect command instead of incorrectly starting the bundled server.
- Full backend suite: **182 passed** before the final two regression additions. Final focused auth/setup/onboarding run: **51 passed**, including **16 onboarding tests**. Log: `/tmp/redacted-onboarding-final-focused.log`.
- Full browser suite passed, including guest UI, history/storage fixtures, provider lifecycle, polling and embedded setup. Log: `/tmp/redacted-onboarding-all-browser.log`. Final setup browser regression also passed after the callback correction: `/tmp/redacted-onboarding-browser.log`.
- TypeScript/frontend build, final Docker image build and `git diff --check` passed. Screenshot `/tmp/redacted-embedded-setup.png` was visually reviewed.
- Live template probe passed and was cleaned up. Script `/tmp/redacted-onboarding-probe.py` contains no embedded credentials. Fixture tests do not establish live account-linking, signatures or encryption.

## Live Playwright results — 2026-10-01

Tested the running Docker app at `http://localhost:3001` and real Tide network using the user's explicitly supplied test identity. No mocked identity, encryption implementation or API responses were used.

- Resumed the existing realm with four completed steps, opened its account invitation from Redacted and completed the Tide sign-in/linking page. Setup advanced automatically through administrator confirmation to `ready`, without another console visit. First-admin bootstrap approvals used the supported server path; no separate human governance signature was required in this run.
- Normal login succeeded. The setup page's **Test encrypted history** performed a real encrypt/decrypt round trip and passed again after a reload.
- Uploaded an invented DOCX containing a name, date, email, phone and address. The model detected six fields. Mask mode produced results that saved automatically to encrypted history.
- SQLite contained six protected artifacts: filename, source, manifest, PDF, DOCX and TXT. Each had the expected encrypted envelope; the sample name, email, address and filename were absent from artifact bytes and public metadata. This observation accompanies the live successful decryption checks; a byte search alone is not a cryptographic proof.
- A cold page refresh fetched/decrypted only the filename, with no retry click. Opening Review detections showed six concealed originals and fetched no manifest. Reveal fetched one manifest containing all six originals. Repeated reveal, closing/reopening the report, and repeated original download caused no further artifact reads in that session.
- Downloaded Original, PDF, DOCX and TXT through the UI. The original DOCX matched the uploaded bytes exactly. All output formats opened, contained masks, and omitted the sample name/email.
- Sign-out hid the history; an unauthenticated history request returned HTTP 401. Signing in again restored the file name and allowed detection reveal.
- Cancelling the trash confirmation preserved the record. Confirming it removed the row and all six encrypted artifacts; deletion persisted after refresh. The test database now has zero history rows/artifacts. An empty draft left by an earlier test-monitor crash was separately removed by exact ID; this was test cleanup, not a claimed UI-deletion test.
- No production code change was needed in this final run. Browser permissions were the remaining operational issue: see the local-network note below. The test account password was not written into repository files or saved test scripts.

## Remaining acceptance scope

1. The user subsequently reported that the shared-server installation works with two users. Automated coverage used the supplied administrator identity; cross-user isolation has not been independently verified in that run.
2. The browser path for a change requiring an explicit governance signature remains untested live because this fresh realm completed under supported first-admin bootstrap approvals. Do not induce unrelated realm changes merely to claim that coverage.
3. The full fresh owner flow has now passed on pinned 0.14.38, including details/terms submission, licensing, permissions, branding, account linking and administrator confirmation. User acceptance and a second-user trial on this new installation remain.
4. The flow creates/resumes only a realm it owns. It does not adopt unrelated existing realms. Switching to a different TideCloak installation is not a migration of identities or ciphertext.

## Fixes from the first live wizard attempt

- The terminal connect command overlapped an app restart and reported a raw connection reset. Discovery now waits up to 30 seconds for readiness, retries transient connection/502/503/504 failures, and gives a useful command on failure. Credential handoff writes are never automatically replayed. Four CLI regression tests pass.
- A governed write returned 409 while an earlier change was pending. Embedded bootstrap applied the supported approval but the caller still raised the original error. It now retries the rejected write once after approvals clear; required enclave signatures still stop setup and persistent conflicts remain bounded. Regression tests cover all three outcomes.
- The live server returned HTTP 400 for administrator creation: `email` is required. Setup now includes the supplied licensing email, an unverified-email flag and default administrator display name. The worker fixture now enforces that email requirement. Live provisioning subsequently reached account linking.
- An automatic progress check could race the account-link click and take the setup lock. UI actions now wait for the in-flight check, polling yields to user actions, and a completed check refreshes busy state. Browser regression covers a click while the check is pending.
- The final focused CLI/setup/onboarding suite has **35 passing tests**. Frontend build and revised setup browser regression pass. A live browser handoff resumes the saved realm; the actual **Connect my Tide account** button returned HTTP 200 with a link on the expected local TideCloak origin. Account linking and document tests subsequently passed as recorded above; the separate interactive governance-signature path still needs live coverage.
- Follow-up observation: a headless hard reload immediately after a progress check began sometimes left the button disabled until the test timed out (30 seconds). Fresh navigation and settled progress checks worked, including the real button click. The delayed-check browser fixture passes; repeat the immediate-reload case when investigating further recovery behavior rather than claiming every live refresh timing has passed.
- The user subsequently reported seeing only the first step complete. The saved job still had four completed steps and current stage `link`. A separate same-tab resume defect was reproduced: changing only the URL fragment did not remount React, so a fresh owner link was ignored until manual reload. The setup page now listens for new setup-link fragments and reloads to exchange them and fetch saved progress. The regression opens a fresh link in an existing locked tab, verifies four checkmarks/current step five, and verifies no repeated realm creation. Frontend build and browser regression pass. The updated assets/index were copied into the running app without restarting the owner session; the Docker image is also rebuilt for future restarts. This proves the same-tab defect, not which page the user had previously open (clarification was requested).

## Useful references

- `/tmp/redacted-tide-provider.jar`: read-only copy of the running server's provider jar. Console bundle `tide-console/assets/index-DovPkRwn.js` shows real bootstrap wizard/API behavior.
- Installed SDK low-level method in `node_modules/@tidecloak/js/dist/esm/lib/tidecloak.js`: `requestTideOperatorApproval`, `initApprovalEnclave`. Requires authenticated doken. Its initializer uses `ApprovalEnclaveNew`, which supports bulk request models.
- Native MCP `tide_canon` names: `tidecloak-bootstrap`, `tidecloak-endpoints`, `iga-change-requests-api`; `tide_playbook('setup-iga-admin-panel')`. Prefer actual server code when older playbook snippets conflict.
- Docker commands in this environment use `DOCKER_CONFIG=/tmp/redacted-docker-config` to avoid an unavailable credential helper. During the historical shared-server run, only the app was restarted. Port 8080 now belongs to the bundled server; the shared server must remain stopped while that port is in use.

## Switching this test back to the original TideCloak

Requested by the owner during the live account test. These are local development settings, not a requirement to use the other application's TideCloak in a normal Redacted installation. The user subsequently authorized deleting the Redacted test realm/data, stopping the shared server, and testing a clean setup on the bundled server. See the reset log below.

| Setting | Current shared-server test | Original bundled installation |
| --- | --- | --- |
| TideCloak container | `tidecloak` (shared with the other app) | `redacted-tidecloak-1` (Compose `tidecloak` service) |
| Tested server image label | 0.14.36 | Pinned 0.14.38 image in `compose.yaml` |
| `.env` `TIDECLOAK_INTERNAL_URL` | `http://host.docker.internal:8080` | Remove the override, or set `http://tidecloak:8080` |
| `.env` `TIDECLOAK_PORT` | `8080` | `8080` to restore the existing original issuer; choose another port only for fresh realm setup |
| `.env` `REDACTED_DATA_DIR` | `./data/onboarding-demo` | `./data` to restore the original installation, or a fresh empty directory for a new realm |
| Realm | `redacted-onboarding` | Previously `redacted-first-test`; a new realm needs its own setup |
| Terminal owner handoff | `python3 scripts/tidecloak.py connect --container tidecloak` | `python3 scripts/tidecloak.py start` (uses `.tidecloak/bootstrap.env`) |
| TideCloak database | Other app's bind-mounted database | Original `redacted-tidecloak` named Docker volume |

The original `data/documents.sqlite3`, `data/tide/config.json` and bundled TideCloak volume have been retained. The original adapter points to `http://localhost:8080/realms/redacted-first-test`; the test adapter points to `http://localhost:8080/realms/redacted-onboarding`. Neither adapter nor encrypted documents should be copied between the two installations as a migration.

To restore the original installation later, first arrange for port 8080 to be free without disrupting the other application. Restore the `.env` settings above, recreate **only Redacted's app** with `docker compose up --build -d app`, and run `python3 scripts/tidecloak.py start`. This should use the retained original configuration and realm; verify login and encryption against that server rather than assuming the shared-server test proves it.

For a clean setup against the bundled server, first create a fresh directory (for example `mkdir -p data/original-onboarding`) and select it with `REDACTED_DATA_DIR=./data/original-onboarding`. Set `TIDECLOAK_INTERNAL_URL=http://tidecloak:8080`. If the other server must keep port 8080, set `TIDECLOAK_PORT=8090` **before creating the new realm**, then recreate the app and run the start helper. The new realm will use the new public issuer; this does not move the existing realm or its encrypted history to that port. Keep the old folders until the new test is accepted.

Changes that should remain on either server: required administrator email/display fields, correct account-link query parameters, bounded retries for rejected writes after supported bootstrap approval, short-lived admin-token refresh, serialized browser setup actions, same-tab resume-link handling and clearer terminal connection recovery. These are general setup fixes, not shared-server overrides. No governance, authentication or encryption checks were disabled.

Browser local-network permissions are also independent of the TideCloak version. The live Playwright test required permission for both `https://ork1.tideprotocol.com` and the embedding app `http://localhost:3001`. A temporary diagnostic change to the iframe's `allow` attribute was removed by reloading; encryption also passed with the SDK's original attribute, so no SDK/iframe patch is needed or retained. Do not disable browser security flags when switching servers.

## Bundled-server reset and fresh setup — 2026-10-01

Performed at the user’s explicit request. No passwords or private owner links are recorded here.

- Stopped Redacted, deleted only `redacted-onboarding` on the shared server after matching its setup ownership marker, and verified `eir`, `master`, `medisure` and `myrealm` remained. Stopped the shared `tidecloak` container; its database is retained.
- Cleared both the demo and original Redacted documents, SQLite databases and setup state. Preserved calibration and the `redacted-model` volume.
- Removed the shared-server internal URL override from ignored `.env` and restored `REDACTED_DATA_DIR=./data`. Rebuilt/recreated Redacted using the Compose default `http://tidecloak:8080`.
- Started the bundled pinned TideCloak 0.14.38. Deleted only its old `redacted-first-test` realm; preserved `master` and `myrealm`. The dedicated TideCloak volume was retained rather than erasing unrelated realms.
- Created fresh realm `redacted` through the embedded browser wizard. The resulting working installation remains running for user acceptance; do not wipe it again until requested.

Fresh-run defect found and fixed: `setUpTideRealm` returns HTTP 200 with plain-text `CREATED` (confirmed in the pinned server’s `VendorResource.RunVendorKeyLifecycle` implementation). The generic admin helper previously attempted JSON decoding and falsely reported a failed licence step after successful activation. The licence call now checks HTTP success without parsing an unused payload. The worker regression covers text success, JSON success and HTTP failure; the focused CLI/setup/onboarding suite passes **37 tests**. The incomplete test realm was removed and the corrected build then passed a fresh full setup. This fix applies to both TideCloak installations.

### Verification on bundled TideCloak 0.14.38

- Playwright drove the entire fresh wizard from the terminal owner handoff, including realm/email/registration/terms entry. Licensing and configuration continued automatically. Linked the authorized test identity in the Tide popup; all seven steps completed with no manual retry or console visit.
- Normal login and a real browser encrypt/decrypt round trip passed. Confirmed signup enabled, all three personal `_tide_*` default roles, `isIGAEnabled=true`, `iga.attestor=tide`, and zero pending changes.
- Uploaded the fictional DOCX, detected six fields and saved six encrypted artifacts. Checked envelope prefixes and absence of the sample sensitive plaintext from artifact bytes/public metadata.
- Cold refresh fetched/decrypted only the filename. Review fetched no manifest until Reveal, which fetched one bulk manifest for all six originals. Repeat reveal and Original download used the session cache.
- Original, PDF, DOCX and TXT downloads succeeded. Original was byte-identical; outputs opened and contained masks without the sample name/email. Logout hid history and anonymous history access returned HTTP 401.
- Restarted the app. Persisted setup remained configured; signing in restored the saved filename and detection reveal succeeded. Browser automation initially navigated before restart readiness and clicked the account menu during identity initialization; waiting for readiness resolved those test timing issues without a production change.
- Cancelled trash once and verified the record remained, then confirmed trash and verified deletion after reload. SQLite now contains **zero history documents and zero artifacts**.
- Final state: `redacted-app-1` healthy on port 3001, bundled `redacted-tidecloak-1` on port 8080, shared `tidecloak` stopped. Realm `redacted` remains ready for the user’s own test; no second wipe has been performed. Docker build, 37 focused backend tests and `git diff --check` passed.

The fresh test proved realm provisioning on the bundled image and persistent setup across an app restart. It did not recreate the whole TideCloak database volume: unrelated `master`/`myrealm` realms were deliberately preserved. The user’s earlier two-user success report applies to the shared-server test; this automated bundled-server run used their one supplied identity.

## User acceptance and final reset — 2026-10-01

The user confirmed the bundled installation works and requested another reset to perform setup themselves. Deleted only the owned `redacted` realm and verified `master`/`myrealm` remained. Cleared local Redacted SQLite history, guest files and Tide setup/configuration. Preserved the model cache, calibration and private bootstrap credentials. Both TideCloak containers are stopped; Redacted is running on port 3001 and reports unconfigured/unreachable TideCloak. Start the fresh owner flow from the project folder with `python3 scripts/tidecloak.py start`. This reset supersedes the ready-for-acceptance state above.

## Follow-up: browser launch, properties and first-user forms — 2026-10-01

The user completed fresh setup successfully, then reported WSL browser-launch failure and asked about document properties and first-time profile forms. Their follow-up specified a universal manual fallback and accepted stripping properties without model scanning.

- `scripts/tidecloak.py` always prints the complete private owner link. It tries the platform browser (Windows/macOS/Linux), uses Windows host integration on WSL, bounds launch waits, suppresses launcher noise and prints explicit copy/paste instructions on failure. `--no-browser` remains supported. URL data is never interpolated into shell/PowerShell code. The ordinary setup page opened through Windows PowerShell in the live WSL check; platform/failure/timeout behavior has unit coverage.
- Strengthened existing metadata stripping: remove Word core/extended/custom property parts and their relationships, and remove the entire PDF Info dictionary so custom keys cannot survive standard-key clearing. PDF XMP removal remains enabled. No property model scan or extra detection rows were added. Tested property-only private values with no model detections, all three modes, both input formats, all output formats and forced native-export fallback. Preserved image payload metadata remains outside this scope; Original remains unchanged.
- Read-only realm diagnostics showed licensing had made email mandatory and the default broker Review Profile remained REQUIRED, despite VERIFY_PROFILE being disabled. Added `backend/tide_signup.py`: after licensing, make email/name optional, disable profile required actions and only the broker profile-review execution. Preserve username validation, unique-user handling and existing-account verification. Settings are read back; governed/unapplied changes fail rather than silently passing. Applied and verified the same fix to the current owned `redacted` realm through normal admin APIs. No additional signed approval was required.
- Created a separate licensed temporary realm with the same template/settings. Playwright signed in with the authorized test Tide identity as its first ordinary user. The browser received a successful authorization code/state without seeing email/name/profile forms. API verification confirmed a linked Tide identity, no email/name/required actions and all personal encryption roles. Deleted the temporary realm afterwards. The isolated harness used the silent-SSO URI as a top-level callback; Redacted correctly rejected that unsupported top-level request. The authorization and user creation had completed; normal app sign-in uses the allowed root callback. This test verifies first-user profile behavior, not multi-admin registration approval policy.
- Backend suite: **214 passed**. Frontend build passed. Browser suite required starting its preview server on port 4173; its first attempt failed for that missing prerequisite, not an app assertion.
- Final verification: all browser regression scripts passed after starting preview, Docker rebuild/deploy passed, and the running app reports configured/reachable bundled TideCloak. The active realm and stored history were preserved.

## Requested reset after follow-up fixes — 2026-10-01

At the user’s request, deleted the currently owned `redacted` realm (ownership marker checked), preserving `master` and `myrealm`. Cleared Redacted SQLite documents/artifacts, guest outputs and Tide setup/configuration. Restarted the app and verified zero document/artifact rows and unconfigured secure history. TideCloak remains running; model cache, calibration and private bootstrap credentials are retained. The latest request did not ask to stop TideCloak. Start fresh with `python3 scripts/tidecloak.py start`.
