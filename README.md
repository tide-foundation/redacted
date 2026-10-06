# Redacted

<p align="center">
  <img src="public/brand/redacted-logo.svg" alt="Redacted" width="420">
</p>

<p align="center">A Tide community project.</p>

Redacted helps you remove sensitive text from PDF and Word documents on your own machine. Drop in a file, choose how to handle detected values, and download the result. Detection runs locally with [OpenAI Privacy Filter](https://github.com/openai/privacy-filter)—no paid API or account required.

## What it does

| Mode | Result |
| --- | --- |
| **Mask** | Fixed masks: `******`, or `**/**/**` for dates, regardless of the original character count. |
| **Label** | Descriptive placeholders such as `[Name]`, `[Date]` and `[Phone Number]`. |
| **Replace** | Fictional values, consistent for repeated detections within a document. |

The model detects names, addresses, emails, phone numbers, dates, URLs, account numbers and secrets. Adjust sensitivity, review detections, reveal or hide original values, and inspect the scan report before downloading PDF, DOCX or TXT.

Matching-format downloads preserve the original layout where possible. If an in-place edit cannot be completed, the app falls back to a clean text rewrite and tells you. Your source file is never overwritten.

**Review every output before sharing it.** Detection can miss sensitive information; images are not scanned and OCR is not included. Sensitivity is not an accuracy score. See the [full disclaimer](DISCLAIMER.md) and [document limits](docs/development.md#document-fidelity-and-limits).

## Run locally

**Upgrading an earlier installation?** Export anything you need first. This version removes the old global, unowned file history and its outputs at startup. Current guest results also disappear on restart. See [updates and storage](docs/development.md#updates-and-existing-installations).

Install Git and Docker with Compose 2.24 or newer, then (no host Python or Node installation required):

```bash
git clone https://github.com/tide-foundation/redacted.git
cd redacted
mkdir -p data/guest
docker compose up --build --wait
```

Open **http://localhost:3001**. Compose downloads and validates the model and tokenizer in the `model-init` setup service before starting the app. The command waits for the app to be healthy; follow download progress in another terminal with `docker compose logs -f model-init`. Later starts reuse the cached files. Setup needs internet access; document processing uses local assets.

Use `docker compose stop` to stop the app. After updating your checkout, use `docker compose up --build --wait` again; keep the `redacted-model` volume to avoid downloading the model again. Linux users whose UID/GID differs from 1000 should follow the [ownership settings](docs/development.md#docker-settings) before the first build.

### Disk and memory

| Component | Approximate size |
| --- | --- |
| Required image + model/tokenizer at initial setup | **4.5 GB** |
| Current local Docker runtime image | 1.7 GB |
| Cached model and tokenizer | 2.8 GB |
| Additional disk | Checkout, build caches, older images and any downloaded results |
| Runtime memory | Several GB for inference, plus document processing and other applications |

These are observations of the current CPU build, not validated minimum requirements. The image and model alone total about 4.5 GB; allow additional disk for building. Memory use depends on the document. The model loads on the first submission. The Docker CPU build retains file-backed weights so the OS can reclaim their pages under memory pressure, and uses small internal expert batches to limit temporary allocations.

### Mac support

The Docker setup is intended for both Intel and Apple Silicon Macs using [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/). The pinned TideCloak dev image publishes both `linux/amd64` and `linux/arm64`; Redacted builds for the host architecture. Use the same commands in Terminal. Processing uses CPU inside Docker, without Apple GPU acceleration. Allow several GB of Docker memory for the model plus 2 GB for optional TideCloak. Mac end-to-end operation has not yet been tested on hardware.

## Temporary by design

Guest mode keeps **one current document per browser session**. Uploading another file replaces the finished result. Results expire one hour after upload, and deletion or a server restart clears them too. Download anything you want to keep.

Guest filenames and detected original values stay in temporary server memory. Generated guest files use temporary memory-backed storage in Docker. The local server can read document contents while processing; concealed values in the interface are not encryption. See [where data lives](docs/development.md#storage-and-privacy-boundaries).

**Optional encrypted history:** the TideCloak integration is ready for owner setup and live-account testing. It runs as a separate, optional local Docker service and uses Tide’s network for authentication and encryption. Run `bash scripts/tidecloak.sh start` once after the app is running. It opens an authorized setup wizard inside Redacted; no setup-code entry or routine admin-console visits. The [setup guide](docs/tidecloak-setup.md) also covers using an existing local TideCloak server. Guest mode works without it. Live sign-in, account linking and unattended self-registration must be verified on your installation before relying on saved history.

## Join in

Try Redacted with invented sample documents, report a reproducible problem, improve the wording or accessibility, or help with document handling and tests. You do not need to download the model to work on the interface.

Start with [CONTRIBUTING.md](CONTRIBUTING.md). For setup without Docker, test commands and implementation details, see the [development guide](docs/development.md).

## Licence

Redacted-original application code is available under the [MIT licence](LICENSE). **The complete runtime is not MIT-only:** it includes PyMuPDF/MuPDF under AGPL terms and Tide SDK components under their Tide Community Open Code licence. Redistribution or hosted deployments must account for those terms; the MIT licence does not replace them. See [third-party notices](THIRD_PARTY_NOTICES.md) for dependency and model licences, and [branding and fonts](docs/branding.md) for font licences and Tide marks.
