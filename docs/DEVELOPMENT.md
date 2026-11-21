# TIHULU JARVIS development snapshot
A Python 3.10+ package containing the original multilingual core, a local Jan chat adapter and an opt-in GTK 4 desktop client. This snapshot is intended for review, not a verified installable release.

## Development use
From this source tree use `PYTHONPATH=src python3 -m unittest discover -s tests -v`. On Linux with GTK 4 and the system Python bindings available, `PYTHONPATH=src python3 -m tihulu_jarvis.desktop` launches the candidate UI. No configuration or service changes are made on launch. No network listener, telemetry or background microphone is provided. The legacy application remains independent.

In **Installed models**, enter a trusted numeric-loopback Jan HTTP endpoint with an explicit port and select **Discover**. Pick an actual returned model. No hardcoded port, proxy, credentials, redirects or cloud fallback. Text chat works independently of voice models.

For the optional local voice route, select a compatible whisper.cpp GGML .bin and a Piper .onnx voice with its matching .onnx.json configuration for each desired language. These are references to trusted existing files. The native audio route requires compatible arecord, whisper-cli, piper and aplay on PATH. It makes no assumptions about Hex file or GUI compatibility and never downloads or bundles weights. Talk explicitly starts microphone capture. Finish recording ends it, or it ends after 30 seconds. Stop speaking interrupts only the current reply. Cancel invalidates the current turn; End also clears conversation memory. Hard stop cancels all work owned by this single-turn client. Model outputs cannot invoke programs. GTK, physical audio quality, installed backend compatibility and real Jan behavior require Linux acceptance.

Only local model choices are stored in the XDG config directory, with a private settings file. History stays in memory. Temporary audio is cleaned after each attempt. Removing the source snapshot or its isolated installation leaves existing services, models and the legacy GUI alone. No live service or shared model migration is part of this change.

## Scope and outstanding gates
The local-only path is a development feature, not proof of OS-wide egress enforcement. A verified private execution bridge, remote providers, privileged automation, complete marketplace downloads, full profile enforcement, authenticated remote access, release packaging and clean-machine rollback remain separate gates. Never enable them based only on this source or a passing fixture.

Project source: AGPL-3.0-only; see LICENSE. Third-party engines, models and voices retain their own terms and are not shipped. The public CI job uses read-only contents permission and synthetic data. Its loopback fixture is not a live Jan instance.

A catalog may be imported from a local file or fetched over HTTPS. A fetch is
scoped by an owner-managed allowlist in the local settings, uses HTTPS with
default system certificate verification, sends no credentials, follows no
redirect and is bounded to the catalog size limit. An unverified catalog may be
browsed and inspected; only a catalog verified against an owner-managed key can
add entries to the registry, and only with the owner's separate download
approval. Tests inject the transport, so no test opens a socket.

Policy decisions are audited locally: every tool-rule and endpoint decision made
by the client is appended to a bounded, append-only log in the profile store.
Entries record time, profile, action, scope, decision and reason only — no
secret, path or transcript. The audit view is on the Profiles page and updates
after each decision. A partial or unreadable log is a bounded reason, never a
silent gap; it is never deleted by the client.

Switching profiles cannot silently add privileges. The client compares the
privileges the current profile grants with those the target would grant —
cloud reasoning, sending content to a provider, running local programs,
reaching other hosts, working while idle, listening for a wake word — by
token, never by name, so renaming a profile cannot hide an escalation. When the
target adds any privilege, a trusted-UI confirmation lists exactly what would
be added and requires an explicit Confirm; cancelling changes nothing. Without
an escalation the switch applies directly. A spoken or typed command is never a
privileged approval; there is no other path.

A recording supplied by the owner can be transcribed without the microphone:
*Transcribe a recording* in *Installed models* picks a WAV file, checks it with
the same signal test used for captures, runs the selected Whisper model and
appends the transcript to the conversation. The speech engine is injected, so
tests exercise the validation, bounds and error reasons without a model. A
missing, silent or unsupported recording is refused before the engine runs.
