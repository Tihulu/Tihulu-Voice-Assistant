# Development checkpoint

Base: Tihulu/Tihulu-Voice-Assistant `main` at `939a567`, the record-card increment.

Landed on `main`: the portable multilingual core, the mode and profile policy core,
the profiles page, the AI Market page with the approved-source download flow, the
consolidated line (curated catalog discovery, mode and endpoint enforcement,
tool-rule enforcement, approved loopback transport, download queue), the feature line
below the open stack (transfer resume, item revisions, update flow, queue
persistence, catalog trust, Debian packaging, owner-scoped HTTPS catalog fetch, audit
trail, escalation consent, file transcription, explicit routing, resource leases,
privacy disclosure, state labels, conversation search, attachments, routing page,
audit filters, streaming replies, text scale, keyboard navigation, receipts, approved
web targets), and the open stack from the activity board through the record card,
including the merge rehearsal tools, the read-only pre-flight, the traceability
check, the release runbook and gates, the approved catalog sources, the resource
profiles and idle wakeups, the measured process usage, the CI trigger automation and
the record-card provenance.

Verified on this base: 356 core tests and 602 native GTK tests pass; compileall is
clean; every CI job (test 3.10, test 3.12, gtk, triggers, package) is green; the
trigger-list check reports no missing and no stale branch. See TEST_EVIDENCE.md.

HOLD: physical acceptance (microphone -> Whisper -> Jan -> Piper), desktop integration
on COSMIC or Wayland, a real-network transport for artifacts and a clean-machine
install. These need a real machine and operator-chosen models; nothing here claims
them. The next gate is the owner's machine.

Rollback: this document is documentation only; no installation, model file or
configuration is touched by updating it.
