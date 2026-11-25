# Owner physical acceptance — exact #138

Production SHA: 40dc8f73ad6d5f4daf7cf1afbb3f8b79b687ec12
Tree: 5f844f34524f23a8b0ef59d91fda041e66a4f86d
Independent scoped PASS: issue #116 comment 7170142141. No merge, release or system installation authorized.

## Preparation (ordinary user, trusted local clone, existing build venv)
Run `sh qa/prepare-owner-preview.sh /absolute/path/to/local/repository /absolute/path/to/existing/build-venv` from this evidence checkout. The script builds a detached exact candidate using offline existing build tools, extracts Debian without installing, checks package source against Git and creates disposable XDG state plus synthetic CSV and research files. No application launch. Missing prerequisites are HOLD; do not install or download automatically. Close stable Jarvis normally before launching; preserve real session bus, Wayland and HOME. Run printed runtime check, then printed isolated launch command only after owner approval. No sudo, no stable-state copying, no automatic model download.

## Owner-only evidence (record PASS / FAIL / HOLD / UNKNOWN, actual observed behavior)
P1: native COSMIC/Wayland startup/shutdown, layout/focus/window sizing, picker open/cancel/reopen; prior crash reproduction or UNKNOWN.
P2: verify local Jan configuration does not forward externally; actual discovery/inference, Whisper/Piper TR/EN/DE, mic, stop/repeated hard stop/close, recovery and owned-process cleanup. No credentials or private data. Synthetic prompts/audio where possible.
P3: select generated temperature.csv; request analysis in natural language; expected 5 samples, min 20, max 28, mean 24 Celsius; chart/table/spoken summary, four-turn memory with mavi, recolor, export SVG/PNG/data CSV/stats CSV to private preview folder; selected local research notes, literal search, attachment expiry and profile change refusal; verify no stale callbacks or unapproved requests. Stop at a genuine failure. Keep data and logs local; share only sanitized outcomes and exact SHA.

The generated acceptance.json deliberately starts UNKNOWN and NOT_CLAIMED; this is a helper report, not reviewer certification. The independent verdict is recorded separately in #116. Physical P1/P2/P3 and clean install/uninstall/rollback are distinct gates. No clean installation, release, or R2–R5 activation. Disposable XDG is not a sandbox. Blocking OS I/O, mount/hardlink isolation and #99 transfer concerns remain outside scoped acceptance.
