# Optional TideCloak integration brief

Redacted remains a local, unauthenticated document redactor by default. The owner can enable an optional, self-hosted TideCloak service for personal encrypted history. App, identity service and documents stay on the owner's machine; access to Tide's network is expected.

## Installation and setup

- Package TideCloak as a separate optional Docker Compose service. Download it during the optional preparation step, but do not start it without an owner action. Give it a persistent database volume.
- Keep guest redaction available when TideCloak is disabled, stopped or unavailable.
- After one terminal command, guide the owner entirely inside Redacted. Use TideCloak APIs for realm creation, licensing, configuration and approvals, with the Tide enclave for identity and signatures. Persist a checklist and resume after interruption.
- Establish owner authority through an automatically opened, short-lived, single-use setup link; no manual code or browser owner-password form. Keep bootstrap credentials in backend RAM only during the authorized session. Never expose a Docker socket or persist these credentials in the app.
- Configure the app client, exact redirect origins and personal-history roles; verify effective settings rather than treating accepted API changes as committed.
- Approve creation of `_tide_history.selfencrypt` and `_tide_history.selfdecrypt`, and their inclusion in default roles, during setup after IGA/QEA is established. New users should inherit these permissions.
- Support owner-enabled self-registration. Acceptance requires a second user to register, encrypt and decrypt without further admin role signatures. Separately verify whether account creation/linking generates pending governance requests on the selected TideCloak version; do not conceal that requirement or bypass governance.
- Keep administrator privileges separate from ordinary personal-history permissions.

## Branding (owner supplied)

- Secure enclave realm background: `public/brand/redacted-wallpaper.jpg`.
- Secure enclave logo: `public/brand/redacted-logo_stacked.jpg`.
- Redacted app favicon: `public/brand/redacted-icon.png`.
- Preserve the existing black-and-white Redacted app design and the supplied main app logo.
- Upload the JPEG assets through TideCloak's supported branding endpoints and apply/sign IdP configuration when required. Recording paths alone does not constitute completed enclave branding.

## Authentication and history

- Use the supported browser SDK with trusted runtime adapter configuration; verify tokens on FastAPI, including signature, issuer, audience and expiry. Never trust frontend owner IDs or unverified token claims.
- Resolve the supported proof-of-possession protocol before claiming or enabling DPoP enforcement.
- Encrypt source, filename, detection manifest and PDF/DOCX/TXT outputs in the browser before durable storage. Keep durable records owner-scoped. Decrypt only in the user's browser, per requested action. Do not decrypt detections before an explicit Reveal. Cache decrypted artifacts only in session memory and show skeletons in the positions of pending filenames and values.
- No server-held decryption keys, delegated decryption or shared-document policies in this release.
- Explicitly handle login transitions: never silently persist guest plaintext to browser storage. If full-page authentication discards a guest result/source, tell the user before redirecting and have them upload after login.
- Hide revealed values and clear transient plaintext on logout, identity changes and page lifecycle transitions.
- Never fall back to durable plaintext if Tide fails. Retain existing ciphertext and show an actionable unavailable state.

## Verification and delivery

- Verify guest mode with TideCloak stopped; preserve the existing demo throughout implementation where possible.
- Validate bootstrap authorization, token rejection, owner isolation, ciphertext-only persistence, logout cleanup and interrupted saves.
- Test actual Tide account linking and signing with the owner at the required browser ceremony. Do not claim those actions passed using mocks.
- Test a second non-admin account with no additional role signatures, including inability to access the first user's documents.
- Verify supplied branding in the real enclave and the favicon in the app.
- Document start/stop, backup/restore (history database plus TideCloak state/configuration), network dependency and known limitations.

## Earlier implementation checkpoint

This checkpoint describes the original console-assisted test; see the setup guide
for the new embedded flow. A fresh live account ceremony remains necessary to
validate the replacement wizard.

The optional Compose service and owner setup flow are implemented, together with
the browser encryption provider, EdDSA/DPoP API verification, automatic protected
saves, original recovery and requested branding configuration.

The local test realm has been licensed and linked to its Tide administrator.
The owner approved the history roles, default grants, client, audience mapper,
role scopes and sign-up changes. Setup applied and read back the supplied JPEG
logo and wallpaper, verified their served bytes, signed the realm settings and
installed the public adapter. The app reports secure history ready and retains
that configuration across container restarts. A fresh browser enables sign-in
and redirects through TideCloak to the configured Tide enclave. The real login
form, supplied wallpaper and stacked logo were visually verified. Granting
local-network access to the configured Tide origin in the isolated test browser
allowed both images and the bound DPoP relay to load with HTTP 200 and no failed
requests. This permission was not changed in the owner's browser.

Setup comparisons tolerate URL ordering and TideCloak-added mapper defaults.
Plain-text branding/signing acknowledgements are followed by JSON readback.
An empty approval queue with unapplied settings is reported as an error, not
another approval request. Thirty-five focused backend checks cover these cases
and authentication; setup and guest/history browser checks also pass.

Completing app sign-in with a real user, Tide encryption round trips and a
second user's self-registration remain acceptance gates.
Token/proof rejection, owner isolation, browser lifecycle and encrypted-artifact
storage boundaries have automated coverage, which does not replace those live
account tests.

Runtime verification: the rebuilt app is healthy on localhost:3001. Earlier
real local-model smoke tests passed for DOCX Mask, PDF Mask, DOCX Label and PDF
Replace, with all three export formats checked and test documents deleted.
Availability described here was observed during that test, not a current runtime status.
