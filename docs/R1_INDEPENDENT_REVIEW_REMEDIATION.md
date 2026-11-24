# R1 independent review remediation

Independent review in [Issue #90](https://github.com/Tihulu/Tihulu-Voice-Assistant/issues/90#issuecomment-7122694713): overall FAIL, HR-01 scoped PASS, full independent execution and physical acceptance HOLD. Frozen reviewed candidate #91: 05282de55567513228f214ad442db85b47950ba8. This branch is a new remediation candidate; no approval or release is implied.

| Finding | Current disposition |
|---|---|
| Q-01 attachment size mismatch | Proposed fix: validate trusted composer separately; preflight full UTF-8 JSON request including framing, history, model ID and retry instruction against Jan transport limit; preserve draft on budget refusal. |
| Q-02 attachment modifies host language state | Proposed fix: structured attachment argument separate from composer; route_language only sees composer. File content and name are still untrusted model input, not a security boundary against model-level injection. |
| S-01 pre-open file replacement | OPEN: selection identity contract, negative fixture and real portal behavior. |
| S-02 worker expiry and external policy changes | OPEN: live worker authorization, expiry before I/O and publication, bounded revocation. |
| S-03 legacy ASK and expired permission | OPEN: explicit scoped approvals, real-time checks, voice capture and legacy tool audit. |

Eight regression fixtures added: six core and two native GTK. They cover 20k attachment, malicious language instructions in file/name, legitimate composer language override, UTF-8/history/framing budget, denial before inference/retry, native long-attachment send and retained draft on budget refusal. No test results claimed until exact-head CI. Recheck pending attachment authorization immediately before asynchronous invocation and each text retry; unchanged-route accepted history retention remains a separate policy issue. No claim of retroactive deletion, endpoint attestation or generic secure hybrid isolation.

Keep R1 frozen reference and main unchanged. Continue remaining findings in this remediation stream, then rerun exact-head CI and request independent re-review of the full candidate. R2 runtime and physical private-data acceptance remain HOLD.
