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

Model routing is explicit. A route names the engine that serves a task
(STT/LLM/TTS), whether it runs on this computer or at a provider, and whether
the active mode permits it: LOCAL_ONLY never routes to a provider, a cloud
route requires an approved provider, and an unavailable local route is a
bounded reason rather than a silent transition to the cloud. The *Installed
models* page states the effective route for the selected model and the mode's
limit, so a capability is never implied.

Exclusive hardware is leased, not assumed. The microphone and audio playback
are exclusive: a voice turn holds the microphone for the capture only and the
playback lease for the reply only, so a capture never keeps the input busy
after it ends and two turns cannot share one device. A turn that cannot get the
lease it needs is refused before the device is touched — `resource_busy:…` is a
bounded reason, never an attempt that fails later, and never a silent retry
against a second device. The lease is always released, including when the turn
is cancelled or fails.

The privacy boundary is stated before content is written. The client derives
it from the active profile's granted privileges, its approved providers and the
kind of endpoint configured — never from a mode name alone, and never from a
guess about a later change. The composer shows one line: content stays on this
computer; this mode permits cloud reasoning with approved providers (named),
but this build only transports to a loopback endpoint; or the configured
endpoint is not a loopback address and this build refuses it. This build's only
transport is a numeric loopback endpoint, so no configuration sends content off
the machine — that is stated as a fact, not left to be inferred. Stating it is
recorded in the audit trail with the boundary tokens and no message content.

State is stated in words, never in colour alone. A row under the status line
reads the live condition: local or cloud permitted, microphone idle, listening
or transcribing, privileged tools may run, approval pending, no local model.
The tokens come from the real stage and the real configuration — never from an
animation and never from a colour. The row is refreshed while a turn runs, so
"microphone listening" is visible exactly when a disabled control would
otherwise hide it, and it is refreshed when a confirmation appears or closes.
Controls carry screen-reader names where the GTK build can set them; where it
cannot, the control is left untouched and the same words remain readable in the
row.

Searching the conversation stays local and bounded. The field under the chat
log searches the session's own messages for a plain substring — never a pattern,
so a search cannot become an expression engine — casefolded with combining
marks dropped, so "kelimem" matches "KELİMEM" without depending on the platform
locale. Results are snippets with a bounded window around the first match and
are listed newest first; selecting one names its position and role in the
detail line instead of rewriting the conversation. The field is disabled while
a turn runs, so a search can never race a reply, and a query that is empty or
longer than 120 characters is refused with a bounded reason rather than
silently returning everything.

An attachment is read once, behind the active profile's filesystem policy, and
bounded twice: by size on disk (256 KiB) and by characters in the message
(60000). The model sees the file by *name* only — the directory it came from
never enters the transcript — inside a block that states its own boundaries so
nothing can be read past it. A binary file is refused before it is decoded, an
empty file after, and an attachment is sent once and then cleared. The policy
layer's rule is unchanged and stated plainly here: an absent rule is not a
forbiddance, so attaching is permitted unless the profile denies it; an
explicit deny is recorded in the audit trail and never opens a file picker.

The Models & Routing page states every task's route, not just reasoning's. One
row per task — speech to text, reasoning, speech — names the model on this
computer, or the bounded reason there is no route: no route configured, a local
mode refusing a cloud route, or an approved provider being required. The rows
come from the same `routing.plan` the policy layer uses, so a capability is
never implied by a layout. A fallback is not silently available either: this
build has one local route per task and says `no_fallback_configured`.

The audit view filters what the log already holds. The action box is filled from
the decisions the log contains — never from a name it cannot show — and the
decision box offers every outcome the client writes: allow, deny, shown. A row
is time, outcome, action, scope and reason; nothing else, and no raw log. An
empty log and a filter that matches nothing are different facts and are stated
as such. A filter survives new decisions: adding an allowed decision does not
silently widen a denied view.

A typed reply is shown as the server sends it. The client asks the Jan
endpoint for a server-sent stream, reads only `data:` fields — comments and
other fields are ignored — and appends each piece to the same message label, in
order. Nothing is displayed that the transport did not actually receive, and a
piece that arrives after the turn was invalidated is dropped rather than shown.
The stream is bounded by events and by characters: a runaway server cannot hold
a turn open or grow a reply without end, and the joined answer is validated
exactly like a whole one. A spoken reply never streams, because synthesis needs
one whole answer.

The interface is scalable and ships no motion. One stored token — Normal or
Large — is applied to a single stylesheet as soon as it is chosen, so a restart
is never needed and no other part of the look changes. Reduced motion is not a
switch: no scale writes a transition, an animation or a transform, so the
interface has none at any size, and a test keeps that true. A stored scale the
client cannot honour is refused when settings are read, never quietly swapped
for a default, and the style provider is replaced rather than stacked.

Keyboard navigation is a stated table, not a surprise. Five bindings are
declared in `keys` — Ctrl+Return sends a written message, Ctrl+F focuses the
conversation search, Escape cancels a running turn or clears a saved search,
Ctrl+Escape stops everything, Ctrl+O opens the attachment chooser — and the
client records which of them it actually bound, so a test can assert the table
without simulating a keyboard. Escape never does two things at once: it
cancels a turn when one is running and clears a saved search otherwise. The
conversation search rows and the market cards are activatable, so Enter and
Space run the same handler as clicking them. Delivering focus itself depends on
the window manager, which a headless session cannot show; that part is physical
acceptance.

A decision in the conversation is a receipt, never a bubble. Approvals,
cancellations and refusals are appended as their own kind of row — no sender,
no time that could be mistaken for an approval — and each one names the outcome
first: "Approved by you · switching to Cloud Chat adds Cloud reasoning, …",
"Not approved · … no privileges were added", "Refused by the active profile ·
read a file (filesystem_read)". `receipts.is_receipt` is the check that keeps a
message from ever reading as an approval, and the tests assert it both ways.

Approved sites separate two rights. A site is registered with its host and
path, and with an explicit submission right; reading is a GET preview and
submitting is a POST that only a registered, submission-approved target allows.
A private or local address is not a web target, a bare domain is not a target
either, and each refusal names its own reason. A stored target is re-validated
when settings are read, and its submission right is stored with it rather than
inferred from the URL a second time. A submission without the right is refused
in words and recorded as a receipt. Browsing itself — rendering, scripts,
screenshots — is not part of this increment: this is the policy and the
registry behind it.

The Activity page lists the work this client runs, newest first. A task names
its kind (a turn, a download, a transcription, a catalog fetch), its label, its
state — queued, running, done, cancelled or failed — the reason it ended with,
the elapsed time and the resource leases it holds. Progress is never guessed: a
turn has no percentage and none is shown, a download counts bytes. A running
turn is visible while it runs, not only after it ends, and the selected running
task can be stopped, which cancels the turn through the real cancellation path
and records the reason. This is the client's own work; an OpenCode project and
session view is not part of this build and is not implied by the page.

The text that will be spoken can be shown, when the owner asks for it. A voice
turn's answer is the full text; the voice layer separately normalises it into
the text Piper will actually read, and until now that normalisation was
discarded. With *Show the text that will be spoken* on, the exact spoken text
appears under the answer as its own row, stored as a preference and honoured at
the next start. This build has no second, shorter answer: a display text that
cannot be spoken is refused, never silently shortened, so the spoken line is
the same content in speakable form rather than a summary of it. A typed reply
never shows a spoken line, because nothing is spoken.

Files & Research reads a folder the owner chose, behind the filesystem policy.
The folder is chosen once and stored; listing it is bounded to 200 entries and
sorted folders first, and a preview is bounded text — 8000 characters — with a
binary file refused before it is decoded and a long file cut with the cut
stated. A name that leaves the chosen root, by traversal, by being absolute or
by following a symlink out of it, is refused before anything is opened, and
nothing is ever written. A folder or file the profile denies is a bounded reason
and a receipt, and the stored folder is validated again when settings are read,
so a path that has since disappeared is reported instead of silently listing
nothing.

The Installed models page states what this machine can do, before a turn fails.
One line per item — Jan endpoint, local models, speech to text, voices, audio
tools, active profile — each either ready, missing with the precise reason, or
not checked yet. Nothing is probed that would touch the network or a device: an
undiscovered model list is *unknown*, not missing, because nobody has looked,
and absent audio tools are stated instead of leaving a Talk button enabled that
would fail later. The row uses the endpoint the owner is looking at, even before
it is saved, because a readiness statement about a stale value would be a lie
about what the next turn would use.

A search that runs is remembered, locally and bounded. The conversation search
keeps the last queries — at most twenty, no duplicates, the newest first — and
activating one re-runs it, so a repeated question costs one key. Only a query
that actually ran is remembered: one the client refuses to run leaves no memory
behind. Forgetting clears the store rather than hiding the row, the label and
the button disappear with it, and a stored history is re-validated when settings
are read, so a corrupt one is refused instead of loaded as a promise of queries
that cannot be run again.

The topbar names what is active, in words. One line carries the active mode, the
active profile, the selected model and the privacy boundary, and a second fact
when the endpoint in the field is not a numeric loopback address — because that
build cannot use it. An absent choice is said to be absent: no model selected,
no active profile, no mode. The line follows the endpoint field before it is
saved, since a statement about a stale value would describe a turn nobody will
run, and it is refreshed with the profile, the model and the state row rather
than on a timer.

The topbar carries a hard stop, and it is the same stop as the composer's: one
cancellation path, one state, one accessible name. It is disabled while nothing
runs, so a stop that would do nothing is never offered as if it would. The
cancellation itself is unchanged — it invalidates the turn and waits for the
cleanup — and the tests assert that the topbar button, the composer button and
the durability of that path all agree.

The topbar counts the queue from the board itself: how many tasks are running
and which devices they hold, so a busy microphone or output is explained rather
than merely shown as a number. The count follows the leases as they are taken
and released — a voice turn names the microphone while it captures and nothing
after the capture ends — and an idle window says idle rather than zero busy.

Preferences live on the Settings page: the text scale, whether the spoken text
is shown, and *Forget local choices*. Forgetting removes the stored choices
file only and says so on the page: profiles, the audit trail and the market
registry are never touched, and the active profile — a policy, not a preference
— is kept. A file that is already absent is not an error, and the outcome is
stated either way. Nothing on the page reaches a device or a provider; models,
approved sites and profiles have their own pages.
