# Restricted ACP JSON-RPC reference (non-shipping)

Based on ACP v1 initialization, session setup and prompt turn docs reviewed 2026-11-28:
https://agentclientprotocol.com/protocol/v1/initialization
https://agentclientprotocol.com/protocol/v1/session-setup
https://agentclientprotocol.com/protocol/v1/prompt-turn

The Python standard library is the JSON syntax oracle. Duplicate keys (including
escaped equivalents), oversized/deep messages, malformed encoding, nonfinite
and floating-point numbers, batch messages and unknown shapes fail closed.
The restricted subset classifies initialization/session/prompt responses and
agent text chunks only. It returns metadata, not user/model text. All incoming
requests, including permission requests, are refused; no response is sent.
Capability advertisements never grant any feature. Rust lifecycle authority
remains separate, and this reference is not wired into it. No actual transport
or authentication. It is not a comprehensive ACP schema or interoperability test.

The existing core CI requires locked offline builds with zero third-party
dependencies. A Rust JSON decoder must use a reviewed mature library (candidate:
serde_json, MIT OR Apache-2.0) with exact transitive pins/checksums, license and
advisory review, deterministic dependency preparation and both Rust MSRV gates.
Do not substitute a hand-written general JSON parser or disable offline tests.
Compare the native decoder with these reference cases before integration.
