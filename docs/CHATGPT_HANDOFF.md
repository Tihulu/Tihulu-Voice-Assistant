# Jarvis — Agent Handoff

Repository: https://github.com/Tihulu/Tihulu-Voice-Assistant
Tracking issue: #79. Planning PR: #78 (plan/recovery-roadmap). Read PRODUCT_SCOPE.md and RECOVERY_ROADMAP.md from the actual planning branch before implementation. The latter governs current decisions and R0–R5 order; retain original requirements and traceability.

## Start here
Verify main, active branches/PRs and exact-head CI. Previously verified main: 20f1349871ad2a6a132975c914f71f8b16b38166. CI run 45290156016 had four passing jobs and failed triggers (missing feat/merge-log-4). GTK file-picker segfault cause UNKNOWN; owner audio/Jan/Wayland/clean-install HOLD. Inspect actual code, tests and installed services with read-only probes. Never publish private data or inventory. Do not assume this snapshot stays current.

Implement R0 first in a small draft PR: stable baseline, CI trigger repair and reproducible crash diagnosis. R1 deterministic selected synthetic CSV -> compute -> chart/table in GTK -> spoken summary -> contextual follow-up -> cancel. Do not expand market or rewrite UI before R1. Reuse existing components. Native inference, connectors, approved OAuth/API-key providers, application engines, four effective modes and CUSTOM saved profiles, session auto-approval and OS-only privileged authentication are specified in the roadmap. No model sees sudo passwords. Standard Hybrid allows scoped cloud proposals through local executor; Secure Hybrid local planner/executor only with mandatory minimized egress. Cloud Chat no host tools; Local Only no cloud inference. No silent permission expansion or cloud fallback.

English repository and default UI; TR/EN/DE localization and independent voice language. No auto-download/install/merge, no main force-push, no private data in public evidence. Use small reviewable commits and draft PRs. Report exact SHA, tests expected/actual, PASS/FAIL/HOLD, owner vs fixture evidence, blockers and next concrete task. Never claim a successful physical test you did not run. If missing a required local service, record HOLD and continue only independent safe work.
