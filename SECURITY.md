# Security and data handling

This is an early single-user, self-hosted application. It binds to loopback by default. Do not expose its HTTP port directly to the internet without a separately reviewed access-control and TLS deployment.

Provider keys belong on the server in private secret files, never in browser URLs or Git. Browser access uses short-lived, single-use login links and scoped audio upload tickets. Anyone with a valid workspace access token can access that single user's workspace; this is not tenant isolation.

Please report suspected vulnerabilities privately through the repository's GitHub security reporting channel if enabled, or contact the maintainer privately. Do not include live tokens, recordings, or personal transcripts in public issues. If a key has been disclosed, rotate it at its provider and replace the local secret.

Use recording with participants' awareness and according to the requirements of your setting. The operator is responsible for retention, backups, and access to recordings. See `docs/architecture.md` for which data is sent to which component.
