# Tihulu Voice Assistant — ChatGPT Handoff

> Copy everything in the box below and paste it into a new ChatGPT session.

---

```
You are working with the Tihulu Voice Assistant project on GitHub.

REPOSITORY
- URL: https://github.com/Tihulu/Tihulu-Voice-Assistant
- Base commit on main: 20f1349 (as of planning)
- Current planning branch: plan/recovery-roadmap (draft PR in progress)

PRODUCT
- Name: Tihulu Voice Assistant (codename: Jarvis)
- Type: Local-first multimodal task assistant for Linux desktop (Pop!_OS 24.04 COSMIC primary)
- Core loop: voice/text → model → authorized local tools → verified results → rich chat + spoken summary
- NOT a static frontend, model list, or chat demo

AUTHORITATIVE SPECS (reference, do not fabricate)
The full specification suite is in /home/horseman/tihulu_jarvis/TIHULU_JARVIS_OPENCode_PACKAGE/:
  01_AUTHORITATIVE_MASTER_SPEC.md — Mission, capabilities, non-goals
  05_FOUR_MODES_AND_PROFILES.md — LOCAL_ONLY, CLOUD_CHAT, SECURE_HYBRID, CUSTOM
  08_UI_UX_AND_DESKTOP.md — COSMIC UI, icon, Wayland, accessibility
  09_AGENTS_OPENCODE_AND_PROVIDERS.md — Multi-agent orchestration, providers, OpenCode
  10_TOOLS_BROWSER_DESKTOP_GIT.md — Tool interface, browser, desktop, Git integration
  11_ROADMAP_AND_TASK_GRAPH.md — Milestone dependency graph
  12_TEST_AND_ACCEPTANCE.md — Evidence-based acceptance per test

REQUIREMENTS
See docs/PRODUCT_SCOPE.md for the full traceability matrix. Summary:
  P0: Evidence audit, crash diagnosis (GTK root cause UNKNOWN), naming, setup
  P1: Voice → synthetic CSV → local computation → chart in chat → spoken summary → follow-up → cancel
  P2: Firefox/background research with citations; AI Market
  P3: Desktop automation connector; OpenCode session delegation
  O:  Packaging (.deb), CI green, owner verification

PRIORITIES
1. Evidence/status audit and crash diagnosis
2. First real vertical slice (voice request → computed chart → spoken summary)
3. Firefox research
4. Desktop/OpenCode connector
5. Market tools (secondary)

DESIGN DECISIONS (already made, do not re-debate)
- Local Jan/Qwen executes ALL host shell operations; cloud NEVER gets direct shell
- Firefox research via dedicated browser adapter (separate profile, not user's personal profile)
- Chart artifact rendering: evaluate existing Open WebUI first; if incompatible, evaluate Tauri
- Four modes: LOCAL_ONLY (no cloud inference), CLOUD_CHAT (no host tools), SECURE_HYBRID (sanitized cloud), CUSTOM
- Unlimited profiles with per-profile scoped permissions; import → disabled until re-authorized
- No automatic model downloads; no duplicate downloads
- CI must be green before merge; NO automerge

CURRENT KNOWN STATE (must be reverified)
- Main at 20f1349; 80+ feat branches exist
- GTK picker-associated crash reported but ROOT CAUSE UNKNOWN
- M1 code reported 29 isolated test passes (no live Jan/voice/GUI)
- Jan server previously responded; model IDs not guaranteed current
- Open WebUI installed; integration NOT verified
- OpenCode Tihulu-Auto compatibility must be AUDITED, not assumed
- PR #77 historical failure contradicts "all green" claim — audit required

WHAT YOU MUST DO — STEP BY STEP

Step 1: Inspect the actual repository
  a. Clone or fetch https://github.com/Tihulu/Tihulu-Voice-Assistant
  b. Verify current main branch SHA and HEAD commit message
  c. List all branches; identify which are stable vs WIP
  d. Check GitHub Actions CI status on main (all jobs: test, gtk, triggers, package)

Step 2: Inspect the actual codebase
  a. Read src/tihulu_jarvis/*.py for current package structure
  b. Read tests/ for existing test coverage
  c. Read docs/ for all existing documentation
  d. Read tools/ for preflight/trace/smoke scripts
  e. Read .github/workflows/ for CI configuration

Step 3: Audit against specs
  a. Compare current code against 01_AUTHORITATIVE_MASTER_SPEC.md capabilities
  b. Identify which requirements have code, which have tests, which are PLANNED
  c. Note the GTK crash — can you reproduce or find evidence?
  d. Verify Jan/Whisper/Piper integration claims

Step 4: Validate the recovery roadmap
  a. Read docs/RECOVERY_ROADMAP.md (on branch plan/recovery-roadmap)
  b. Read docs/PRODUCT_SCOPE.md for requirements matrix
  c. Assess if milestones are realistic given current code
  d. Identify any missing dependencies or blockers

Step 5: Execute the first vertical slice (P1)
  a. Create Jan text adapter that calls a local Jan endpoint
  b. Generate synthetic CSV data (lab example)
  c. Run local computation (pandas/numpy) on the CSV
  d. Generate a matplotlib chart and save as PNG
  e. Display chart as artifact in rich chat
  f. Generate spoken summary via Piper TTS
  g. Verify 4+ turn context retention
  h. Verify cancel/stop works

Step 6: Create acceptance evidence
  a. Each test must have: TEST_ID, expected result, actual result, PASS/FAIL/HOLD
  b. No self-certification — independent verification
  c. Security tests: deny paths must produce documented denial evidence
  d. Owner-verified tests: audio + UI must be physically checked

RULES
- NEVER claim capability without inspecting actual repo code and CI
- NEVER publish private files, host paths, tokens, lab data, or raw conversation
- NEVER fabricate access, permissions, or capabilities
- ALWAYS verify CI status from actual repository before asserting
- ALWAYS use sanitized metadata for model/tool discovery
- If any step fails, STOP and report; do not continue blindly
- Use the exact product name "Tihulu Voice Assistant" and codename "Jarvis"
- All documentation in English

REPORT FORMAT
When you report back, include:
1. Main branch SHA and HEAD commit
2. CI status (all jobs) on main
3. Code audit: which requirements have evidence vs PLANNED
4. Blockers found
5. Exact next milestone with acceptance criteria
6. Any local shell boundary issues discovered
```

---

## Notes for the human operator

- This handoff is designed for a ChatGPT session with **web access and GitHub CLI** (or manual repo inspection).
- **Do not** share `TIHULU_JARVIS_OPENCode_PACKAGE/` contents beyond what's summarized here — ChatGPT should reference the specs, not receive them.
- If ChatGPT lacks GitHub access, paste the key findings from steps 1–3 manually.
- The exact GitHub Actions CI status should be captured from the PR checks after publishing.
- Never include actual API keys, Jan model IDs beyond what's public, lab CSV data, or private file paths in any handoff.
