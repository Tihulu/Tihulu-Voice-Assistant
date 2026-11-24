# Native chooser lifecycle remediation

Preliminary maintainer review of the legacy desktop found seven FileChooserNative instances that were local variables only; the parent did not own their lifetime or consistently invalidate callbacks when the profile/session changed. This is a concrete lifecycle and authorization defect, not proof of the cause of the reported COSMIC/Wayland segfault.

The desktop now retains one legacy native chooser and its response handler, sets modal ownership, disconnects the response handler on dismissal, and validates profile ID/version/mode and session epoch before any response callback. Profile activation, hard stop, interruption, new owned work and window close dismiss outstanding legacy dialogs. The R1 analysis chooser does not overlap the legacy chooser. Existing response handlers retain their original disposal behavior.

Legacy text attachment reads now recheck permission at the actual read, and CLOUD_CHAT refuses this route. An unsent attachment is bound to the active profile/version/mode and selected inference route; the send path revalidates that scope. Profile activation, conversation end, hard stop and close clear the pending attachment. Seven native GTK fixture tests cover lifetime, cancellation, stale profile, cloud denial and unsent attachment revocation.

## Limits
- This is not a comprehensive filesystem sandbox, and the legacy text attachment reader's bounded stat/read race still needs separate remediation.
- Existing conversation history and other legacy file actions require the broader mode/policy audit; do not claim CLOUD_CHAT host-tool isolation globally or Secure Hybrid completion from this change.
- GTK under Xvfb does not reproduce the target compositor/portal path. Exact-head CI and a real target reproduction are still required.
- On the owner machine use the extracted preview launcher and disposable XDG state in R1_PREVIEW_GATE.md. Start with no microphone or models running; open/cancel/reopen the model picker and the attachment picker, accept synthetic fixtures, change profile, and close with a picker open. Repeat under the actual Wayland session. If a crash occurs, collect the exact build, GTK/portal versions, minimal steps and a sanitized stack trace; do not publish raw paths, environment, credentials or core dumps. Root cause remains UNKNOWN until supported by evidence.

No automatic merge or stable installation changes.
