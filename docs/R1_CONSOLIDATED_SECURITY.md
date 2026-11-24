# R1 consolidated security remediation

This successor addresses independent findings CLEANUP-01, POLICY-01,
PRIVACY-01 and AUDIT-01 together. The frozen #110 candidate remains intact.

- Owned worker drains and child reclaim/termination are shielded against
  further caller cancellation. Desktop shutdown first drains session and
  playback owners before cancelling residual tasks. Cleanup may exceed an
  operation deadline; this is not a hard real-time or process-tree guarantee.
- Scope matching and specificity use the same set semantics. Repeated tokens
  and equivalent same-action rules (including reordered tokens) are rejected
  at profile validation; malformed stored/imported rules fail closed. Direct
  evaluation refuses matching duplicate-token rules. Valid narrower rules
  retain precedence and global/mode limits remain unchanged.
- Portable import/export copies clear every model selection (including opaque
  LLM identifiers) and voice mapping. The original local profile is unchanged;
  the owner must explicitly choose installed models on the destination.
  Descriptive text is not a secret scanner; owners must avoid private text
  in profile names and descriptions.
- Audit rows must have the existing six-field schema and native-safe UTF-8
  text (no NUL or unpaired surrogate). Invalid stored rows yield
  audit_unreadable without rewrite; invalid new input yields audit_unavailable.
  JSONL uses LF separators, preserving valid Unicode line separators inside
  JSON strings. No claim of a tamper-proof audit or native hardware acceptance.

The regression suite is synthetic except for disposable harmless subprocess
cleanup probes. Independent review, native acceptance and release gates remain.
