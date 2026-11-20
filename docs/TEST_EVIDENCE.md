# Core and voice candidate: executed tests

Isolated CPython 3.12.7, Pyodide 0.27.7 (Emscripten). The four unmodified baseline files were loaded first; migrated modules and new tests were run separately from the source archive. Ordinary cases used unittest TestResult; async assertions and lifecycle ran on the existing browser event loop. No native program, device or model was invoked.

| Group | Passed | Failed/errors | Not run |
| --- | ---: | ---: | ---: |
| Original M1 and contracts | 21 + 8 | 0 | 0 |
| Migrated M1 and contracts | 21 + 8 | 0 | 0 |
| Jan adapter | 4 | 0 | 1 |
| Session, synthetic audio and local choices | 5 | 0 | 0 |

Candidate total: 38 passed of 39; JanTests.test_fixture_chat_over_loopback requires a native TCP socket and was not run in WASM. All 13 Python source/test files compiled. Do not sum the original and migrated baseline groups. The source archive SHA-256 is 7c958c2e4263dc9d535591ce488b6197a9a9bc1c04f269808e21fbcabe3c8da3. Its 21 file payloads matched the add-only patch exactly. This branch adds the evidence document and updates the status. Standard unittest discovery on the Ubuntu CI runner, real Jan, mic, output, COSMIC and clean-system install/rollback remain unverified.
