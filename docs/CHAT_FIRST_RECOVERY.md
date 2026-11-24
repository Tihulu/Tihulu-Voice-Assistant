# Chat-first recovery — draft for owner review

Refs #114, #79. Frozen #112 is a reviewed technical baseline, not a usable product release. Preserve its exact source and independent evidence. This successor is an isolated first UX correction, not a new acceptance candidate or a replacement stack. Real Jan, Whisper, Piper, target picker and end-to-end owner acceptance remain open.

## Interaction contract

| Surface | Intended use |
|---|---|
| Main chat | Persistent text/voice composer; inline real text, chart, table, citations, files and task progress. |
| Header | Effective mode/profile, selected model, connection status, approvals and immediately reachable Hard Stop. Never label an untested upstream private. |
| First-run setup | Explicit local endpoint selection, bounded discovery and model choice. Text chat without voice setup. Existing-model discovery, no automatic download or loading. Explain missing or denied prerequisites with a next action. |
| Secondary navigation | Conversations, tasks/activity, models, AI Market/downloads, agents, engines, research, settings and permissions. Existing controls remain available during incremental migration; final grouping subject to owner review. |
| Contextual tools | CSV analysis is only a test engine; selection, result recolor and exports belong beside the artifact, not permanently in the main composer. No arbitrary model HTML/JS. |
| Conversation search | Collapsed until requested, live transient matches, explicit Search/Enter commits bounded local query history, rerun chosen historical query and explicit Forget. |

## This increment
- Replace the horizontal stack switcher with scrollable native side navigation, preserve page names and callbacks.
- Add a first-run action to the existing installed-model page; distinguish empty endpoint from rejected non-loopback endpoint.
- Keep empty discovery local and actionable; no network request for empty URL.
- Collapse search by default and make persistence explicit; preserve existing live-result and clear behavior.
- Test real GTK construction with empty configuration and no model-free chat inference/capture, model-free local analysis readiness and explicit search persistence. Tests use fixture transport and cannot certify actual Jan/audio/picker.

## Next implementation work
1. Explicit connection wizard and real local model/engine discovery with consent, useful failure states, text-only chat using real Jan. No silently chosen endpoint/model.
2. Consolidate composer/voice actions and contextual artifact controls; implement task-centric navigation, rich validated artifact rendering, keyboard/accessibility and owner visual acceptance.
3. Real microphone, STT/LLM/TTS selections, independent languages, cancellation/recovery and physical target acceptance.
4. Retain R2–R5 runtime holds until R0/R1 acceptance or explicit approved roadmap revision; preserve research, native inference, cloud providers, hybrid policy, OpenCode and application-engine scope. Never present disabled placeholders as working features.

No changes to authorization, inference, file I/O or cancellation contracts are intended here. Recheck changed UI boundaries, complete CI and owner review before distributing a successor. No merge, release, installation or acceptance waiver.
