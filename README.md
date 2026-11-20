# TIHULU JARVIS — Voice Assistant

> **Status: pre-alpha / active development.** The repository does **not** yet contain a verified, installable application release. Please do not treat the current documentation as an installation guide.

TIHULU JARVIS is a planned privacy-first, multilingual, multimodal desktop AI assistant, initially targeting **Pop!_OS 24.04 COSMIC/Wayland** and **Ubuntu Linux**, with future Debian and other platform support based on verification.

## Product vision

- Natural voice conversations in **Turkish, English, and German**, with independent STT, LLM, and TTS selection.
- A modern native-feeling desktop interface with rich chat, a real microphone workflow, Talk, Stop Speaking, Cancel Turn, and Hard Stop.
- An **AI Market** for discoverable and safely installable compatible transcription, language, and speech-generation models.
- Four modes: **LOCAL_ONLY**, **CLOUD_CHAT**, **SECURE_HYBRID**, and **CUSTOM**.
- Unlimited saved custom profiles with explicit, scoped, revocable permissions.
- Optional OpenCode agent integration and authorized browser/desktop operations.
- A practical offline-first experience and optional approved cloud providers.

## Safety boundaries

- Cloud models are **coordinators**, not direct local Shell executors.
- Local Shell operations require independently authorized local Jan/Qwen execution components.
- Privileged browser/desktop actions require local policy enforcement.
- Cloud disclosure requires local minimization and appropriate permission; failure must deny access.
- No silent credential uploads, model downloads, background microphone listening, or privileged tool activation.

**These are design requirements, not claims that all security boundaries are already implemented.**

## Current development status

A limited experimental local prototype and separately tested language/TTS contracts have been reported, but the **modern desktop app, integrated voice workflow, AI Market, profile policy engine, and secure hybrid bridge are not yet released**.

The existing legacy tkinter prototype is not the target interface. Migration will be incremental and non-destructive.

## Installation

**No supported installer or release package is available yet.** Do not run unofficial one-line installation commands.

When release acceptance criteria are met, supported builds and checksums will appear on [GitHub Releases](../../releases), with documented prerequisites, model selection, installation, upgrade, and uninstall procedures.

Planned initial targets: Pop!_OS 24.04 and supported Ubuntu versions. CPU fallback and optional GPU inference will be tested separately.

## Development

See [Development roadmap](docs/ROADMAP.md), [Contributing](CONTRIBUTING.md), and [Security](SECURITY.md).

Changes should arrive through small, reviewed pull requests with passing tests. Main is the public baseline; unreleased experiments remain in feature branches.

## License

**License selection is pending.** Public visibility alone does not grant open-source redistribution rights. An explicit `LICENSE` file and review of all third-party model/voice licenses are required before publishing installable releases.

## Disclaimer

JARVIS is a descriptive product direction. This project is not affiliated with Marvel, Disney, OpenAI, or any model provider.
