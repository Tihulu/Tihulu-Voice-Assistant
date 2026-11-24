# R1 maintainer finding: history route binding

Frozen candidate #89: 20d3092678aaa1de57cf485008180c9b7438b481. This is a maintainer remediation proposal, not an independent verdict.

## Finding HR-01 — cross-route history replay

The candidate's Session.reply reads self.history and forwards it to the supplied backend on each turn. Desktop.activate_profile clears pending attachments and local analysis artifacts but not Session.history; typed and voice calls construct a Jan backend from the current profile, model and endpoint. A previously submitted text attachment remains in history. Changing profile/endpoint/model and continuing chat can therefore replay that content to a different inference route without a new file-selection grant. The finding is about cross-route replay, not a demonstrated cloud upload.

Reproduction performed locally with synthetic content and two fake backends: the original session.py, m1.py and jan.py from the supplied source archive matched the frozen GitHub blob hashes. First reply included a synthetic attachment canary; second unrelated reply to a different supplied backend contained the canary. Explicit invalidate(clear=True) prevented replay. No actual user data, network or device accessed.

## Proposed remediation

Session.require_route binds successful conversation history to a trusted caller's route identity. Typed/voice desktop turns supply profile ID, version, mode, effective endpoint and model. A mismatch refuses before invoking the completion backend; the user must explicitly end the conversation or restore the original route. Same-route follow-ups are preserved. Cancel without clear does not bypass the guard. An explicit end clears route and history. Existing standalone unbound Session callers are not a desktop route.

Local targeted reproduction on the patched session blob (Git blob SHA 12b319244bfeaa69b37f5da477bc75118f0d53e6 verified) passed: same-route context, denial for each of five changed components, missing binding denial, cancellation-only denial and explicit clear without prior canary. Five new core and two GTK tests are committed; exact-head CI remains required.

This guard does not establish endpoint authenticity, prevent upstream forwarding at an unchanged endpoint, guarantee same-ID profile files cannot be externally tampered with, erase already-disclosed bytes, or implement the future secure hybrid gateway. It is one scoped defense. Review full profile/route/history behavior independently. #89 stays frozen and must not be merged or used for private-data acceptance while HR-01 is unresolved. Do not merge this remediation automatically.
