# Real WAV acceptance (no microphone)

Separate CPU-only CI from deterministic fixture CI and physical acceptance. The real-engine job builds whisper.cpp at 4979e04f5dcaccb36057e059bbaed8a2f5288315 (v1.8.2), uses a digest-verified multilingual Base model and Piper 1.8.0 with digest-verified Lessac (EN), DFKI (TR) and Thorsten (DE) weights. EN/TR sidecars use pinned revisions, DE uses the v1.0.0 tag; all sidecar hashes are logged. Exact PR head is checked out explicitly. Package dependencies and Ubuntu runner versions are recorded but not fully locked. No user recordings, cloud model or credentials; no hardware required.

Twenty cases: public upstream JFK speech (WER <= 25%); independently synthesized eSpeak EN/DE/TR (WER <= 50% each), both automatic and explicitly selected input language (three cases each); mixed TR/EN/DE and speech after 32 seconds of silence (WER <= 50% each); silence must stop before inference; three virtual-microphone cases using actual arecord and ALSA file PCM with known raw samples padded to 30 seconds; real Piper + ALSA null playback in all three languages; three Piper-to-Whisper roundtrips (WER <= 50% each). WER is word edit distance / reference word count with case/punctuation normalization, not a language-quality benchmark. Thresholds are initial explicit smoke criteria, not tuned after results. Natural human speech for TR/DE is not yet covered. Base CPU is not the owner's Large v3 Turbo GPU setup.

Production audio.transcribe and audio.speak are used, with actual subprocesses and inherited descriptors. The observer copies only the generated test WAV at playback; ALSA null is not physical sound. Virtual capture uses an explicit per-process ALSA configuration, never a physical microphone or system config. A null-backed source can complete before listening publication; this does not verify physical capture timing. No Jan test, no hardware acceptance, no release. A failure or skipped stage is not PASS. CI logs include public/synthetic texts, timing and SHA-256.

With local fixture engines and verified models, run:
```sh
PYTHONPATH=src python3 tools/voice_wav_acceptance.py --model /absolute/ggml-base.bin --voice /absolute/en_US-lessac-low.onnx --voice-tr /absolute/tr_TR-dfki-medium.onnx --voice-de /absolute/de_DE-thorsten-low.onnx --human-wav /absolute/jfk.wav --report /tmp/voice-report.json
```

The user can explicitly choose Auto/TR/EN/DE input recognition in the model page for the current window; it is separate from response language. Auto remains the default. No silent language fallback. Forced-language PASS cannot clear an automatic-language FAIL.

Experimental segmented mode partitions at quiet gaps (>=600ms), keeps every PCM sample, processes each part with automatic detection and one shared 120-second transcription deadline. Maximum 60 seconds and eight segments, explicit refusal above bounds. This is not continuous code-switch recognition; the original mixed-auto test remains required. Added separate segmented mixed case.
