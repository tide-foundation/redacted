# ADR 001: retain separately protected source, manifest and derived outputs

Date: 1 October 2026. Decision: **Approach B**. Tide-specific integration is deferred until Raziel MCP supplies authoritative guidance.

## Context

Guests need one temporary working result. Optional authenticated history needs protected durable retention, owner-scoped access, detection review and recovery. The backend cannot independently decrypt Tide-protected data. It does see plaintext during active parsing/redaction. Preserve one FastAPI runtime, existing native PDF/DOCX export, the separate model volume and SQLite under `data/`.

## Alternatives

| Criterion | A: protected source + manifest | B: source + manifest + protected cached outputs | C: packaged protected history object |
| --- | --- | --- | --- |
| Storage | Lowest; no cached outputs | Higher; retains current PDF/DOCX/TXT outputs | Similar to B plus packaging overhead |
| Reprocessing and CPU/model cost | Every output needs rendering; stored edits can avoid inference | Downloads need no renderer or model; rerun only for changed settings | Can cache outputs, but package access may be coarse |
| Download latency | Browser decrypt, transient upload, render, return | Retrieve one artifact and decrypt in browser | May require downloading/decrypting the whole package |
| Fidelity | Renderer changes can alter historical output | Preserves exact originally generated output | Preserves output if included in package |
| Regeneration without inference | Source + exact manifest edits are sufficient in principle | Same option plus immediately usable cached output | Possible after unpacking the required artifacts |
| Transient plaintext exposure | More server rendering/handoffs for routine downloads | Routine output/original recovery can remain browser-only | Browser can unpack locally; larger decrypted object lifetime |
| Complexity | Fewer blobs; more processing orchestration | Simple typed artifacts and atomic metadata/artifact transaction | Versioned packaging/unpacking and whole-object updates |
| Failure/recovery | Rendering failure prevents a download | Cached results survive model/renderer changes; publish only complete records | Corruption or failed updates can affect a whole package |
| Future delegated server capability | Can attach to the source interface later | Same; no current dependency on delegation | Requires package awareness or an extraction layer |
| Migration | Smaller schema, but later caching adds a new lifecycle | Explicit versions and artifact kinds support incremental changes | Package-version migration can require full rewrite |
| Hosted behavior | Lower storage, greater compute/latency and request exposure | More storage/bandwidth, predictable downloads and selective retrieval | Large transfers/updates complicate quotas and recovery |

## Decision and boundaries

Retain the original, structured manifest and generated PDF/DOCX/TXT as **separate protected artifacts**. Encrypt outputs as well: body text, images and missed detections can remain confidential. Cached artifacts are useful and preserve fidelity; deleting them merely to minimize storage would increase cost and exposure.

Use owner-required SQLite metadata and opaque binary payload storage, with draft/upload/commit semantics. SQLite BLOBs keep bounded document artifacts and metadata transactional without a second filesystem commit protocol. This is a local-app starting point; the repository interface allows a later object store. Limit artifact/record sizes, expire incomplete drafts, and expose only complete history records. SQL operations must never interpret a protected artifact's plaintext.

Metadata is limited, not information-free: category counts, types, times, mode, sensitivity and layout state reveal some information. Do not store original filenames, originals, original-to-replacement mappings, exact spans, arbitrary model warning text or user descriptions in searchable metadata. Protect the filename separately; keep original values and exact spans in the protected manifest. Generated replacement values may also be stored as a validated, unencrypted review projection without their originals. Use constrained warning codes in durable metadata.

Guest state is separate and temporary: an opaque browser-session cookie scopes one in-memory working record; generated files use `data/guest/` with a fixed one-hour lifetime. Polling does not extend that lifetime. Replace/delete/expiry/restart remove the artifacts; late-running jobs must not republish expired records. Compose mounts this directory as bounded tmpfs. Direct Python uses the same directory with explicit filesystem cleanup. The raw upload is not saved as a source file. Full manifests remain in bounded RAM; guest review defaults to a concealed projection without originals or length-revealing offsets. An explicit same-session Reveal request can return the document’s detected original values from RAM. Filenames also remain only in guest RAM; durable filenames have their own protected artifact.

Before actual Tide integration, durable APIs fail closed, no production account is fabricated and no plaintext payload is labelled encrypted. Unit fixtures test opaque byte handling and owner checks; they are not encryption tests.

## Eventual protected workflow

An authenticated browser obtains transient results, self-encrypts filename, source, manifest and outputs, uploads opaque artifacts, then commits the complete owned record. It clears temporary working data after successful retention. Reveal/recovery fetch ciphertext and decrypt in browser memory. Original recovery decrypts the source in the browser; there is no Reprocess action, backend decryption key or durable plaintext source. Exact SDK, token, permission and envelope conventions are deliberately left to Raziel.

This provides protected durable storage, **not a zero-knowledge processing server**. Memory release and file deletion do not promise forensic erasure; browser downloads, swap, host backups and model detection misses remain relevant limitations. No Forseti or delegated decryption is part of this decision.

## Migration

Legacy unowned records and UUID output folders are disposable by user instruction. Delete them during the documented migration; do not auto-assign them or retain an unencrypted backup. The model volume is unaffected. Guest sessions/results intentionally do not survive application restart. Durable schema versions must distinguish later migrations from this one-time legacy removal.
