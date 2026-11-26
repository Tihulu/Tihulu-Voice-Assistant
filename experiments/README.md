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

CAP-08 local document-to-Jan fixture bridge reuses LocalAdapter streaming state machine, binds exact completed source result digest, bounded canonical private text, model identity/version/language and separate private host+read grant. Checks parent and child together on each event/result, no cloud fallback; four tests. No real Jan API, inference, credentials, network or production activation.

CAP-06 #316/#317 actual file-limit probe failed while process limits and other checks passed. Successor explicitly writes exact limit using unbuffered writes, requires EFBIG on next byte and exact on-disk size. Original failures preserved. Experimental workflow now verifies exact PR head rather than merge ref. No limit or security boundary relaxed.

CAP-04/06/08 persist trusted fixture dependency edges at claim and revalidate transitive ancestors in every later checkpoint/claim (including standalone checkpoint). Bounded visited traversal, no new callback or transport. Four regression methods cover direct parent revoke, ancestor expiry, child initiation denial and shared ancestor/unrelated grant. Real effect serialization remains open.

CAP-06: exact ordered 13-case sandbox preflight required before any real fixture conversion/version probe. A failed/held/missing/reordered preflight stops engine execution; failed lossless roundtrip prevents successful typed receipt. Four preflight regressions. Existing 16 real cases preserved. Disposable CI only, no owner activation.

CAP-01/02/06: actual Poppler per-page bbox and Tesseract TSV OCR inside existing fail-closed Bubblewrap boundary, fixed TR/EN/DE generated public PDF/images, typed preclaim and receipt, source digest preservation. Seven additional cases (23 total planned), CI-only packages; no personal documents or runtime permission. Preserve prior 16 cases.

CAP-08: strict normalized offline model response frame binds exact grant invocation, target, model-call identity and sequence; closed JSON fields, duplicate/malformed/oversize rejection, immutable public call property, stale/revoked frame denial. Five regressions. Not actual Jan/OpenAI SSE or model inference, no network/transport.

CAP-06/08 private fixture publication phase evidence: recheck exact claimed publication and source immediately before link; explicit uncertain outcome after successful link but fsync/cleanup failure, never retry automatically or delete final name. Four injected real-filesystem phase tests. Not proof of atomic revoke/effect, malicious same-UID mutation, or power-loss recovery.

CAP-06 actual isolated LibreOffice text-to-PDF with disposable profile and explicit fontconfig file, no host /etc or home mount. Validate output and extract exact text inside sandbox before typed receipt, source unchanged. Three additional cases (26 planned), no owner files or runtime activation.

CAP-02 actual isolated mixed PDF text/blank/scan fallback and selected fixed split/reorder, original and split file digests unchanged. Six additional CI cases (32 planned with Office). OCR receipt binds exact original PDF/page/render digest; fixed PDF split/merge still lacks runtime export grant. No owner files or activation.

CAP-02: exact bounded ordered PDF split/merge operation, part digest and source binding; merge requires already completed live split parent and separate grant. Five regressions and one actual isolated receipt scenario (33 planned total). Fixture only; signature alone is not format validation, actual page checks are separate; no real export permission.

CAP-06 #325/#326 Office startup HOLD (NoSuchElementException); other isolated engines and PDF split/reorder passed. Successor copies only regular root-owned dpkg-listed /etc/libreoffice package files into a disposable directory, mounts that copy read-only only for Office, sets headless svp and separate profile for version probe. No live /etc mount, user config, or security relaxation. Keep historical failures; verify actual export.

CAP-02 phase gate refinement: document result only completed after exact fixture text, mixed fallback stops on unexpected text/page count or scan/source failure, split receipt only after exact page contents, merge receipt only after exact order and source preservation. Preserve 33 case plan, no new test count claimed. No runtime export.

CAP-06 #328 Office still HOLD after three package-owned configuration files, while 30 other cases passed. Next narrow bootstrap experiment supplies generated numeric fixture passwd/group/NSS and root-owned direct .xcd registry files from stock disposable CI package subtree. No host identity file or broad /etc mount, no security relaxation. Preserve failed evidence. Actual Office export remains HOLD until CI passes.

CAP-06 #330 registry bootstrap stopped on its config bound, not a successful Office launch. Exact seven generated package registry paths from installation log, including res/fcfg_langpack_en-US.xcd; separately bounded stock config (32 MiB/file, 64 MiB aggregate), regular root-owned files with no redirected paths, no changes to 1 MiB artifact/process limits. Hosted synthetic runner only. Preserve earlier failures.

CAP-08: strict bounded synthetic OpenAI-style SSE subset, fragmented UTF-8, exact model and response id, one text choice, stop plus DONE plus EOF before exposing result; reject tool calls, duplicates, truncation, late frames, cancellation and oversized input. Six regression methods. No actual Jan endpoint, network, credential or model invocation; not full wire compatibility.

CAP-08 actual HTTP request/response bytes over private AF_UNIX socketpair in a synthetic CI test: exact input before one-shot initiation, fixed POST and model/body, bounded content-length SSE, reject redirects (no connect method), truncated/oversized body, stale response and revoke. Seven methods. No TCP/DNS/proxy/real Jan or credentials, trusted synthetic peer; not production deadlines or full HTTP/SSE implementation.

CAP-04 first actual Firefox WebDriver fixture: disposable Ubuntu22.04 runner-provided Firefox/geckodriver, private profile and isolated network namespace, fixed local HTML, DOM text and fixed local click, session close and unchanged source. Eight planned checks, no user profile, arbitrary URL, external network, script input or sandbox disable. CI tool availability/compatibility not guaranteed. Not actual runtime browsing permission.

CAP-08 strict fixture HTTP EOF/length and SSE event framing: fixed response headers, absolute receive deadline, bounded whole stream, no duplicate length/chunking/trailing body, one data line plus required blank separator, validate UTF-8 and expected response id before opening socket. Four new regressions. Synthetic closed subset; no real backend or production transport claim.

CAP-04 #334 actual Firefox smoke: namespace/private home/source unchanged PASS; session failed with browser exit127. Successor adds exact in-sandbox version probe and bounded geckodriver diagnostics, fixed read-only /usr loader paths, private XDG runtime dir. No security relaxation or owner paths. Preserve #334 failure.

CAP-06 after successful #331 Office startup/export, recheck the changed Office-specific mount/environment before launch: generated config/identity and host marker/shadow scope, read-only config and input, caps plus PID/IPC/network namespaces. Three actual cases (36 planned), refuse Office if any fail. Existing 1 MiB output limit remains. No runtime activation.

CAP-04 #336 loader diagnostics identify stock firefox.sh failing because which is unavailable inside minimal namespace. Install stock debianutils only in disposable CI and explicitly check which; retain no host alternatives mount or sandbox disable. Browser actual session still requires CI evidence.

CAP-04 alternative package-wrapper remediation: verify fixed stock /usr/lib/firefox/firefox ELF, root ownership and exact resolved wrapper/binary identity; execute actual package binary inside existing /usr read-only mount, no which or /etc/alternatives dependency, no sandbox disable. Hosted Ubuntu22.04 only, actual browser acceptance pending.

CAP-04 #339 real Firefox 160.0 private session, local navigation/DOM/click and closure 8/8 PASS. Successor adds actual fixed host-loopback listener denial (socket probe and real WebDriver navigation), no connection accepted by host listener, and invalid-session rejection after close. Four additional actual cases, 12 planned. No public web or owner profile; no security relaxation.

CAP-04 #340 Firefox 160.0.1 version succeeded, 5/12 checks PASS, WebDriver session startup timed out. Successor separates bounded startup (30s) from ordinary commands (8s), fixed page load/script limits, phase-specific HOLD diagnostics, 115s outer timeout. No retries or security relaxation. Prior #339 8/8 preserved; expanded package requires new evidence.

CAP-08: local synthetic socketpair transport checks the exact live invocation before send and before/after each bounded receive, cancels on socket allocation failure. Three regressions for setup failure and revoke at send/receive. Not atomic OS send/revoke serialization or actual Jan backend.

CAP-04 offline browser fixture capability: separate private local-only open/read/fixed click scope with browser+read+host ceilings but no external network permission. Exact profile/tab/artifact/digest/element, distinct claimed grants and live chain, close revokes. Six regressions. No actual I/O, arbitrary file URLs or public browsing; real WebDriver binding remains separate.

CAP-04 #341 startup correction succeeded, 11/12 cases PASS; host listener untouched and closed session denied, browser navigation error classifier failed. Successor records structured WebDriver error status/type/message and accepts only explicit network refusal/timeout or Gecko about:neterror exact target and closed code. No generic error or successful navigation accepted. Preserve #341 evidence.

CAP-04 connect actual fixed Firefox fixture operations to offline typed grants: exact browser version/session, artifact digest and fixed elements, claim and checkpoint before navigation/read/click, compare actual DOM digests, revoke open ancestor and deny new operation, close discards receipt. Four added real cases (16 planned); only exact dependency source files copied into private read-only mount. Browser session startup and intentional host-denial probe remain separate CI mechanics, no production authorization or atomic OS effect claim.

CAP-04 #344 real 12/12 PASS; tighten denial classifier to exact observed Gecko error-page prefix/code/target with bounded fields, reject bare network strings, embedded errors, duplicate parameters, wrong target and fragments. Four regression methods. Preserve actual evidence and no generic error accepted.

CAP-04 browser preflight: exact fixed HTML digest, read-only fixture code/input and denied work writes, dropped effective/permitted/ambient capabilities, inherited bounded per-process core/file/CPU/fd limits (0,32MiB,60s,1024). Four added real checks, 20 planned. Browser profile needs its own file limit; no total memory/disk/process quota claim or security relaxation. Fail before startup if preflight fails.

CAP-04 actual WebDriver window handle replaces synthetic tab token; require exact single window and unchanged HTML digest before every authorized fixed operation, reject unexpected DOM content before issuing receipt. Explicit denied host ceiling fixture negative and unchanged page, two additional actual checks (22 planned). Fixed synthetic CI only; no generic arbitrary tab control or atomic effect guarantee.

CAP-04 terminal failure handling: revoked/expired ancestors close offline browser fixture at prepare/begin/active checkpoint, clear receipt and revoke all claims; enforce bounded operation count before planning. Three regression methods. No actual browser teardown on its own, real CI wrapper owns teardown; not production runtime.

CAP-04 non-idempotent fixed local click fixture: repeat click increments a counter and changes DOM text; inspect exact counter and visible text after explicit host deny and ancestor revocation to distinguish an unintended repeat effect. No extra case count (22 planned). Trusted test-only observation, no generic selector or production operation.

Gateway dense-chain performance: #349 199/199 tests but 64-operation limit test consumed ~24–25s whole suite because group checks re-evaluated shared ancestors. Deduplicate exact grant/request evaluation within one lock/time observation; repeated forged grant/request and unclaimed/revoked ancestor still denied. Four regression methods including exact validation count. No production changes or relaxed policy.

CAP-08 local speech: exact single-language eSpeak NG text-to-WAV request and canonical bounded mono PCM receipt (source/language/output/hash/rate/frame count). Six synthetic tests for provenance, malformed/truncated/silent audio, cancellation and policy. No actual audio device, voice playback, Piper or runtime activation.

CAP-08 actual eSpeak NG stock TTS in disposable offline bubblewrap, fixed public TR/EN/DE sentences, exact preclaim, canonical PCM receipt and source preservation, distinct outputs and no audio device. Eleven planned real cases. No Piper/Whisper, intelligibility or owner microphone/speaker acceptance. No external network at inference time.

CAP-08 first real pinned CPU LLM experiment: llama.cpp b6000 exact source 4762ad7316dcdec20016ab5985fb46a27902204d, public stories260K GGUF exact HF revision 688f41ddf8f3622d08c6954a061ff12999b6c8d8 and SHA256 047bf46455a544931cff6fef14d7910154c56afbc23ab1c5e56a72e69912c04b. Download/build only in disposable CI dependency phase; actual server and two fixed public native completion requests in isolated network namespace, exact request digest and typed bounded response, live grant/revoke. Thirteen planned actual cases plus three response regressions. Not Jan/Qwen, actual chat quality or production effect authorization. No owner files, credentials or external inference traffic.

CAP-08/06 speech→audio conversion chain: completed exact WAV receipt derives a distinct FFmpeg grant bound to actual speech output/digest and distinct staging output. Persist live ancestor edges in ConversionFixture; revoke speech invalidates downstream result, wrong digest and aliases denied. Three new synthetic regressions. No audio device or real conversion in this contract.

CAP-08/06 real eSpeak→FFmpeg chain: per-language WAV-only read mount and fresh output, separate live dependent grant, real lossless FLAC and exact raw PCM roundtrip, source preservation, typed receipt then ancestor revoke invalidates it. Twelve added actual checks (23 planned). No user audio, playback or production activation.

CAP-08 narrow each real TTS invocation to its own read-only fixed text and fresh private output; preflight exact digest and empty output/work mount set for each language, prior languages not visible. Three additional real cases (26 planned), preserve exact FLAC chain. Same-UID hostile mutation and aggregate quotas remain outside fixture claims.

CAP-08 first actual pinned Whisper.cpp CPU STT experiment, v1.8.2 source 4979e04f5dcaccb36057e059bbaed8a2f5288315, upstream public jfk.wav git blob 3184d372cd2f8b804d3a540c70ec50d927b335d2, multilingual tiny model exact HF revision fb5ed9369c26eaf08392504d8446da431e0fffb9 and SHA256 be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21. Separate public provisioning/build; actual isolated English transcript with exact source/model/language/output grant, expected phrase, no microphone/GPU, bounded output and revoke. Nine planned real cases and four regression methods. Not multilingual STT accuracy, Turbo, Jan, real microphone or production acceptance.

CAP-08 local LLM→speech: derive exact text bytes only from a completed live LocalAdapter result, bind selected voice language/output in a separate speech grant and persist parent edge. Four regression methods for completion, aliases, mismatched language, revoked parent and separate write policy. Fixture only; no automatic TTS or production permission.

CAP-08 actual llama.cpp→eSpeak NG in isolated CPU LLM fixture: each real completed model result requires a separately issued exact speech grant and immutable input digest, synthesize to private WAV (no playback), validate typed PCM receipt, then revoke ancestor and refuse dependent result. Four added real cases (17 planned). No actual Jan/Qwen chat quality, automatic TTS or owner hardware.

CAP-08 typed transcript→local LLM→speech chain: exact completed live transcript, source/model/result digests and explicit response language derive distinct local turn grant, then distinct voice output grant. Five new regressions for immutable payload, wrong language, cloud denial, and ancestor revoke across all three stages. No auto-submit, automatic playback or actual end-to-end inference claim.

CAP-08 #360 HOLD: espeak --version died SIGXFSZ before first inference, 7 earlier/host checks passed; cause not proven. Successor disables unnecessary PulseAudio autospawn/shared memory via private config and nonexistent private socket, moves version probe before server and bounds capture; no resource-limit increase or security relaxation. Real 17-case rerun required.

CAP-08 bind transcript-selected response language to the LocalAdapter voice-turn input and reject mismatched downstream speech language, before granting any effect. Two regression methods. This is selected-language consistency, not automatic language identification or semantic language quality.

CAP-08 exact native voice transport: fixed English stories260k native completion profile; normalized transcript envelope commits to actual canonical HTTP payload digest, with exact model/provider and language. Separate normal/native grants, reject wire mutation/replay/revocation. Four regression methods. No arbitrary endpoint or production transport authorization.

CAP-08 real three-engine chain, six additional planned checks (23 total): pinned Whisper.cpp sample/model, actual verified transcript, exact transcript-derived native request bytes and mutation denial, actual llama.cpp response, separately granted eSpeak WAV, transitive revoke across all three. Separate public pinned dependency provisioning, isolated CPU fixture only. No conversation intelligence, Jan/Qwen/Piper, microphone, playback or production runtime claim. #362 baseline audio initialization correction still requires real rerun.

CAP-08 voice envelope hardening: exact canonical schema, source/model/text/parent digests, required explicit response language, exact original claimed STT grant/request identity, no substitution of same-request alternate grant, native events refused before actual wire start. Four regression methods. Trusted fixture contract; no untrusted model events or production effect authorization.

CAP-08 speech→transcription fixture grant: exact verified WAV result, source digest, same selected language, exact model digest and distinct output. Persist dependency and invalidate transcript on revoke. Four regressions for provenance, alias, wrong model/language and revoked ancestor. Supports generated multilingual fixture testing, not microphone/quality acceptance.

CAP-08 actual generated multilingual roundtrip attempt: separate per-language eSpeak WAV→pinned Whisper Tiny CPU, exact source/model/language grant, private scope, explicit WER <=40% gate chosen before run, withhold receipt if semantic gate fails, revoke parent. Fifteen planned additional actual checks (41 with existing 26), four quality regressions. Generated public TTS only, not natural speech accuracy or mixed-language quality. Preserve failing results; no threshold tuning after observation.

CAP-08 native tiny-model output contract tightened to exact declared 16-token request budget and pinned 512-token vocabulary, limit stop must have exact 16 tokens, EOS within bounds, no control/surrogate content. Three regressions. Closed pinned fixture only, not arbitrary model limits or production protocol.

CAP-08 #368 Tiny multilingual actual result: TR 4/5 errors (80%, FAIL), EN 0/6 (0%, PASS), DE 1/7 (~14.29%, PASS). 13/15 added cases PASS, Turkish semantic and receipt withheld; existing 26 cases PASS. Preserve frozen failure. Compare Base and Small pinned upstream models (HF revision fb5ed9369c26eaf08392504d8446da431e0fffb9) in separate jobs, exact allowlisted SHA/size, unchanged same text and predeclared 40% WER gate, no auto fallback. STT uses already established Whisper CPU90s/outer120s budget, original TTS/FFmpeg limits unchanged. No natural-speech or production quality acceptance.

CAP-08 explicit cleanup of fixed fixture-owned text/WAV staging after each revoked LLM result and the integrated transcript; observe absence in private tmpfs and repeat idempotent cleanup in finally. Three added actual checks in integrated mode (26 planned), two in basic mode (19 planned). Not secure erasure, arbitrary user-file deletion or production cleanup guarantee. Namespace teardown remains last containment layer.

CAP-08 register immutable completed transcript provenance in trusted fixture gateway, then atomically validate the exact registered receipt when claiming a voice turn. Three regressions: self-consistent forged text/wire digest, source/model/artifact substitution, claimed but uncompleted STT grant. Registry bounded by existing grant limit. No production security boundary or untrusted caller authentication claim.

CAP-08 explicit owner voice review: exact transcript parent and turn target (model/identity/language/wire) require separately issued owner/private review grant. Atomic consumption and dependency linking with turn, no auto-submit, single-use review, reject local/cloud actor, revocation invalidates turn and voice. Four new regressions plus one real negative check (27 planned). Trusted fixture owner grant only, not actual UI consent/authentication.

CAP-08 expand fixed multilingual generated corpus from 3 to 9 utterances (3 per TR/EN/DE) with pinned Small; 68 TTS/FLAC/isolation and 45 STT checks planned (113 total). Original three examples and unchanged per-sample WER <=40% gate retained, six new predeclared public inert utterances, distinct scopes/grants, no automatic action execution. Preserve #368/#370 Tiny/Base failures. One corpus structure regression. Not natural speech or mixed-language acceptance.
