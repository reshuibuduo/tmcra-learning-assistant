# Contributing

Keep changes small and add a regression test. Run the README test commands before opening a pull request.

- Preserve manual microphone start/stop semantics.
- Keep capture storage independent of ASR and reasoning availability.
- Never commit API keys, real recordings, transcripts, private learning profiles, runtime logs, or deployment credentials.
- Use synthetic text/PCM fixtures; label real-provider experiments separately from mocked tests.
- Keep citations tied to original evidence. Do not silently accept fabricated references.
- Changes to recording, storage, or schema need a data-preserving upgrade/rollback explanation.
- Avoid unrelated rewrites. Include the measured latency boundary when reporting performance changes.

Contributions are provided under the repository's Apache-2.0 license unless explicitly stated otherwise.
