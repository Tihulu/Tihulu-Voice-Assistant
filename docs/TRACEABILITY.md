# Requirements traceability

Each row of the specification is mapped to the code that implements it and the
test classes that prove it. The mapping is checked by a tool, so a row that
points at a test class or a module that no longer exists fails the check rather
than quietly becoming a claim.

How to check it:

```sh
PYTHONPATH=src:. python3 -m unittest discover -s tests      # the suites below
python3 tools/trace_check.py                               # this document
```

## Modes, profiles and permissions (file 05)

| Requirement | Code | Test classes |
| --- | --- | --- |
| Four operating modes with capability envelopes | `profiles.py` | `ModeTests`, `ProfileTests`, `PolicyTests` |
| Scoped allow / deny / ask rules, narrower wins | `profiles.py` | `RuleTests`, `ActionEnforcementTests`, `DescribeTests` |
| Endpoint enforcement per mode; loopback only | `profiles.py`, `voice/jan.py` | `EndpointEnforcementTests`, `EndpointScopeTests`, `EndpointTests` |
| Lifecycle, revisions and rollback | `profiles.py` | `StoreTests` |
| Audit trail of policy decisions, bounded | `profiles.py` | `AuditTrailTests`, `AuditFilterTests`, `TokenTests` |
| Privilege escalation needs trusted-UI consent | `profiles.py`, `desktop.py` | `EscalationTests`, `EscalationConsentTests` |
| Import / export sanitized, no credentials in the file | `profiles.py` | `ImportExportTests`, `LocalChoicesTests` |
| Presets are never weakened by cloning | `profiles.py` | `TemplateTests`, `RegistryTests` |

## Market and models (file 07)

| Requirement | Code | Test classes |
| --- | --- | --- |
| Discovery from Jan inventory and a curated catalog | `market.py` | `InventoryTests`, `CatalogTests`, `PartialDiscoveryTests` |
| Truthful cards, digests and revisions | `market.py` | `CardTests`, `RevisionHistoryTests`, `UpdateTests` |
| Local file import without downloads | `market.py` | `FileImportTests` |
| Approved loopback transport, resume, cancel, discard | `transfer_http.py`, `market.py` | `TransferTests`, `ResumeTests`, `ResumeTransferTests`, `TransportTests` |
| Remote catalog fetch over an owner allowlist | `catalog_fetch.py` | `FetchTests`, `NoNetworkTests` |
| Catalog trust: owner-managed HMAC keys gate install | `market.py` | `CatalogTrustTests` |
| Explicit routing: local never silently becomes cloud | `routing.py` | `PermissionTests`, `SelectionTests`, `WarningTests`, `PlanTests`, `TableTests` |

## Voice: STT, LLM, TTS (file 06)

| Requirement | Code | Test classes |
| --- | --- | --- |
| Capture, transcription, bounded replies, playback | `voice/audio.py`, `voice/m1.py` | `SessionTests`, `ResponseValidationTests`, `BoardTests` |
| Transcription of an owner-supplied recording | `voice/audio.py` | `TranscribeFileTests`, `UsageTests` |
| Response language and preservation across turns | `voice/session.py`, `voice/m1.py` | `LanguageRoutingTests`, `SelectedVoiceTests`, `TurnTests` |
| Spoken text normalization, never silent corruption | `voice/m1.py` | `SpeechNormalizationTests`, `SpokenTextTests` |
| Streaming a typed reply as the server sends it | `voice/jan.py` | `JanStreamTests`, `CountTests` |

## UI and desktop (file 08)

| Requirement | Code | Test classes |
| --- | --- | --- |
| Native GTK client with real widgets | `desktop.py` | the whole `tests/gtk` suite (66 classes) |
| Cancellation, awaited cleanup, late progress ignored | `desktop.py`, `voice/session.py` | `CancellationTests` and the GTK cancellation cases |
| Modes, profiles and policy-gated controls | `desktop.py` | `ProfileUITests`, `ActionEnforcementUITests`, `EndpointModeTests` |
| Search, filters, attachments, receipts | `search.py`, `attachments.py`, `receipts.py` | `FindTests`, `SnippetTests`, `AttachmentTests`, `ReceiptTests`, `ReceiptUITests` |
| Readiness, task board, queue summary | `readiness.py`, `tasks.py` | `ReportTests`, `RowsTests`, `ReadinessUITests`, `ActivityBoardTests`, `QueueSummaryTests` |
| Keyboard navigation with a stated table | `keys.py` | `TableTests` is in routing; keyboard uses `KeyboardTests`, `KeysHelpTests` |
| Display scale without motion; settings page | `a11y.py`, `settings.py` | `ScaleTests`, `TextScaleUITests`, `SettingsPageTests` |
| Local conversation export | `transcript.py` | `RenderTests`, `WriteTests`, `TranscriptExportTests` |
| Mute suppresses output, never the answer | `desktop.py`, `states.py` | `MutedTokenTests`, `MuteTests` |

## Security, packaging and release (files 04, 12, 13)

| Requirement | Code | Test classes |
| --- | --- | --- |
| Locking/exclusive resources per turn | `locks.py` | `LeaseTests`, `LeaseContextTests` |
| Privacy boundary disclosure before content | `disclosure.py` | `BoundaryTests`, `FactsTests` |
| Approved web targets: reading and submitting | `web.py` | `TargetTests`, `PlanTests`, `PreviewTests` |
| Local folder reading behind the filesystem policy | `files.py` | `FolderTests`, `InsideTests`, `PreviewTests` |
| Stored preferences validated on read | `settings.py` | `SpokenTextTests`, `TextScaleTests`, `WebTargetTests`, `ForgetTests` |
| Debian package content and install verification | `packaging/deb/build.sh` | the CI `package` job (see `docs/RELEASE.md`) |
| Clean-machine install verified by a smoke test | `tools/smoke.py` | 21 checks, run in CI from the installed wheel and the deb root |
| The stack is mergeable in the documented order | `tools/stack_check.py` | `ChainTests` |
| The machine says what it is missing | `tools/preflight.py` | `RowsTests`, `VerdictTests` |

## What is deliberately not here

Physical acceptance — real capture, real playback, real model quality on a real
desktop — has no automated row, because it cannot be run here. It is the owner's
checklist in `docs/NATIVE_ACCEPTANCE.md`, and `tools/preflight.py` states what
that checklist needs before it starts.
