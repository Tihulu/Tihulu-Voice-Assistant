# Tihulu Voice Assistant — Product Scope

> **Product name:** Tihulu Voice Assistant  
> **Project codename:** Jarvis  
> **Authoritative specs:** `/home/horseman/tihulu_jarvis/TIHULU_JARVIS_OPENCode_PACKAGE/` (files 01–19)

---

## 1. What this product is

Tihulu Voice Assistant is a **real, local-first, multimodal task assistant** for Linux desktop (Pop!_OS 24.04 COSMIC primary). It is **not** a static frontend prototype, a chat demo, a model list viewer, or a framework exercise. It is a production-grade application that:

- Accepts **natural voice and text input**
- Routes requests through an **approved local model** (Jan/Qwen initially) or an authorized cloud model
- Executes **real tasks** using authorized local tools (shell, file ops, browser, desktop)
- Returns **rich chat artifacts** (charts, tables, images, citations, downloadable files) **plus** a short spoken summary
- Respects **four operating modes** with strict privacy boundaries
- Preserves **conversation context** across follow-up turns and commands

## 2. User scenarios (ordered by priority)

| Priority | Scenario |
|----------|----------|
| P0 | Evidence audit, crash diagnosis, readable naming, clear setup |
| P1 | Voice request → synthetic CSV → local computation → chart in chat → spoken summary → follow-up → cancel |
| P2 | Firefox / background research with citations |
| P3 | Desktop automation connector |
| P3 | OpenCode session delegation with progress/cancel |
| P2 (secondary) | Market analysis tools |

## 3. Architecture principles

1. **Local-first, native voice survives without the web UI**
2. **Privacy boundary enforced at the runtime layer** — no cloud direct shell access
3. **Independent STT / LLM / TTS** per saved profile; no forced coupling
4. **No automatic model downloads or loading** — user discovers, selects, and approves
5. **Rich chat rendering architecture requires evaluation** — no blind framework rewrite
6. **Existing OpenCode Tihulu-Auto compatibility must be audited, not assumed**

## 4. Four operating modes

| Mode | Reasoning | STT/TTS | Local tools | Network |
|------|-----------|---------|-------------|---------|
| `LOCAL_ONLY` | Fully local | Local only | Approved local bridge only | Optional public web; strict-offline denies |
| `CLOUD_CHAT` | Cloud reasoning | Approved speech pipeline | **None** | Provider + approved cloud-safe research |
| `SECURE_HYBRID` | Cloud + local worker | Configured, consented | Jan/Qwen local shell only | Scoped; locally minimized results |
| `CUSTOM` | User-configured | Independent STT/LLM/TTS | Per-profile scoped | Per-profile |

> **Never** allow cloud models to execute host shell operations directly. All host operations are local authorized Jan/Qwen only.

## 5. Profiles and privacy

- Unlimited saved CUSTOM profiles; independently scoped permissions
- Template presets: Coding Agent, Research Assistant, Private Assistant, Daily Assistant, Custom Developer
- Model selection: STT, LLM, TTS chosen independently per language
- Credentials stored in OS secret store; never embedded in config, prompts, or exports
- Profile import → `disabled/pending_review` until re-authorized
- No source/model credentials in profile exports

## 6. Design decisions

### Firefox research vs isolated browser

Research is performed via a **dedicated controlled browser adapter** (Playwright extension or similar) with a separate user-approved browser profile. This is an explicit design choice:

- **Not** the user's personal Firefox profile (avoids credential/cookie leakage)
- **Not** a headless sandbox that cannot render JavaScript (ensures real web interaction)
- Domain-scoped permissions with separate read/navigation vs. submit/download controls
- Content previewed locally before any cloud disclosure

### Chart / artifact rendering

Rich chat output (charts, tables, images, citations) uses a **component evaluation path** — the existing Open WebUI or a compatible renderer is assessed against COSMIC/Wayland requirements. If incompatible, a standalone modern frontend (evaluated Tauri/Rust + web) is chosen. No blind migration.

## 7. Requirements traceability matrix

| User need | Current status | Milestone | Observable acceptance |
|-----------|---------------|-----------|----------------------|
| Natural voice input (push-to-talk, VAD, VAD) | PLANNED | M1/M2 | Real mic capture, transcribe, speak |
| Multilingual (TR/EN/DE) with context | PLANNED | M1 | 4-turn `mavi` with language switching |
| Real task loop voice→model→tools→result→chat+voice | PLANNED | P1 vertical slice | End-to-end synthetic CSV → chart → speech |
| Firefox / background research with citations | PLANNED | M7 | Browser adapter navigates, cites, screenshots |
| Local file/lab CSV analysis with real computation | PLANNED | P1 vertical slice | Computation produces chart artifact in chat |
| Charts/tables/images as downloadable chat artifacts | PLANNED | M2 | Artifact appears in chat, downloadable |
| Follow-up commands retaining context | PLANNED | M1 | 4+ turn history preserved |
| Cancel / Hard Stop | PLANNED | M1/M2 | Own audio stops; task stops without affecting shared services |
| Authorized local shell | SECURED in policy | M5/M7 | Jan/Qwen executes; cloud denied |
| Desktop app control (COSMIC/Wayland) | PLANNED | M7 | App launch through portal; unauthorized denied |
| OpenCode session delegation | PLANNED | M6 | Start/continue/stop visible with progress |
| Four-mode isolation | PLANNED | M5 | LOCAL_ONLY offline; CLOUD_CHAT no shell; SECURE_HYBRID sanitized; CUSTOM isolated |
| AI Market (model discovery, no dup downloads) | PLANNED | M3/M4 | Real STT/LLM/TTS discovered; independent selection |
| Provider credentials (secret store, never in chat) | PLANNED | M8 | API keys in OS store; exports clean |
| Profile import safety | PLANNED | M5 | Import → disabled until re-authorized |
| CI green before merge, no automerge | PLANNED | M10 | GitHub Actions pass; manual merge |
| Installer / package for Pop!_OS | PLANNED | M10 | .deb installs, launches with correct icon |

## 8. Synthetic lab example (P1 vertical slice)

```
User (voice):  "analiz et, bu dosyadaki veriyi grafige donustur"
STT (Whisper): "analiz et, bu dosyadaki veriyi grafige donustur"
LLM (Qwen):    parse CSV → compute stats → generate matplotlib chart → save PNG
Local Shell:   execute computation (Jan executor) → verify result
Chat output:   display chart PNG + statistics table
TTS (Piper):   "Veri analiz edildi. Ortalama 42.3, maksimum 98.7. Grafik sozlu ozet:"
Follow-up:     "mavi rengi de ekle" → retains chart context → updates chart
Cancel:        "dur" → stops speaking, returns to idle
```

## 9. Non-goals / prohibited

- No Iron Man / JARVIS movie iconography
- No fake waveform or dead buttons
- No model self-report as end-to-end proof
- No replacing existing apps/sessions without consent
- No internet required for LOCAL_ONLY functions
- No cloud shell access, ever
- No unlicensed assets

## 10. References

- Master spec: `01_AUTHORITATIVE_MASTER_SPEC.md`
- Four modes: `05_FOUR_MODES_AND_PROFILES.md`
- UI/UX: `08_UI_UX_AND_DESKTOP.md`
- Agents/OpenCode: `09_AGENTS_OPENCODE_AND_PROVIDERS.md`
- Tools/browser/desktop: `10_TOOLS_BROWSER_DESKTOP_GIT.md`
- Roadmap: `11_ROADMAP_AND_TASK_GRAPH.md`
- Acceptance tests: `12_TEST_AND_ACCEPTANCE.md`
