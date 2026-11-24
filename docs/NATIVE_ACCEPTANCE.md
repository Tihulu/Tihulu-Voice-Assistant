# Native voice-control acceptance

## Automated layers

The core suite runs with Python 3.10 and 3.12:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The GTK suite is deliberately separate. It requires GTK 4, PyGObject, Xvfb and a session bus and uses the distribution's Python bindings:

```sh
PYTHONPATH=src GDK_BACKEND=x11 GSK_RENDERER=cairo GTK_A11Y=none G_DEBUG=fatal-criticals \
  dbus-run-session -- xvfb-run -a /usr/bin/python3 -m unittest discover -s tests/gtk -v
```

Missing GTK or a display is a failure, not a skip. Tests create actual application windows, run the production GLib/asyncio bridge, emit real widget signals and inspect control sensitivity and visible messages. Temporary settings, metadata-only voice files, capture/transcription, Jan replies and playback are fixtures. No model weights or audio device are required. The unit suite also starts and reaps a Python subprocess to test cancellation during normal use and delayed spawn.

Covered scenarios:
- Idle startup, explicit discovery and text chat independent of voice models.
- Four-turn TR/EN/DE/TR context and independent response-locale voice choice.
- Finish recording, Stop Speaking, Cancel Turn, End Conversation and Hard Stop.
- Repeated cancellation while cleanup is blocked; no new turn before cleanup completes.
- Stale capture progress and late model replies after cancellation.
- Window close during playback, capture error recovery and invalidated inventory.

These results demonstrate widget/controller behavior under X11/Xvfb. They do not certify COSMIC/Wayland input, physical devices or inference quality.

## Physical acceptance: pending

Use an isolated development install and operator-chosen existing models. Record versions and bounded results, not raw conversations, paths, recordings or credentials.

1. Launch on the intended COSMIC desktop. Confirm correct sizing, keyboard focus, scrolling and model selection. Startup must leave the microphone idle.
2. Discover an explicitly configured local Jan endpoint. Verify text-only chat with audio models unset.
3. Select an existing Whisper model and compatible TR/EN/DE Piper voices. In each locale, Talk -> Finish recording -> transcription -> reply -> speech. Confirm the UI remains responsive.
4. During synthesis and playback, press Stop Speaking repeatedly. Audio must stop, cleanup must finish, and the accepted conversation must remain usable.
5. During capture, transcription and inference, press Cancel Turn and Hard Stop repeatedly. Verify capture/output stop and no late result is appended. Previously accepted conversation remains; interrupted unaccepted work does not enter history.
6. Press End Conversation during an active turn. Verify visible and remembered conversation are cleared and a late reply cannot restore either.
7. Close the window during each active phase. Confirm all owned audio processes exit and temporary audio is removed.
8. Run the four-turn memory check: store the word mavi in Turkish, ask for it in English, ask again in German, then Turkish. Check response language, retained word and matching voice.
9. Remove an unrelated selected voice file and repeat a valid locale. It should still work. Remove or mismatch the requested locale's voice: report a bounded reason before chat inference.
10. Disconnect a device or stop Jan during a turn. Verify a bounded error and successful next attempt after recovery.

Record PASS / FAIL / UNKNOWN per scenario with the exact build. Keep hardware/model acceptance distinct from fixture CI. Clean-machine install and rollback require their own packaging gate.

## Pre-flight before touching a microphone

`tools/preflight.py` answers what a checklist asks before a person starts
tapping buttons, and says what is missing rather than leaving a failed attempt
to explain itself. It is read-only: nothing is downloaded, started or recorded.

```sh
PYTHONPATH=src /usr/bin/python3 tools/preflight.py
```

The pre-flight states the interpreter basename and Python version, GTK import,
required audio tool presence, numeric loopback endpoint syntax, a selected chat
model ID, nonempty readable STT file and the TR/EN/DE voice files with bounded
regular sidecars. GPU tooling is optional and never blocks CPU-only readiness.
A required missing or unverified check returns exit code 1. The report uses
fixed categories rather than raw URLs, file paths, tool paths or exception text.
It does not open model weights or parse voice sidecars, launch audio tools,
contact Jan, verify model compatibility or certify physical acceptance. A zero
exit status means metadata prerequisites only; still execute every owner test.
Use the exact reviewed candidate with disposable XDG state and synthetic inputs.
