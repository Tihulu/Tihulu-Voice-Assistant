# TIHULU JARVIS — Development Roadmap

This document records **targets**, not completed features. Evidence from tests, GitHub Actions, and owner acceptance is required to mark a milestone complete.

| Stage | Scope | Exit criteria |
| --- | --- | --- |
| R0 | Public repository foundation | README, contributing/security policy, PR workflow, safe project manifest |
| R1 | Recover and audit local baseline | Sanitized inventory; no credentials or private workstation data committed; verified rollback |
| R2 | Multilingual core and Jan adapter | Isolated tests + real locally authorized Jan response + voice mapping |
| R3 | Modern desktop Chat/Voice shell | Working native launch, original icon, real status and functioning voice controls |
| R4 | Audio pipeline | STT→LLM→TTS real user test; TR/EN/DE; stop/cancel; no background listening by default |
| R5 | AI Market | Installed inventory; independent STT/LLM/TTS selectors; reviewed model import/install workflows |
| R6 | Four operating modes and profiles | Actual runtime DENY/ASK/ALLOW, persistence, isolation, negative security tests |
| R7 | Agent and tool integrations | OpenCode, browser, desktop, GitHub/GitLab with scoped approvals |
| R8 | Multimodal and remote UI | Documents/images/visual responses; authenticated remote access |
| R9 | Public pre-release and stable packaging | Clean-room tests, checksums, license/asset audit, uninstall/rollback, release notes |

## Task ownership

A lead coordinator may use up to six specialized subagents: (1) security, (2) voice and multilingual core, (3) UI/native desktop, (4) AI Market and model runtime, (5) modes/profiles, (6) tools, CI and QA. Separate task scopes, checkpoints, tests, and dependency ordering are required.

## Release policy

- Use issue → feature branch → draft PR → CI → review → explicit merge.
- Never push unreviewed local configurations, model weights, personal recordings, logs, keys, or generated secrets.
- CI must run on public fixtures, not depend on the owner’s workstation or personal Jan installation.
- A passing unit-test suite does not prove real speech, security isolation, or installability.
- Development artifacts and packages are not automatically labeled stable.
- Release only supported installable formats whose launch, upgrades, uninstall and rollback have been tested on clean target systems.
- Select an explicit source license before accepting broad contributions or shipping a public installer.

## Installer deliverables (planned)

Prefer an auditable release artifact (for example a tested .deb or AppImage as technically appropriate) with published checksums and release notes. A convenience one-line installer may be offered only after it has been independently reviewed; it must fetch pinned, verified artifacts and must not execute arbitrary changing remote scripts.
