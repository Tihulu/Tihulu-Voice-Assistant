# Real WAV acceptance (no microphone)

Separate CPU-only CI from deterministic fixture CI and physical acceptance. The real-engine job builds whisper.cpp at 4979e04f5dcaccb36057e059bbaed8a2f5288315 (v1.8.2), uses a digest-verified multilingual Base model and Piper 1.8.0 with digest-verified Lessac (EN), DFKI (TR) and Thorsten (DE) weights. EN/TR sidecars use pinned revisions, DE uses the v1.0.0 tag; all sidecar hashes are logged. Exact PR head is checked out explicitly. Package dependencies and Ubuntu runner versions are recorded but not fully locked. No user recordings, cloud model or credentials; no hardware required.

Twenty-two cases: public upstream JFK speech (WER <= 25%); independently synthesized eSpeak EN/DE/TR (WER <= 50% each), both automatic and explicitly selected input language (three cases each); mixed TR/EN/DE and speech after 32 seconds of silence (WER <= 50% each); silence must stop before inference; three virtual-microphone cases using actual arecord and ALSA file PCM with known raw samples padded to 30 seconds; real Piper + ALSA null playback in all three languages; three Piper-to-Whisper roundtrips (WER <= 50% each). WER is word edit distance / reference word count with case/punctuation normalization, not a language-quality benchmark. Thresholds are initial explicit smoke criteria, not tuned after results. Natural human speech for TR/DE is not yet covered. Base CPU is not the owner's Large v3 Turbo GPU setup.

Production audio.transcribe and audio.speak are used, with actual subprocesses and inherited descriptors. The observer copies only the generated test WAV at playback; ALSA null is not physical sound. Virtual capture uses an explicit per-process ALSA configuration, never a physical microphone or system config. A null-backed source can complete before listening publication; this does not verify physical capture timing. No Jan test, no hardware acceptance, no release. A failure or skipped stage is not PASS. CI logs include public/synthetic texts, timing and SHA-256.

With local fixture engines and verified models, run:
```sh
PYTHONPATH=src python3 tools/voice_wav_acceptance.py --model /absolute/ggml-base.bin --voice /absolute/en_US-lessac-low.onnx --voice-tr /absolute/tr_TR-dfki-medium.onnx --voice-de /absolute/de_DE-thorsten-low.onnx --human-wav /absolute/jfk.wav --report /tmp/voice-report.json
```

The user can explicitly choose Auto/TR/EN/DE input recognition in the model page for the current window; it is separate from response language. Auto remains the default. No silent language fallback. Forced-language PASS cannot clear an automatic-language FAIL.

Experimental segmented mode partitions at quiet gaps (>=600ms), keeps every PCM sample, processes each part with automatic detection and one shared 120-second transcription deadline. Maximum 60 seconds and eight segments, explicit refusal above bounds. This is not continuous code-switch recognition; the original mixed-auto test remains required. Added separate segmented mixed case.

## Focused model comparison

The separate CPU comparison matrix uses the same six eSpeak WAV cases on Base, Small and Large v3 Turbo Q5_0, four threads, identical pinned whisper.cpp v1.8.2 source and independently checked SHA-256 weights from the upstream Hugging Face model pointers. Verify identical WAV hashes before comparing WER; runner timings are indicative, not a controlled hardware benchmark. Auto and segmented remain separate, explicit TR is not an automatic rescue. All six use the unchanged 50% smoke threshold. No production model is switched or downloaded automatically. The quantized Turbo test is not the full-precision owner model or GPU acceptance.

Mixed acceptance additionally requires >=50% exact in-order reference-word recall in every language block (LCS over the complete reference, no reused matches). Aggregate WER can conceal total loss of Turkish. The original WER threshold remains, and this is a stricter smoke criterion, not a waiver. Two further mixed cases concatenate actual per-locale Piper roundtrip WAVs, auto and segmented; output hashes remain recorded because Piper synthesis can vary. Comparison workflow triggers on shared fixture changes.

## Full model acceptance

#274 comparison demonstrated Small on identical eSpeak fixtures: 25% TR WER vs Base 212.5%; mixed segmented 5.41% WER and TR/EN/DE recall 75/100/100%. Turbo Q5_0 exceeded the unchanged 120s transcription deadline in segmented CPU mode, and mixed-auto TR recall was 12.5%. Neither model is fully accepted by that six-case comparison. Run all 22 real cases on Base and Small in separate fail-fast-disabled jobs. Base remains a negative control; no relaxed thresholds, no switch of user-selected model, no change to operation deadline. Workflow-level timeout allows all cases to finish, not a longer per-turn budget. Piper output can vary between jobs, so compare eSpeak hashes separately and do not claim identical synthesized Piper waveforms.
