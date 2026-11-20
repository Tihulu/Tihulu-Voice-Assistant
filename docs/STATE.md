# Development checkpoint

Base: Tihulu/Tihulu-Voice-Assistant main at d548f7c04d62dcf4133a17d0adba542d472c0dcf. The existing documentation draft PR #1 was open at 083a524c7905fc588ad6326251f2126419d3a5dd when reviewed. This source candidate has no assigned PR number or remote commit.

Migrated: two pure core modules and their 21 + 8 synthetic tests. Added: loopback Jan HTTP client, cancellable local chat/voice, native GTK client, local installed model/voice selection and a PR-only CI job. New tests: five Jan tests and five session/audio/choice tests. Test definitions are not execution evidence. Native Linux acceptance, provider and device integration, clean install and release are HOLD. Recheck public CI for the actual feature commit.

Rollback: no main or live installation changes are required for this candidate. Abandon the isolated feature branch or revert only its feature commit; preserve shared model files, configuration and the legacy application.

Observed test results: original 29/29; candidate 38 passed, one native TCP fixture not run. See TEST_EVIDENCE.md. A review branch in a bundle is not a published GitHub PR.
