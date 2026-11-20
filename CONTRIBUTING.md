# Contributing to TIHULU JARVIS

Thank you for your interest. The repository is in **pre-alpha**; the public contribution and licensing policy must be finalized before accepting substantial external contributions.

## Development workflow

1. Read the [roadmap](docs/ROADMAP.md) and open a scoped issue before large changes.
2. Work on a feature branch and submit a **draft Pull Request**.
3. Keep PRs focused; separate interface, model, permission, and installer changes when feasible.
4. Add deterministic, offline-capable tests and document any hardware-dependent HOLDs.
5. Never introduce unreviewed remote installers, system-wide privilege changes, telemetry, or automatic model downloads.
6. Use clear English in source files, docs, commits, issues and PRs.
7. Describe files changed, test evidence, privacy/security effects, migration compatibility, and rollback.

## Code and data hygiene

Never commit API keys, access tokens, credentials, real voice recordings, personal documents, private filesystem information, real system logs, large model weights, or generated caches. Use synthetic fixtures. All bundled model weights and voice packs require explicit redistribution and license review.

## Installable release criteria

A public install button or installation command must not appear until a clean installation has been verified on declared target versions, with upgrade/uninstall, model setup, permission boundaries, release hashes, and rollback documented.

## Licensing

An explicit project license has not yet been selected; do not assume a right to redistribute code or third-party assets solely because the repository is public.
