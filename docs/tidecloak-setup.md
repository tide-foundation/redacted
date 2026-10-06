# Enable TideCloak and encrypted history

This is an optional integration. Ordinary `docker compose up -d` starts Redacted only. The local app and its documents remain usable in guest mode when TideCloak is stopped. Authentication and personal encryption require access to Tide's network.

## Owner setup

Bash and Docker Compose 2.24 or newer are required on the host; Python runs inside the Redacted container. Run commands from your Redacted checkout after building and starting Redacted with `docker compose up --build --wait`. macOS and Linux provide Bash; on Windows use WSL.

To download and create TideCloak without starting it:

```bash
bash scripts/tidecloak.sh prepare
```

To start it when you are ready:

```bash
bash scripts/tidecloak.sh start
```

The same `start` command handles a bundled or externally configured TideCloak and already-completed setup. For an external server that is not configured yet, it prompts for owner credentials. The command opens (or prints) a private, single-use link to [Redacted setup](http://localhost:3001/secure-history/setup). There is no setup code or administrator password to copy into the browser. Keep that link private: it can be exchanged once within one hour for a seven-day setup session in that browser.

The complete link is always printed. The helper attempts to open the default browser on macOS/Linux, or `wslview` on WSL when installed. If no browser opens, paste the **entire** link, including `#setup=` and everything after it, into a browser on the same computer. Use `bash scripts/tidecloak.sh start --no-browser` for manual opening. This also works with `connect`. No host Python, Node, curl or jq is needed: Bash handles Docker commands and the container handles credentials and the private setup handoff.

Complete the remaining steps inside Redacted:

1. Enter your email (used for Tide licensing and the setup administrator), choose whether to allow sign-ups, and review/accept [Tide’s Terms and Conditions](https://tide.org/legal) and read its [Privacy Policy](https://tide.org/privacy).
2. Redacted creates the realm, activates Tide, and prepares the login client, personal-history permissions and supplied branding. The four-step checklist sits beside the current task (below it on narrow screens). Login and encryption configuration is included in activation; it does not import guest results.
3. Select **Connect my Tide account**. Complete the Tide account-linking window. Its return page tells you to close it and return to the original setup window, where Redacted checks for completion automatically.
Administrator confirmation and signed-configuration checks run automatically after account linking. The original setup window then attempts a standard SSO sign-in with PKCE and DPoP using your existing Tide session. After Redacted verifies the session, it opens the redaction screen directly. If setup is complete but you are signed out, the setup page shows **Setup complete** and **Sign in to start redacting**. Manual sign-in also returns directly to the app. If SSO needs another interaction, use the sign-in action shown there. The account-link popup carries no app tokens back to the original window.

4. If a signature is required, select **Review and approve with Tide** inside Redacted. The actual Tide enclave presents the signing request. Redacted submits the signed response and continues when quorum is satisfied. No routine console visit is needed.
5. Once signed in, test an invented sample document, sign out/in, and check saved downloads and detection reveal.

The app configures personal encryption, login settings and branding before granting the first realm administrator. It uses Tide’s supported first-admin bootstrap approvals; once Tide requires a human enclave signature, setup stops for that signature. It never disables governance to finish a step. The default encryption permissions apply to ordinary users; administrator rights are separate.

New users sign in with Tide without a separate Keycloak username/email/name form. After licensing, setup makes email and name optional, disables profile required actions and disables the broker’s profile-review step. Tide supplies the account identifier; account uniqueness and existing-account verification remain enabled. No placeholder email or name is invented for ordinary users.

Progress is saved in `data/tide/onboarding.json`. Returning in the same browser resumes your current step for seven days, including after browser or app restarts. After that fixed expiry, clearing cookies, or switching browsers, rerun the initial command to restore access. The checklist resumes the existing realm instead of creating it again. Setup automatically uses `redacted` when available, or a free name such as `redacted-2`. Existing realms are never adopted or changed; interrupted setup resumes only a realm with its matching ownership marker.

The helper proves terminal access using a short-lived filesystem permit. It sends the bootstrap credentials directly to the local backend. After the private link is exchanged, the backend seals the resume authority (including those credentials) in `data/tide/owner-resume.enc`, an owner-readable file encrypted with Fernet using a domain-separated key derived from the random browser cookie. The encryption key is not saved on the server. The browser receives a persistent HttpOnly, SameSite=Strict setup cookie and a CSRF token. Only that cookie can restore authority after a restart; the record is bound to the original app and TideCloak addresses and expires after seven days. Completion, expiry or a new terminal handoff invalidates the resume record. No plaintext credentials or bearer tokens are written to the progress file. The private link is removed from the address bar immediately after use; it is not put into browser storage or server access-log URLs. The app never mounts Docker’s socket. Successful configuration closes the setup API.

Owner credentials for the bundled service live in `.tidecloak/bootstrap.env` (owner-readable, ignored by Git and Docker builds). Compose supplies these credentials to TideCloak; the helper also streams them through stdin to the running app for the one-time handoff. The app does not mount the file. Keep it for maintenance; replacing it does not rotate an existing administrator’s password.

### Connect a TideCloak that is already running

Redacted can create its own realm on another local TideCloak server. Existing realms are left alone. For a Docker app connecting to a host service on port 8080, put this in `.env` and recreate **only the app**:

```dotenv
TIDECLOAK_PORT=8080
TIDECLOAK_INTERNAL_URL=http://host.docker.internal:8080
```

```bash
docker compose up --build -d app
bash scripts/tidecloak.sh start
```

The connect command prompts for that server’s owner credentials in the terminal, then opens the authorized wizard. If it is your local Docker container and its bootstrap password is still current, use `bash scripts/tidecloak.sh connect --container tidecloak` to read those credentials in memory instead. No other container is stopped or reconfigured.

For a Python app outside Docker, export `TIDECLOAK_INTERNAL_URL=http://localhost:8080` and `TIDECLOAK_PUBLIC_URL=http://localhost:8080` before starting it. The Bash helper is for Docker installations and uses the Compose port and data-directory settings automatically. For source development outside Docker, the legacy `python3 scripts/tidecloak.py` helper accepts `--app-url` and `--data-dir`.

To use a different port for a **new bundled installation**, set `TIDECLOAK_PORT=8090` (or another free port) in `.env` before creating its realm, then recreate the app and run the start helper. A port collision produces an explanation; the helper never stops whatever owns that port. Changing an established issuer/port or switching TideCloak instances is not a migration of identities or ciphertext. Keep an existing installation’s addresses stable.

## Browser permission for local hosting

When the hosted Tide sign-in page asks to access your local network, allow it for
the configured Tide origin. Also allow this permission for Redacted's local app
origin when its embedded encryption window requests it. The enclave needs to load the local DPoP relay and
branding images from localhost. In Chrome, denying this permission can leave the
login form visible while blocking its local security handshake and images.
The local smoke check verified the relay and both branded images load once the
permission is granted. See [Chrome's Local Network Access permission](https://developer.chrome.com/blog/local-network-access).
Do not disable browser security checks globally.

## Accounts and documents

Sign in **before** uploading. A full-page login clears the working session; guest documents and their original source do not silently transfer through browser storage. Download the guest result first, then upload again after signing in.

For a signed-in user, completed results are encrypted and saved automatically. Once committed, the temporary working copy is deleted; if cleanup fails, the app asks the user to trash that copy. Source, filename, detection manifest and each output format are independently encrypted using the user's Tide `history` permissions. An interrupted save is discarded where possible; abandoned drafts expire after 15 minutes. A save failure leaves the temporary result available for download/retry until guest-session expiry.

Durable ciphertext is stored in `data/documents.sqlite3`. Metadata (time, format, mode, sensitivity, category counts and generated replacement values) is not encrypted. The browser decrypts filenames individually for the Files list. Opening Review detections reads category counts and generated replacements; Reveal values decrypts all originals for that document together, in one manifest operation. Original and output downloads decrypt only the selected artifact. Decrypted items are cached in tab memory until sign-out, reload or eviction (256 MiB limit). Older documents initially show a document ID; their first explicit Reveal adds a separate encrypted filename without changing existing ciphertext. There is no Reprocess action.

Default encryption roles are approved during initial setup. Additional administrators are never automatically granted. Self-registration is configurable; **unattended registration with QEA, including account linking without further admin approval, must be verified against the installed release**. Do not promise this until a second user passes that check.

## Start, stop and storage

```bash
bash scripts/tidecloak.sh stop
bash scripts/tidecloak.sh start
bash scripts/tidecloak.sh logs
```

The services bind to localhost: Redacted on port 3001 and TideCloak on 8080 by default. This release does not expose either service to other computers. Use the same app origin consistently; the realm configuration is bound to it. Choose custom local ports before setup so the generated client, issuer and origin settings match. Remote hosting is outside this release.

The TideCloak database is in the `redacted-tidecloak` Docker volume, mounted at `/opt/keycloak/data/h2`. A short-lived init container sets volume ownership; it has no network and exits before TideCloak starts. Both containers use only the pinned `tideorg/tidecloak-dev` image; the init container is not a second server and no production TideCloak image is used. The actual TideCloak server runs as its normal unprivileged user. It has a 2 GB container memory limit; actual use and adequacy must be checked for your workload. TideCloak does not automatically start after a host restart.

Back up **together**:

- `data/documents.sqlite3` and `data/tide/config.json`;
- the stopped `redacted-tidecloak` volume;
- `.tidecloak/bootstrap.env`, stored securely outside a public repository;
- your Tide account recovery material through Tide's supported recovery process.

Stop both services before taking a simple filesystem/volume backup. Keep the model volume separately if you want to avoid downloading it again. Deleting/recreating the realm is not a recovery procedure for existing ciphertext. A restored deployment must retain its issuer, client configuration and user identities.

## Version and verification notes

The integration pins TideCloak image digest `sha256:06ad6dfc58a441c40cda99ce6800511846a27b6cfb47d9c084274470e10548db` (image label 0.14.38) and browser SDK `@tidecloak/js` 0.14.34. These were the supported image and latest stable JavaScript SDK available during implementation; exact interoperability still requires the live account tests above. Do not infer it from matching version numbers in older setup guides.

The SDK includes its own DPoP relay and CSP. Build copies those assets from the pinned package. FastAPI checks EdDSA access tokens against the locally trusted adapter keys, exact issuer/audience/client, expiry and required roles, plus ES256 DPoP proof signature, key binding, HTTP method, URL, access-token hash, nonce and replay protection. A bound-token claim alone is not accepted as proof of possession. No server decryption key is installed.

Automated tests cover token/proof rejection, owner isolation, single-use owner handoff and resumable setup, guest behavior, and the browser storage boundary with an explicitly isolated encryption fixture. Fixture tests do not prove live Tide network behavior, admin ceremonies, self-registration or cross-account cryptographic isolation; those require real accounts.

If realm provisioning, account linking or governance approval is incomplete, leave setup unfinished and keep using guest redaction. There is no plaintext secure-history fallback.

Upstream registration tests explicitly permit default self-encryption roles for
open self-registration: [default-role grants](https://github.com/tide-foundation/tidecloak-iga-extensions/blob/main/iga-core/src/test/java/org/tidecloak/iga/providers/IgaUserProviderRegistrationDefaultRolesTest.java)
and [default-role privilege guard](https://github.com/tide-foundation/tidecloak-iga-extensions/blob/main/iga-core/src/test/java/org/tidecloak/iga/providers/DefaultRoleCompositeGuardTest.java).
This source evidence supports the intended flow but does not replace a live test
of the pinned container and account-linking path.
