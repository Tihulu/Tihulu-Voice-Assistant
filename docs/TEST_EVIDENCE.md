# Core and voice candidate: executed tests

Isolated CPython 3.12.7, Pyodide 0.27.7 (Emscripten). The four unmodified baseline files were loaded first; migrated modules and new tests were run separately from the source archive. Ordinary cases used unittest TestResult; async assertions and lifecycle ran on the existing browser event loop. No native program, device or model was invoked.

| Group | Passed | Failed/errors | Not run |
| --- | ---: | ---: | ---: |
| Original M1 and contracts | 21 + 8 | 0 | 0 |
| Migrated M1 and contracts | 21 + 8 | 0 | 0 |
| Jan adapter | 4 | 0 | 1 |
| Session, synthetic audio and local choices | 5 | 0 | 0 |

Candidate total: 38 passed of 39; JanTests.test_fixture_chat_over_loopback requires a native TCP socket and was not run in WASM. All 13 Python source/test files compiled. Do not sum the original and migrated baseline groups. The source archive SHA-256 is 7c958c2e4263dc9d535591ce488b6197a9a9bc1c04f269808e21fbcabe3c8da3. Its 21 file payloads matched the add-only patch exactly. This branch adds the evidence document and updates the status. Standard unittest discovery on the Ubuntu CI runner, real Jan, mic, output, COSMIC and clean-system install/rollback remain unverified.

## Stack evidence (engineering record)

- Core suite growth: 29 original → 39 core → 64 (native controls + cancellation) → 85
  (policy core) → 97 (market core and settings) tests per interpreter, all green on
  Python 3.10 and 3.12 in GitHub CI.
- Native GTK suite: 11 (desktop controls) → 17 (profiles) → 22 (market) → 31 (discovery,
  preview, storage) → 38 (download flow) cases, all green under Xvfb/D-Bus.
- Package job: wheel content verified (19 entries; every shipped module, LICENSE, entry
  point and desktop metadata), installed into a clean venv, suite 97/97 against the
  installed artefact, imports and console script verified.
- Repository hygiene: 30 tracked `__pycache__/*.pyc` files were removed and a `.gitignore`
  now keeps build artefacts out.

These are fixture-level results. Physical acceptance, real transport and clean-machine
installation remain open; see `docs/NATIVE_ACCEPTANCE.md` and `docs/RELEASE.md`.

## Current stack (main at 29cc369, the PR #19 merge commit)

| Group | Passed | Failed/errors | Not run |
| --- | ---: | ---: | ---: |
| Core suite on `main` | 122 | 0 | 0 |
| Native GTK suite on `main` | 59 | 0 | 0 |
| PR #19 CI `test (3.10)` | green | 0 | 0 |
| PR #19 CI `test (3.12)` | green | 0 | 0 |
| PR #19 CI `gtk` | green | 0 | 0 |

Local recipe: `xvfb-run -a /usr/bin/python3` with `PYTHONPATH=src` (the system interpreter
carries PyGObject and GTK 4; the anaconda interpreter does not), default and `C` locales;
the `tr_TR` locale was unavailable on the verification machine. Real Jan, microphone,
output, COSMIC/Wayland integration and a clean-system install or rollback remain
unverified HOLD gates.

This section supersedes the historical candidate matrix below for the current stack; the
original evidence is kept unchanged.
