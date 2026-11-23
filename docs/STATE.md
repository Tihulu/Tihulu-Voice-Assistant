# Development checkpoint

Base: Tihulu/Tihulu-Voice-Assistant `main` at `29cc369`, the PR #19 merge commit.

Landed on `main`: the portable multilingual core, the mode and profile policy core, the
profiles page with policy-gated controls, the AI Market page with the approved-source
download flow, and the consolidated line - curated catalog discovery, mode and endpoint
enforcement, tool-rule enforcement, the approved loopback transport and the download queue.

Verified on this base: 122 core tests and 59 native GTK tests pass; `compileall` is clean;
PR #19's CI (`test (3.10)`, `test (3.12)`, `gtk`) was green before the merge. See
`TEST_EVIDENCE.md`.

In flight: the feature line stacked below the open pull requests (`feat/web-targets` and
its predecessors - transfer resume, item revisions, update flow, queue persistence, catalog
trust, Debian packaging, owner-scoped HTTPS catalog fetch, audit trail, escalation consent,
file transcription, explicit routing, resource leases, privacy disclosure, state labels,
conversation search, attachments, the routing page, audit filters, streaming replies, text
scale, keyboard navigation, receipts, approved web targets), then open pull requests #43
upward (activity board through the record card).

HOLD: physical acceptance (microphone -> Whisper -> Jan -> Piper), desktop integration on
COSMIC or Wayland, a real-network transport for artifacts and a clean-machine install.
These need a real machine and operator-chosen models; nothing here claims them.

Rollback: this document is documentation only; no installation, model file or configuration
is touched by updating it.
