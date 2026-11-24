# R1 analysis controller

This is the next independent R1 increment, stacked on PR #82. The trusted UI activates a Context (session ID, profile ID, version and mode), owns the live authorization predicate and supplies an explicit picker path. Only LOCAL_ONLY is accepted. The controller calls the selected CSV reader and deterministic temperature engine; it returns an opaque artifact ID. A local UI obtains the immutable series, chart, source digest, chart digest, engine version, task ID, parent ID and deterministic English/Turkish/German summary through get(). Recoloring creates a new child without rereading the original source. There is no LLM, shell, network, storage, audio playback or GTK wiring in this increment.

## Integration contract
- The authorization predicate must check active profile state, exact session/task/selection grants and current mode, return literal True, be nonblocking and not call a model or reenter the controller. No generic fallback allow. The picker alone supplies the path. Selected CSV analysis grant includes local computation/publication, not disclosure or export.
- Artifact read and recolor are separate scoped permissions; read is also required throughout recoloring. The UI must recheck the current context and read authorization on receipt of asynchronous events, not display cached worker results. Never give the full Artifact, private source path, raw points or SVG to an LLM.
- Single active job; cancellation and context invalidation are serialized with internal publication. A cancelled worker must drain before another begins. Hard stop clears the local controller context/results; activate again explicitly. The UI owns external grants, their revocation and synchronization with context transitions. A callback is not an OS sandbox, and previously delivered bytes cannot be recalled by revocation.
- At most 16 artifacts per activation, no silent eviction, no persistence. New context clears them; source file is not modified. User export and longer-lived retention are separate future capabilities.
- Tests cover the exact synthetic fixture, four-turn lineage, language summaries, scope/mode denial, permission failure, cancellation, context switch during work, parent read revocation, bounded store and source immutability.

## Remaining gates
Verify exact-head CI. Then integrate the actual GTK picker/controller with an explicit task grant, main-thread display, existing task board, no generic attachment prompt injection, and the voice session. Real microphone/Jan/Piper and COSMIC picker acceptance remain HOLD. No automatic merge.
