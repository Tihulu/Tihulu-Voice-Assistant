# Development checkpoint

## Published core

- Repository: https://github.com/Tihulu/Tihulu-Voice-Assistant
- Core draft PR: #4, `feat/m1-portable-core`.
- Core commit: `20116430dc2caa8426c817838194dedb573102fd`.
- Ubuntu CI run [44664461595](https://github.com/Tihulu/Tihulu-Voice-Assistant/actions/runs/44664461595): success on Python 3.10 and 3.12, including the native loopback TCP fixture (39 tests per interpreter).
- Main was still `d548f7c04d62dcf4133a17d0adba542d472c0dcf` at publication. Core PR #4 and documentation PR #1 remain separate drafts.

## Current increment

`feat/native-voice-controls` is based on the exact core commit above and tracks #5. Its PR targets `feat/m1-portable-core` to keep the native increment reviewable independently of #4. Retarget after the dependency is merged; never auto-merge.

Changes: independently constructible GTK window and GLib/asyncio bridge; idempotent turn/playback cancellation; awaited device cleanup; stale progress suppression; response-locale-only voice validation. New tests exercise actual GTK widgets using synthetic backends and native process cleanup using a short-lived Python fixture. Physical audio and model inference are separate gates.

Observed for `feat/native-voice-controls` commit `beaac21`: local Xvfb reproduction exposed a single-test-harness defect (a Gtk.Application exports its D-Bus interface at its application id and cannot export it twice in one process), fixed and re-verified locally, then GitHub CI run 44674465300 succeeded for `test (3.10)`, `test (3.12)` and `gtk`. Core suite: 45 cases per interpreter; native GTK suite: 11 cases. See TEST_EVIDENCE.md. Physical COSMIC and audio acceptance remain HOLD.

## Remaining product gates

- Physical COSMIC window/input behavior and microphone -> Whisper -> Jan -> Piper.
- Live TR/EN/DE recognition, response and audio quality.
- AI Market lifecycle, enforced modes and isolated persistent profiles.
- Authorized integrations, installable release, clean-machine install/recovery.

Manual native acceptance: NATIVE_ACCEPTANCE.md. This is a development increment, not a verified release.

## Recovery

Changes are confined to feature branches. To abandon this increment, discard its branch or revert its feature commit after review. Preserve the core branch, legacy installation, shared models and private configuration. No live service or shared model migration is part of this change.
