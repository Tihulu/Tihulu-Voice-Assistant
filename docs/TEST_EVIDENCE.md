# Core and voice candidate: executed tests

Isolated CPython 3.12.7, Pyodide 0.27.7 (Emscripten). The four unmodified baseline files were loaded first; migrated modules and new tests were run separately from the source archive. Ordinary cases used unittest TestResult; async assertions and lifecycle ran on the existing browser event loop. No native program, device or model was invoked.

| Group | Passed | Failed/errors | Not run |
| --- | ---: | ---: | ---: |
| Original M1 and contracts | 21 + 8 | 0 | 0 |
| Migrated M1 and contracts | 21 + 8 | 0 | 0 |
| Jan adapter | 4 | 0 | 1 |
| Session, synthetic audio and local choices | 5 | 0 | 0 |

Candidate total: 38 passed of 39; JanTests.test_fixture_chat_over_loopback requires a native TCP socket and was not run in WASM. All 13 Python source/test files compiled. Do not sum the original and migrated baseline groups. The source archive SHA-256 is 7c958c2e4263dc9d535591ce488b6197a9a9bc1c04f269808e21fbcabe3c8da3. Its 21 file payloads matched the add-only patch exactly. This branch adds the evidence document and updates the status. Standard unittest discovery on the Ubuntu CI runner, real Jan, mic, output, COSMIC and clean-system install/rollback remain unverified.

## Subsequent Ubuntu core verification

The published core commit `20116430dc2caa8426c817838194dedb573102fd` passed standard unittest discovery on Ubuntu 24.04 with Python 3.10 and 3.12: **39 tests per interpreter**, including the native loopback TCP fixture that was unavailable in WASM.

Evidence: [GitHub Actions run 44664461595](https://github.com/Tihulu/Tihulu-Voice-Assistant/actions/runs/44664461595). This supersedes the earlier "no Linux CI pass" statement for that core commit only. It does not establish physical microphone, model or desktop acceptance.

## Native controls candidate

This increment adds six core regression tests and eleven native GTK widget tests. Expected discovery: 45 core cases and 11 GTK cases. These counts describe the candidate; the actual run linked from its draft PR is authoritative. Execution results will be recorded after CI completes.

The GTK job uses Ubuntu's PyGObject/GTK 4 under Xvfb and D-Bus with synthetic Jan/capture/playback. Model metadata files are empty placeholders with synthetic language configurations. The process-lifetime tests use an owned Python child, not a speech engine. See NATIVE_ACCEPTANCE.md for the remaining physical gates.

## Native controls increment: executed results

Observed results for `feat/native-voice-controls` commit `beaac217297f2e725e14255ec017a2ad365e30cc`:

- **Local reproduction before the fix** (Xvfb, D-Bus, system PyGObject/GTK 4): `tests/gtk` failed 10 of 11 with `GError: An object is already exported for the interface org.gtk.Application`, matching CI run 44669442190. This defect was in the new test harness, not in the product controls.
- **After the fix**: `tests/gtk` 11/11 OK; `tests` (core plus cancellation) 45/45 OK on Python 3.12 locally.
- **GitHub CI run 44674465300**: `test (3.10)`, `test (3.12)` and `gtk` all succeeded. The protected suites are discovery-based: 45 core cases per interpreter and 11 native GTK cases. The `gtk` job uses Xvfb, D-Bus, Ubuntu PyGObject/GTK 4, synthetic backends and `G_DEBUG=fatal-criticals`; it fails, and does not skip, when GTK or a display is unavailable.

These runs prove widget and controller behavior under X11/Xvfb with fixtures. They do not establish COSMIC or Wayland input behavior, physical microphone or output quality, model inference quality or clean-system installation; those remain separate gates in NATIVE_ACCEPTANCE.md.

PR: https://github.com/Tihulu/Tihulu-Voice-Assistant/pull/6 (draft, stacked on #4, unmerged).
