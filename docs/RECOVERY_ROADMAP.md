# Tihulu Voice Assistant — Recovery Roadmap

> **Status:** Planning branch — no product code altered  
> **Base commit:** `20f1349` (origin/main)

---

## Current state (evidenced)

| Area | Evidenced status | Notes |
|------|-----------------|-------|
| Repo structure | `main` at `20f1349` with 80+ feat branches | Extensive incremental feature history |
| CI | `test`, `gtk`, `triggers`, `package` jobs on PR to `main` | Tests run on `python -m unittest discover`; GTK tests require Xvfb |
| GTK crash | **UNKNOWN root cause** | GTK picker-associated crash reported by user; not proven |
| M1 code | Reported 21 M1 + 8 contracts tests passing | Only isolated unit tests; no live Jan/voice/GUI wiring |
| Jan server | Previously responded; model IDs `Qwen3_6-35B-A3B-UD-IQ4_XS` | Availability not guaranteed |
| STT/TTS | Hex/Whisper Large V3 Turbo preferred; Piper TR/EN/DE voices reported | Must re-verify |
| Open WebUI | Installed; integration not verified | Requires audit |
| OpenCode Tihulu-Auto | Exists in related efforts | Must audit compatibility, not assume |
| README | Vague — "Jarvis like linux desktop assistant" | Needs clear product statement |
| Documentation | `docs/` has STATE.md, STACK.md, DEVELOPMENT.md, RELEASE.md, TRACEABILITY.md, TEST_EVIDENCE.md, NATIVE_ACCEPTANCE.md, CATALOG_SOURCES.md | No PRODUCT_SCOPE, RECOVERY_ROADMAP, or CHATGPT_HANDOFF |
| PR #77 | Historical | Triggers failure contradicts all-green claim; requires audit |
| PR #72, #71, #70 | Most recent merged | Provenance increment, preflight/GPU check, CI trigger automation |

---

## Recovery strategy: sequential milestones with gates

### Phase P0 — Audit, naming, and crash diagnosis

**Objective:** Establish truthful baseline and safe checkpoint before any feature work.

| Task | Expected output | Gate |
|------|----------------|------|
| A.1 Full model/tool inventory detection | Sanitized metadata of active models, available tools | Checklist completed |
| A.2 GTK crash investigation (if feasible) | Reproduce or confirm cause; if not reproducible, document UNKNOWN | Report filed |
| A.3 Rename package to `tihulu_jarvis` consistently | Readable, non-generic naming in all imports/docs | `compileall` passes |
| A.4 README rewrite | Clear product name, codename, status, feature overview, links | Reviewed |
| A.5 Safe worktree checkpoint | Branch `plan/recovery-roadmap` with only docs | Push non-force |

### Phase P1 — First vertical slice: real voice → compute → chart → speech

**Objective:** End-to-end evidence that the core task loop works.

| Task | Expected output | Gate |
|------|----------------|------|
| B.1 Jan adapter (text-level) | `naber nasilsin` → TR reply via real Jan endpoint | Unit test + real Jan test |
| B.2 Synthetic CSV pipeline | Generate CSV → compute → matplotlib chart → save PNG | File exists, chart renders |
| B.3 Chart displayed in chat | Chart appears as attachment in rich chat | Visual verification |
| B.4 Spoken summary | Piper speaks chart summary | Audio verification |
| B.5 Follow-up retains context | 4+ turns with chart reference | Session history check |
| B.6 Cancel / Hard Stop | Stop speaking; task stops without affecting shared services | Negative test |

**Milestone gate:** Code exists on branch; unit tests run; owner verifies audio + UI.

### Phase P2 — Firefox / background research with citations

| Task | Expected output | Gate |
|------|----------------|------|
| C.1 Browser adapter | Playwright extension or similar with domain scope | Permission matrix enforced |
| C.2 Research workflow | Navigate → read → extract citations → return | Citation format verified |
| C.3 Private tab isolation | No cookie/session leakage | Negative test |
| C.4 Prompt injection defense | Browser content cannot change tool policy | Negative test |

### Phase P3 — Desktop and OpenCode connectors

| Task | Expected output | Gate |
|------|----------------|------|
| D.1 Desktop app control | COSMIC portal / D-Bus app launch | Authorized only |
| D.2 OpenCode session integration | Start/continue/stop real session with progress | Evidence-based status |

### Phase O — Packaging, release, QA

| Task | Expected output | Gate |
|------|----------------|------|
| E.1 CI green on all branches | `test` + `gtk` + `triggers` pass | All green |
| E.2 .deb package | `dpkg -i` installs, icon in launcher | Pop!_OS verification |
| E.3 Recovery tests | Crash during recording, model failure, partial download | No data loss |
| E.4 Owner sign-off | Real mic/audio/UI + native packaging | Documented approval |

---

## Acceptance criteria per milestone

Each milestone follows the five-state tracking system:

| State | Meaning |
|-------|---------|
| PLANNED | No verified artifact |
| DRAFT | Source proposal exists |
| IMPLEMENTED | Files exist |
| UNIT_PASS | Isolated tests ran |
| INTEGRATED_PASS | Actual service/interface tested |
| OWNER_VERIFIED | Physical UI/audio/product test passed |
| RELEASE_READY | All gates pass; owner signed off |
| BLOCKED | External hard gate |

> **No milestone is DONE** until: code exists on intended branch, non-destructive tests ran, negative tests passed where security-relevant, owner behavior verified when necessary, and rollback path tested.

---

## Blockers and risks

| Blocker | Impact | Mitigation |
|---------|--------|------------|
| GTK crash root cause UNKNOWN | Blocks P0 stability | Document, attempt reproduction; if unreproducible, defer with tracking issue |
| Open WebUI compatibility unverified | Blocks M2 UI architecture choice | Run short spike; document ADR |
| Real Jan endpoint availability uncertain | Blocks M1/M2 P1 vertical slice | Check before starting; if unavailable, mock with bounded timeout test |
| PR #77 historical failure | Contradicts "all green" claims | Audit CI logs before asserting status |
| No automerge policy | Slower iteration | Manual merge after green CI + review |

---

## Timeline (estimate)

| Phase | Effort | Dependencies |
|-------|--------|-------------|
| P0 Audit & naming | 1–2 days | None |
| P1 Vertical slice | 3–5 days | P0 baseline, Jan available |
| P2 Research | 3–5 days | P1, browser adapter |
| P3 Desktop/OpenCode | 3–5 days | P1, policy engine |
| O Packaging & QA | 2–3 days | All phases |

---

## References

- `docs/PRODUCT_SCOPE.md` — full product scope and requirements matrix
- `docs/CHATGPT_HANDOFF.md` — handoff prompt for ChatGPT
- `/home/horseman/tihulu_jarvis/TIHULU_JARVIS_OPENCode_PACKAGE/11_ROADMAP_AND_TASK_GRAPH.md` — original task graph
- `/home/horseman/tihulu_jarvis/TIHULU_JARVIS_OPENCode_PACKAGE/12_TEST_AND_ACCEPTANCE.md` — acceptance test matrix
