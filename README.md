# Tihulu Voice Assistant

**Codename:** Jarvis  
**Target:** Pop!_OS 24.04 COSMIC / Wayland (portable to Ubuntu/Debian, later macOS/Windows)  
**Status:** Planning — recovering from known defects, building first vertical slice

> **Product name:** Tihulu Voice Assistant  
> **Project codename:** Jarvis (technical package names retained pending migration decision)

---

## What it is

A **local-first, multimodal task assistant** that:

- Accepts **natural voice** (push-to-talk, wake word) and **text** input
- Routes through **approved local models** (Jan/Qwen initially) or authorized cloud
- Executes **real tasks** via authorized local tools (shell, files, browser, desktop)
- Returns **rich chat artifacts** (charts, tables, images, citations, downloads) + **spoken summary**
- Supports **four operating modes** with strict privacy boundaries

**NOT** a static frontend, chat demo, model list, or framework exercise.

## Quick links

- [Product Scope & Requirements](docs/PRODUCT_SCOPE.md) — full scope, traceability matrix, design decisions
- [Recovery Roadmap](docs/RECOVERY_ROADMAP.md) — phased milestones, current state audit, gates
- [ChatGPT Handoff](docs/CHATGPT_HANDOFF.md) — ready-to-copy prompt for external review
- [Development Guide](docs/DEVELOPMENT.md) — setup, architecture, workflow
- [Release Runbook](docs/RELEASE.md) — packaging, recovery, QA
- [Traceability Matrix](docs/TRACEABILITY.md) — requirements to tests mapping
- [Test Evidence](docs/TEST_EVIDENCE.md) — documented test results
- [Native Acceptance](docs/NATIVE_ACCEPTANCE.md) — COSMIC/Wayland acceptance criteria
- [Stack](docs/STACK.md) — technology stack overview
- [Catalog Sources](docs/CATALOG_SOURCES.md) — AI model catalog definitions

## Authoritative specifications

Full spec suite: `TIHULU_JARVIS_OPENCode_PACKAGE/`

| File | Content |
|------|---------|
| `01_AUTHORITATIVE_MASTER_SPEC.md` | Mission, 10 capabilities, non-goals |
| `02_BASELINE_AND_HISTORY.md` | Current state, known defects, prior work |
| `03_ARCHITECTURE_AND_INTERFACES.md` | Target architecture, bounded contexts, contracts |
| `04_SECURITY_AND_PERMISSIONS.md` | Policy engine, audit, privacy |
| `05_FOUR_MODES_AND_PROFILES.md` | LOCAL_ONLY, CLOUD_CHAT, SECURE_HYBRID, CUSTOM |
| `06_VOICE_STT_LLM_TTS.md` | Voice pipeline, model selection |
| `07_AI_MARKET_AND_MODELS.md` | Model discovery, AI Market |
| `08_UI_UX_AND_DESKTOP.md` | Modern UI, COSMIC, icon, accessibility |
| `09_AGENTS_OPENCODE_AND_PROVIDERS.md` | Multi-agent orchestration, OpenCode, providers |
| `10_TOOLS_BROWSER_DESKTOP_GIT.md` | Tool interface, browser, desktop, Git |
| `11_ROADMAP_AND_TASK_GRAPH.md` | Milestone dependency graph |
| `12_TEST_AND_ACCEPTANCE.md` | Evidence-based acceptance tests |

## Priorities

R0 baseline recovery → R1 real local task → R2 research and rich chat → R3 inference/providers and secure modes → R4 application engines and OpenCode → R5 release and remaining scope. See the [roadmap](docs/RECOVERY_ROADMAP.md) for acceptance gates.

## Operating modes

| Mode | Reasoning | Host tools | Disclosure |
|---|---|---|---|
| LOCAL_ONLY | Local | Scoped local | No cloud inference; optional web / strict offline |
| CLOUD_CHAT (Cloud Only) | Cloud | None | Explicit chat content only |
| STANDARD_HYBRID | Local + cloud coordination | Local executor; cloud proposals allowed subject to policy | Explicit scoped approval |
| SECURE_HYBRID (Tihulu-Auto) | Local planner + cloud advice | Local planner and executor only | Mandatory minimum-necessary local filtering |

CUSTOM is an unlimited saved profile facility, not a bypass or fifth effective preset. Session Auto-Approve is OFF by default, scope-limited, revocable and reset on session/mode changes. Sudo uses OS authentication only; no model receives or types credentials. Native inference, Jan and OpenCode connectors, optional installed Application Engines, and official provider OAuth/API key flows are required, subject to verified compatibility and authorization. See the [authoritative recovery roadmap](docs/RECOVERY_ROADMAP.md) for full contracts and gates.

## Getting started

See [Development Guide](docs/DEVELOPMENT.md) for setup instructions.

## Status

| Item | State |
|------|-------|
| Main branch | `20f1349` — stack landing recorded |
| CI | PR #77: test, GTK and package passed; triggers failed (missing branch) |
| GTK crash | UNKNOWN root cause (reported, not proven) |
| M1 tests | 356 core / 602 GTK reported for record-card baseline; owner acceptance HOLD |
| This branch | `plan/recovery-roadmap` — docs only, no product code |

## License

See LICENSE file in repository root.
