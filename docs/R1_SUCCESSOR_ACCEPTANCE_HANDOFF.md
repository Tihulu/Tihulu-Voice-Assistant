# R0/R1 successor independent acceptance handoff

Status: draft, not accepted. This is a test contract, not proof of execution.
Review the exact final PR #100 head after its CI completes. Never replace the
frozen #94 FAIL verdict, stable installation, or main. No R2–R5 runtime enablement.

## Independent test matrix

| ID | Fixture | Required result |
|---|---|---|
| ADJ-01 | clone/import/rollback plain, timed, and expired ALLOW, credentials, automatic activation | no inherited grant or automatic capture |
| ADJ-02 | wall correction, monotonic advance, two stores, restart with intact floor, checkpoint loss with marker | observed expiry never revives within declared storage model |
| ADJ-03 | revoke or expire while backend is paused or streaming; backend ignores callback/cancellation | no newly unauthorized delta, retry, history commit or speech; already sent bytes cannot be recalled |
| GEN-01 | same-version file replacement during chooser, consent or chat history | refuse stale selection, approval and replay; explicit new conversation required |
| GEN-02 | source profile changes while confirming destination, target changes, late confirmation | no activation under stale consent |
| GEN-03 | concurrent create/save/rollback/delete, missing archive, history prefix collision | no silent overwrite, cross-profile rollback or replay of historical grants |
| PARSE-01 | duplicate keys, boolean strings, wrong numeric and scope types, FIFO/symlink/oversize import | bounded fail-closed; no automatic activation |
| AUDIT-01 | malformed/special/oversized log, raw URL credential canary, conflicting writer | no unsafe read or silent loss of existing rows; corruption visible; endpoint redacted |
| UX-01 | bad clock, missing/invalid profile at startup, bad secondary profile | no arbitrary profile fallback; bounded diagnostic; timed controls disabled |
| PERF-01 | local profile checks, long streaming turn, large bounded audit, repeated clock checks | measure actual UI latency and I/O; no unbounded read; ordinary stream identity check does not parse JSON |
| PKG-01 | exact wheel and Debian extract, staging sentinel, smoke, trigger checks | required CI and independent execution separately recorded |
| VOICE-01 | expire or replace profile during capture or while model reply is pending | refuse new transcript dispatch, reply history/publication and new speech; existing captured samples and in-flight request bytes cannot be recalled |
| DISC-01 | loopback service with unknown upstream, non-loopback refused, observed grant expiry | direct transport distinct from unknown upstream locality; no absolute privacy assertion; same nondecreasing clock for UI summary |
| OWNER-01 | actual supported OS, local Jan, microphone/speaker, cancellation, permissions, install/remove/rollback | owner evidence on real environment, not synthetic CI |

## Evidence and limits

Record exact commit/tree, source hashes, commands, platform, counts, skipped
tests and missing dependencies. Required Python 3.10/3.12, native GTK,
wheel and Debian layers must be independently reproduced or explicitly HOLD.
A source inspection or a GitHub-hosted CI PASS is not independent acceptance.
Use disposable state and synthetic canaries; do not install over stable.

The clock is not a trusted external time source. A valid older checkpoint
or removal of both checkpoint and marker after all processes exit is not
detectable by these files alone. Locks coordinate cooperating local writers,
not hostile same-UID code or noncooperating external writers. Profile and
archive updates are not a single crash-atomic transaction; archive recovery
does not replay permissions. Deletion preserves historical local records.
Audit is bounded and not tamper-proof; other categorical scope/reason inputs
must be trusted by the caller. Endpoint redaction does not erase old backups
or storage remnants. Audit write failures are reported, not treated as an
independent access-control grant. UI consent is still action-level, not the
full R3 scoped grant broker. Numeric loopback transport is not proof that a
third-party local service performs inference entirely locally. Do not claim
physical, upstream, process isolation, or release acceptance from these tests.

## Voice and disclosure boundary clarification

Voice capture uses the exact active profile generation and current clock, with
rechecks before capture, after returned transcription, at inference checkpoints,
and before new playback. This is not a periodic microphone watchdog: a capture
or playback already running can continue until its ordinary completion or owned
cancel/stop. A completed result is refused if its grant has changed. Test the
paused capture and paused backend cases separately. A reply committed while
still authorized is not retroactively erased by later expiry. The disclosure
UI reports only the direct loopback transport and explicitly unknown upstream
processing. Physical verification of the service and isolation remain HOLD.

## ADJ-04 successor (PR #101)

The frozen #100 remains unchanged. The successor requires an explicit trusted
checkpoint for every listen, speak and transcribe call; ordinary voice, preview,
selected recording and analysis speech supply existing profile-generation and
epoch checks. Active subprocess waits poll at a nominal 250 ms and each stage
checks before spawn, inside the scheduled spawn task, after spawn and before
publication. Test both transitions, active capture/playback drain, refused
spawn, timeout/cancellation, and all four native call sites. This is a nominal
polling interval, not a measured device cutoff deadline. OS spawn and cleanup
latency, hostile same-UID processes, existing captured/played data and real
hardware remain outside this guarantee. Low-level standalone child tests retain
their trusted default callback; production audio entrypoints have no default.

## Selected recording cancellation and identity

Selected recording now uses the same owned task/epoch and Hard Stop lifecycle.
A regular WAV is identified at selection, bound to the exact policy generation
through task binding, copied using descriptor-relative traversal to a private
mode-0600 snapshot, and checked again before invoking Whisper. Maximum source
size 256 MiB; copy chunks 256 KiB with cooperative yields. Test selection-to-task
replacement, same-version profile replacement during binding, FIFO rejection,
snapshot privacy/cleanup, and cancellation while transcription is paused.
The private copy is removed on ordinary completion or cancellation; hard
process/host crash cleanup and filesystem blocking latency are not certified.
No physical timing or full independent test reproduction claim.

## ADJ-05 transport successor

A paused-connect synthetic reproduction against frozen #101 showed a new
request write after simulated revocation (both ordinary and streaming paths).
The separate successor binds Jan to a trusted desktop route/epoch/operation
checkpoint and rechecks after semaphore acquisition and connect, before write,
after drain/response and before stream publication. Discovery, probe, analysis
classifier and conversation use the same seam; selected attachment and voice
operations add their existing live checks. No guarantee of recalling queued
bytes, OS-level atomicity or upstream isolation. Standalone transport fixtures
retain an explicit trusted default; production desktop constructors bind it.
The successor also shares the bounded HTTP header/framing parser across JSON
and SSE, decodes chunked/content-length/close-delimited bodies, bounds total
body and header bytes/count, rejects conflicting framing and invalid delta
types, and accepts LF or CRLF event lines. An explicit DONE marker is required;
known-length/chunked frames are validated through their end. This is the
supported single-JSON-data-line SSE subset, not a general-purpose HTTP client.
CI and independent evidence remain separate.
