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
