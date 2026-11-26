# R3 shared boundary experiment
Owner-approved parallel implementation per #79. Non-shipping experiment only.
Context, exact request, identity-bound grant, common existing PolicyClock and
profiles.decide, revocation, four future mode structural checks and fail-closed
classification. No real permission minted; no callback/transport/executor.
PolicyClock fixture time is not a real persisted live profile binding. No
actual provider/inference, credentials, host shell, OS isolation or UI wiring.
Before activation: trusted context generation, saved mode migration, live
policy binding, broker effect initiation/revoke serialization, separate
minimum disclosure consent, OS containment, independent review and owner gates.

## CAP-07 typed application capabilities (fixture only)

Closed catalog for OCR, PDF extract/render/export, public search, Firefox/Brave read, application window inspection and FFmpeg conversion. Each call binds adapter, version, operation, exact ordered typed arguments and input digest into the existing gateway Request target. Separate policy ceilings for host, file read/write and browser/network. No app, command, browser, parser, file, search provider or worker is invoked; the sole result is a synthetic receipt. No actual URL/IP/DNS validation claim; origin is a fixture token only. Submit/upload/download, UI input and arbitrary functions deliberately refused pending separate runtime security contracts. Seven regression methods; source shipping unchanged. Roadmap #286, activation HOLD.

## CAP-01/02 document evidence contract (fixture only)

Typed synthetic OCR and PDF text output: exact call target, source digest, requested page, immutable normalized integer boxes, OCR confidence 0–100 only, UTF-8/control/size and count limits. The output digest includes geometry and confidence; one completion, live gateway checks before accepting and publishing, cancel/revoke drops results. No real document parser, file access, OCR binary or export effect. No claim of recognizing actual documents. Five tests, existing synthetic suite remains intact.

## CAP-01/02 real engine smoke evidence (CI only)

Separate disposable runner, fixed self-generated public documents, no user paths, no desktop entrypoint or runtime grant. Actual Tesseract TR/EN/DE PNG OCR; ReportLab text-layer PDF with Poppler per-page extraction; scan-only PDF render and OCR fallback; corrupt/encrypted refusal. Ten fixed cases, exact normalized text criterion, source digests and version evidence. Apt packages installed only in CI. This is not adversarial parser isolation, real user-document acceptance, production adapter integration or authorization for activation. No output document shipped.

## CAP-04 exact browser operation binding (fixture only)

Firefox and Brave navigate/snapshot operations bind task-owned profile, tab, canonical restricted HTTPS URL, and snapshot content digest. Distinct grants; no navigation grant reuse for snapshot or cross-browser/profile/page. Reject ambiguous authority, private literal/alternate address forms, paths with percent escapes/traversal, queries/fragments, noncanonical typed inputs. DNS, actual profile ownership, peer enforcement and transport remain unimplemented. No real browser or UI control. Three new tests. Legacy origin-only receipts are fixture-only and never authorize real I/O.

## CAP-03 search result contract (fixture only)

Exact public query UTF-8 bytes and provider version bound to the shared one-use grant. Bounded query/hit counts/snippets, strict restricted URL and display text, duplicate refusal, separate empty outcome, no navigation rights from result metadata, live cancellation/revocation. Search snippets remain untrusted and are not verified page citations. Five tests; no network/provider/API credentials or runtime activation. Classification is trusted-fixture-only, not a private-query disclosure detector.

## CAP-05/06 engine inventory readiness (fixture only)

Separate detected, compatible, tested evidence per exact adapter/version/function. Unknown and contradictory evidence refused or explicitly unavailable; a tested observation never means authorized or available. No scanner, subprocess, desktop window lookup or grant creation. Four tests. Real engine discovery, signed/verified executable identity, live permission, installation ownership and OS isolation remain open. Settings must render these states separately.

## Actual engine output mapped to shared fixture result contract

#291 ten raw engine smoke cases passed. Add six separate actual TSV/XHTML bbox cases: three OCR languages with real per-word confidence and geometry; three per-page Poppler word geometry results. Map into existing DocumentFixture with exact engine version, source SHA-256, page and immutable block output digest; compare normalized recognized text exactly and source bytes unchanged. The real document workflow now verifies unchanged shipped paths against frozen experimental base. 16 cases total; no private document or actual runtime authorization. This is not a sandbox/host adapter or acceptance of arbitrary PDF/TSV/XHTML input.

PDF page OCR has a distinct typed call: original PDF artifact/digest, selected page, rendered image digest and OCR language. The result must preserve both digests and page; OCR confidence applies, ordinary PDF text extraction cannot claim raster provenance. Synthetic only; a live trusted renderer must establish the derivation before any real grant.

Mixed three-page actual fixture (German text, Turkish scanned image, blank page): verify page count, per-page extraction, exact scan page render and OCR through original PDF + raster digest contract. Five additional cases, 21 total, prior 16 retained. Blank extraction is not by itself a determination that a page is blank or a request to OCR; this test knows the fixture contents. Workflow triggers on geometry/result/capability dependencies. No runtime document handling.

CAP-04 synthetic owned browser lifecycle: exact ordered page plan, task-owned profile/tab identifiers, separate navigation and content-bound snapshot grants, prior navigation revocation blocks publication, new navigation clears old result, close revokes claimed grants, automatic traffic always denied. No real browser profile ownership, OS/network isolation or actual execution. Four regressions.

CAP-08 local document-summary chain fixture: completed live document result derives a distinct Jan-local summary request bound to artifact, source digest, selected page, output digest, version and requested language. Separate one-use owner grant, source revocation blocks next initiation/publication. No model inference, text disclosure, cloud variant, or actual summary; synthetic receipt only. Two tests.

CAP-06 real application engine smoke evidence: fixed public synthetic PCM lossless WAV/FLAC conversion and roundtrip with FFmpeg, text-to-PDF headless LibreOffice export with disposable profile and exact Poppler extracted text. Four cases, source hashes unchanged, actual versions recorded, CI-only installation. No actual production adapters, arbitrary commands, owner files or desktop UI; no adversarial isolation claim.

CAP-06 conversion output fixture: exact FFmpeg WAV→FLAC and LibreOffice text→PDF typed operations, source/output handle and digest, bounded immutable output bytes and narrow magic signature, separate one-use grant, cancel/revoke checks. Five tests. No actual file publication or production validation; signature alone does not establish valid media/PDF content.

CAP-06 actual conversion output mapped to shared fixture: retain original four real cases and add two exact source/output receipt checks using actual FFmpeg and LibreOffice versions. Six cases total. These grants validate post-conversion fixture receipts, not authorize the actual subprocess; production pre-effect binding and isolation remain open.

CAP-06 isolation feasibility: disposable Ubuntu runner only, actual unprivileged bubblewrap, fixed system /usr read-only and synthetic input bind, private temporary root, separate writable output, separate network namespace and inaccessible host-loopback listener, fixed FFmpeg conversion. Five cases if namespaces available; HOLD/nonzero if runner disallows unprivileged namespaces, never disable AppArmor/sysctl or use privileged fallback. Not adversarial sandbox proof, owner hardware acceptance or production adapter.

CAP-02 actual PDF split/reorder/merge fixture: four extra cases with Poppler pdfseparate/pdfunite on generated 3-page multilingual PDF. Exact output set, per-page text and count, explicit reordered merge, all input hashes unchanged. 25 total document cases. Fixed CI operations only; no arbitrary PDF or runtime export authorization.

CAP-06/08 separate synthetic conversion publication: source/output handles, source and result SHA256, exact destination and store version bound to distinct publish grant; no conversion grant reuse, reject logical source/staging aliases, revoke source before publishing, one simulated publication. Three tests. No real filesystem publication, path/alias resolution, or authorization of runtime effects. Isolation remains HOLD until an actual supported sandbox passes.

Conversion publication has its own shared gateway action with all three exact-target ceilings: artifact publication, filesystem read and filesystem write. A grant for staging conversion cannot satisfy the publication target; explicit write denial wins. Two regressions. Still fixture-only; actual OS path and sandbox acceptance required.

CAP-06 alternate stock Ubuntu 22.04 hosted runner: rerun identical five bubblewrap probes with unprivileged UID assertion and kernel/version evidence. Preserve #304 Ubuntu 24.04 failure; success here is runner-specific, not Pop!_OS acceptance. No privileged sandbox fallback or security settings changed.

CAP-06/08 simulated publication claim: validate child and already-claimed exact parent in a single gateway lock and policy-clock observation; failed pair does not consume the child, replay refused. Four regressions. Dependency relation supplied by trusted fixture caller, no real I/O or atomic OS effect; actual broker must establish trusted relationship and effect linearization separately.

CAP-06 stock Ubuntu 22.04 #308: four isolation probes PASS; actual FFmpeg start FAIL due to libblas.so.3 unavailable inside minimal mount. Successor exposes no new host mounts; set fixed architecture-specific BLAS/LAPACK search paths already within read-only /usr and add actual isolated lossless PCM roundtrip (six total cases). No /etc bind, host environment inheritance or privileged fallback. Keep #304/#308 as evidence and retain owner runtime HOLD.

CAP-06 fixed real application engine fixture now claims the exact typed conversion receipt before launching fixed commands, verifies expected source digest and no existing destination, rechecks grant and source/output after completion, bounds output. Two additional negative preflight cases (missing write ceiling and wrong source digest) assert no conversion output and source unchanged. Eight real-engine fixture cases. No user paths, arbitrary command, real permission, TOCTOU protection or production isolation; this is separate from bubblewrap evidence.

CAP-06 #310 stock Ubuntu 22.04 six actual isolated checks PASS. Successor adds real effective/permitted/ambient capability checks and synthetic host environment marker denial, then claims exact typed FFmpeg receipt before fixed isolated conversion and validates bounded output after it. Nine actual CI cases. Fixture policy is still not a real permission; no adversarial concurrent path binding, host install or owner hardware acceptance.

CAP-06/08 actual no-clobber filesystem primitive evidence in private temporary synthetic directory: exclusive stage, pinned directory FD, atomic non-replacing hard link, existing regular/symlink/stage preservation, exact result bytes and replay/revoke refusal (five additional test methods). No arbitrary path or owner data. Synthetic publication grant is not real OS authorization; crash recovery, filesystem compatibility, real directory identity and atomic revoke/effect boundary remain open.

CAP-04/08 extend shared dependent claim/checkpoint to all exact previously claimed grants in a bounded tuple, one lock/time observation. Browser navigation/snapshot chains and document summary use this shared contract. Four new regressions. No actual browser, inference, network or real OS atomic effect.

CAP-06/08 private synthetic publication directory scope now binds actual device/inode plus fixed destination name to the separate fixture grant. Open no-follow and verify mode/UID, verify bounded actual source digest before claim, fsync after stage cleanup. Five new regression methods: replacement directory, changed source, directory symlink, nonprivate mode, source symlink. No arbitrary/owner paths; not proof against same-UID races, ancestor replacement, inode reuse or crash recovery.

CAP-06 bounded isolated process experiment: fixed Python exec wrapper sets core/file/CPU/FD rlimits, bounded file-backed capture, timeout terminates owned process group with existing bubblewrap die-with-parent. Four additional actual cases (limits visible, oversized file denied, excessive capture rejected, late marker absent after timeout). 13 cases total. No cgroup total disk/memory quota or general descendant cleanup guarantee; fixed synthetic CI only.

CAP-06 stricter sandbox mount evidence: read-only root remount while explicit output and private scratch remain writable, absolute bwrap executable and allowlisted launcher environment, verify denied root/work writes, private scratch and separate PID/IPC namespaces. Three additional cases (16 total). Hosted Ubuntu 22.04 only; no target-machine or adversarial acceptance.
