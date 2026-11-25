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

## Composer and contextual-result increment
Move Talk beside Send/Attach, keep inactive finish/stop/cancel/duplicate hard-stop controls hidden, retain permanent header Hard Stop and keyboard routes. Make optional voice setup secondary to text-model selection. Put the explicit local CSV tool under a collapsed Local tools panel; keep the existing last-result controls in a clearly labelled adjacent chat result area only while a validated result exists. Hard stop removes that area with the result. This is not yet per-artifact action rendering or complete agent task execution; no change to grants, export destinations, artifact lineage, worker cancellation or model requests. Verify real widgets, visibility and existing analysis/cancellation regressions before preview.

## Connection readiness and independent voice preview
Invalidate the actionable inventory before an explicit rediscovery, including market discovery. Saved model preference remains but is restored only on a matching successful inventory; failure cannot leave a previously listed model enabled. Show distinct discovered/selected states and missing individual audio executables or selected audio files. Fixed voice samples do not require an LLM; existing audio policy, lease and checkpoint still apply. Detection is not proof of model compatibility or physical device operation. No automatic scan of private folders, installs, model downloads or endpoint guessing.

## Explicit audio model folder discovery
Owner-selected native folder picker; local-only, selected-folder read authorization, live policy rechecks, owned worker and cancel/drain. Scan at most 512 entries, 64 candidates and two descendant levels, no symlink descent, no model contents or sidecar reads. List nonempty regular .bin and .onnx suffix candidates only, no compatibility claim. Select a candidate and explicit STT or voice locale, then review it in the existing trusted native model picker; discovery alone does not register, persist, hash, load or activate weights. Clear results on stop/profile change, reject stale context. No automatic whole-home scan or native LLM engine support. Slow filesystem operations are not a hard real-time deadline.

## Owned task lifecycle and offline recording tool
Task cancellation is a request, not a terminal event: keep the active board row and resource leases until the owned coroutine drains, retain the explicit stop reason, classify invalidated work as cancelled, freeze final elapsed time and clear released leases. Label discovery, preview, probe, voice, transcription and transfer operations at submission rather than carrying over the preceding chat prompt. Existing task board remains single-operation; no parallel or remote agent capability is claimed. Expose existing local WAV transcription in the collapsed chat tools without requiring Jan, with model-file and whisper-cli readiness gates, explicit native picker, existing live filesystem checks and bounded private snapshot. GTK tests exercise synthetic transcription and owned cancellation; actual Whisper performance and native hardware remain unaccepted. R2–R5 holds unchanged.

## Typed local picker commands
Exact TR/EN/DE owner-composer requests for local recording transcription and audio model discovery use the same native owned picker as their explicit buttons. No Jan request, model-generated path or shell action, no file read before selection. Preserve draft on refusal, refuse attachment/picker conflicts and non-local mode, check policy at picker entry and again on actual execution. This adds a bounded command surface, not general natural-language agents or new model engines.

## Local file boundary hardening
Directory listing scans at most 1024 entries and displays at most 200 regular files/directories, never following links. Descriptor-relative no-follow traversal, nonblocking file open, bounded reads, before/after file identity, selected root identity and live authorization checkpoints protect previews from stale selections and replacement. Clear old listing/preview on changed root, failed listing, profile activation or Hard Stop. This is a synchronous bounded metadata/text operation; slow filesystem I/O can still block and no new chat/agent file access is granted. Hard links and mount boundaries are not a sandbox; owner-selected directories must be trusted.

## Chat-local folder research
The same hardened files engine is now accessible from explicit TR/EN/DE composer requests and a local tools button. A native owner picker initiates an owned worker, with live scoped authorization and cancellation/drain. A bounded latest-result panel shows directory metadata, an explicit file selector and bounded local text preview. No file contents or names enter model messages/history; no persistence of the chat folder selection, recursive search, write or agent access. Clear result on profile change, Hard Stop, end conversation or explicit clear; invalidate revoked scope at refresh. Existing secondary research page remains available and synchronous. No hard realtime cancellation guarantee for blocked OS calls.

## Local selected-file search
Explicit literal case-insensitive search of one selected file uses the same pinned root and entry identity, 256 KiB UTF-8 read limit and live folder/file grants as preview. Count matching lines across the entire bounded file; show at most 30 snippets of at most 240 source characters with line numbers, no regex input, no automatic folder-wide file-content access, no model/history or persistence. Clear stale search output on query/selection/grant change, Hard Stop or profile change. Existing blocked-I/O cancellation limitations apply.
