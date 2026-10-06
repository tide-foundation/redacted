# Third-party notices

Redacted's original application code is licensed under the [MIT License](LICENSE).
Dependencies, model weights and fonts retain their own licences and copyright
notices. The MIT grant for Redacted does not relicense those components.

## PDF processing: PyMuPDF and MuPDF

The current runtime uses **PyMuPDF 1.28.2**, including **MuPDF 1.28.2**. These
products are offered under the GNU Affero General Public License, version 3
(AGPL), or a separate commercial agreement with Artifex. MuPDF is copyright
2006–2026 Artifex Software, Inc. No commercial licence is supplied by this project.

The runnable application combines Redacted with the AGPL PDF engine; it is not
an MIT-only distribution. Redacted-original code remains separately available
under MIT, while distribution and deployment of the combined application must
satisfy the applicable AGPL terms unless separately covered by a commercial
licence. Those terms include corresponding-source, licence and notice obligations,
including the applicable source-access requirements for network users. Keeping
this notice alone does not satisfy those obligations.

The full text is included in [licenses/AGPL-3.0.txt](licenses/AGPL-3.0.txt), copied
unchanged from [PyMuPDF 1.28.2 COPYING](https://github.com/pymupdf/PyMuPDF/blob/1.28.2/COPYING).
See [Artifex's licensing guidance](https://artifex.com/licensing) and the
[FSF's explanation of combining permissive and copyleft code](https://www.gnu.org/licenses/license-compatibility.html).

Corresponding-source starting points for this dependency version are:

- [Redacted application source and build files](https://github.com/tide-foundation/redacted).
- [PyMuPDF 1.28.2 source](https://github.com/pymupdf/PyMuPDF/tree/1.28.2).
- [MuPDF 1.28.2 source](https://github.com/ArtifexSoftware/mupdf/tree/1.28.2), including its third-party source references and notices.

A distributor of a built image must provide the source corresponding to that
specific image, including local changes and required build material, through an
AGPL-compliant mechanism. These upstream links identify sources; they do not
constitute a complete source offer for an arbitrary future build or modified
deployment.

## Privacy Filter code and model

OpenAI Privacy Filter code and the separately downloaded model weights are
**Apache-2.0**, rather than MIT. The application installs the `opf` package from
commit `f7f00ca7fb869683eb732c010299d901457f19c3` and downloads the model into its
separate persistent model store.

- [Pinned Privacy Filter source](https://github.com/openai/privacy-filter/tree/f7f00ca7fb869683eb732c010299d901457f19c3).
- [Pinned upstream licence](https://github.com/openai/privacy-filter/blob/f7f00ca7fb869683eb732c010299d901457f19c3/LICENSE).
- [Official model and model licence declaration](https://huggingface.co/openai/privacy-filter).
- [Full Apache-2.0 licence included here](licenses/Apache-2.0.txt), copied unchanged from the installed `opf` package.

Retain the relevant upstream licence, attribution and any supplied notices when
redistributing the code or model. The weights are not included in the application
image; any separately redistributed model cache must carry its own applicable
licensing material.

## Browser assets

The compiled browser application includes these components:

| Component | Licence | Included full notice |
| --- | --- | --- |
| React 19.2.4, React DOM 19.2.4 and Scheduler 0.27.0 | MIT; Meta Platforms, Inc. and affiliates | [React-MIT.txt](public/licenses/React-MIT.txt) |
| Lucide React 0.468.0 | ISC, with the upstream Cole Bemis / Feather MIT attribution retained | [Lucide-ISC.txt](public/licenses/Lucide-ISC.txt) |

| docx | MIT | [docx-MIT.txt](public/licenses/docx-MIT.txt) |
| pdf-lib | MIT | [pdf-lib-MIT.txt](public/licenses/pdf-lib-MIT.txt) |
| @pdf-lib/fontkit | MIT | [upstream README and licence declaration](public/licenses/fontkit-README.md) |

These files retain the installed upstream notices. The fontkit package and repository declare MIT in their README and package metadata but do not ship a standalone licence file; its unmodified README is retained here.
Vite includes them in the static build, where they are available at
`/licenses/React-MIT.txt` and `/licenses/Lucide-ISC.txt`. React, React DOM and
Scheduler supply identical licence texts in these versions. Upstream sources:
[React](https://github.com/facebook/react) and
[Lucide 0.468.0](https://github.com/lucide-icons/lucide/tree/0.468.0).

The locally served fonts have separate **SIL Open Font License 1.1** grants:

- **Inter** — copyright 2016 The Inter Project Authors. Full notice:
  [Inter-OFL.txt](public/fonts/Inter-OFL.txt); [upstream project](https://github.com/rsms/inter).
- **Cousine** — copyright 2026 The Cousine Project Authors. Full notice:
  [Cousine-OFL.txt](public/fonts/Cousine-OFL.txt); [upstream font distribution](https://github.com/google/fonts/tree/main/ofl/cousine).

Keep those font licences with distributed font files. Font software is not
relicensed under Redacted's MIT licence. Brand-asset provenance is documented
separately in [docs/branding.md](docs/branding.md).

## Other runtime and build components

The primary application libraries include FastAPI and python-docx under MIT,
Uvicorn and Starlette under BSD-3-Clause, and the Privacy Filter runtime's PyTorch,
NumPy, Hugging Face libraries, safetensors and tiktoken under their respective
upstream licences. Exact versions are recorded in
[backend/requirements.lock.txt](backend/requirements.lock.txt) and
[package-lock.json](package-lock.json).

This is a practical notice index, not a replacement for every bundled component's
licence. In particular, certifi includes MPL-2.0 material; tqdm includes MPL-2.0
and MIT material; and binary packages such as PyTorch, NumPy and lxml carry
additional third-party notices. Their installed licence files, package metadata
and source notices remain in the copied Python environment (`/opt/venv` in the
container, generally under `*.dist-info/licenses/`). Preserve these materials
when repackaging. System-package notices are also retained in the base image,
including under `/usr/share/doc`.

Build and test tools retain their own licences even though Node and those tools
are not production servers. Examples include Vite under MIT, and TypeScript and
Playwright under Apache-2.0. Nothing in this file grants rights beyond the
applicable component licences.

## Optional Tide integration

The browser integration uses `@tidecloak/js` 0.14.34 and its locked Tide
cryptographic dependencies. They retain their **Tide Community Open Code
License**, which is not MIT. The package's unmodified full notice is served at
[public/licenses/Tide-Community-Open-Code.txt](public/licenses/Tide-Community-Open-Code.txt).
The SDK relay page and its CSP are copied together from the installed SDK during
build; they are Tide-supplied assets. Dependency sources and versions are recorded
in `package-lock.json` and the [setup guide](docs/tidecloak-setup.md).

TideCloak runs in its separately distributed upstream container, pinned by digest
in `compose.yaml`; its licences and notices remain in that image. Adding the
integration does not relicense Tide software under Redacted's MIT grant. Review
all applicable component licences before redistributing a combined deployment.
