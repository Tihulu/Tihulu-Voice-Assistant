# Real WAV acceptance (no microphone)

Separate CPU-only CI from deterministic fixture CI and physical acceptance. The real-engine job builds whisper.cpp at 4979e04f5dcaccb36057e059bbaed8a2f5288315 (v1.8.2), uses a digest-verified multilingual Base model and Piper 1.8.0 with digest-verified Lessac (EN), DFKI (TR) and Thorsten (DE) weights. EN/TR sidecars use pinned revisions, DE uses the v1.0.0 tag; all sidecar hashes are logged. Exact PR head is checked out explicitly. Package dependencies and Ubuntu runner versions are recorded but not fully locked. No user recordings, cloud model or credentials; no hardware required.

Thirteen cases: public upstream JFK speech (WER <= 25%); independently synthesized eSpeak EN/DE/TR (WER <= 50% each); mixed TR/EN/DE and speech after 32 seconds of silence (WER <= 50% each); silence must stop before inference; real Piper + ALSA null playback in all three languages; three Piper-to-Whisper roundtrips (WER <= 50% each). WER is word edit distance / reference word count with case/punctuation normalization, not a language-quality benchmark. Thresholds are initial explicit smoke criteria, not tuned after results. Natural human speech for TR/DE is not yet covered. Base CPU is not the owner's Large v3 Turbo GPU setup.

Production audio.transcribe and audio.speak are used, with actual subprocesses and inherited descriptors. The observer copies only the generated test WAV at playback; ALSA null is not physical sound. No Jan or microphone test, no hardware acceptance, no release. A failure or skipped stage is not PASS. CI logs include public/synthetic texts, timing and SHA-256.

With local fixture engines and verified models, run:
```sh
PYTHONPATH=src python3 tools/voice_wav_acceptance.py --model /absolute/ggml-base.bin --voice /absolute/en_US-lessac-low.onnx --voice-tr /absolute/tr_TR-dfki-medium.onnx --voice-de /absolute/de_DE-thorsten-low.onnx --human-wav /absolute/jfk.wav --report /tmp/voice-report.json
```
