# Changelog

## 0.1.0 — 2026-11-08

- Initial standalone open-source package for the learning/meeting assistant.
- Manual recording, durable browser outbox, offset acknowledgements and resumable ASR.
- Rolling-window subtitles, vocabulary hints, concept explanations and streaming selection questions.
- Optional TMCRA memory integration; default memory reads/writes disabled.
- Portable initialization, loopback launcher, one-time login command, documentation and CI.
- macOS floating-window source and offline state tests.

Known limits: early single-user release; Python 3.13+ unsupported; browser closure/sleep interrupts audio input; anonymous speaker clusters are connection-local; real long-session and multi-speaker acceptance remains environment-specific.
