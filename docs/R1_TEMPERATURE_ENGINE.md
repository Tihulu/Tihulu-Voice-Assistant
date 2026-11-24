# R1 temperature engine — first deterministic increment

This increment is deliberately smaller than the complete R1 vertical slice. It accepts an already-authorized immutable byte snapshot; it does NOT provide file selection, file authorization, shell access, network access, Jan tool routing, GTK embedding, task persistence, or speech.

- Input: UTF-8 (optional BOM), exact `time_s,temperature_c` header, strictly increasing finite numeric time, bounded 256 KiB and 10,000 rows. Reject malformed input, do not silently discard rows. Binary64 arithmetic, not a general scientific precision engine.
- Output: immutable numeric series, count, arithmetic mean, min/max, last-minus-first delta, duration, SHA-256 of original bytes; static SVG generated from numeric data only. No external SVG links or user-controlled markup. Color selection is enumerated and does not recompute data.
- Cancellation: cooperative checks before and during parsing/rendering; the caller must additionally enforce live authorization and stale-result suppression before publishing.
- Synthetic fixture: (0,20),(10,22),(20,24),(30,26),(40,28). Expected 5, 24, 20, 28, 8, 40. Tests assert exact coordinates and malformed-input rejection. No private data.
- Next: select and read bounded regular files through an authorized descriptor; block symlink/path substitution and truncation; bind snapshot to session/task; adapt typed local tool dispatch and render verified artifact in GTK; then test real Jan, Whisper and Piper, follow-up and hard stop. The analysis core cannot establish filesystem security or owner acceptance alone.

Stacked on R0 CI fix, PR #80. Do not merge either automatically. Record exact-head CI and independent review; owner device acceptance remains HOLD.
