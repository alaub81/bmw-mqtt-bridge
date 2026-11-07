# Security policy

Report vulnerabilities privately using
[GitHub private vulnerability reporting](https://github.com/alaub81/bmw-mqtt-bridge/security/advisories/new).
Do not post tokens, VINs, locations, credentials or private logs in public issues.
Include affected revision/image digest, a redacted reproduction and likely impact.
There is no guaranteed response time or long-term support branch; use the latest
stable release and check its security advisories before deployment.

Keep `.env`, token volumes and backups private. Docker administrators can read
container environment variables. Enable local broker TLS and certificate
verification on untrusted networks. Debug logs may contain vehicle telemetry.

Private notes and `AGENTS.md` are excluded from Git and the Docker build context.
Ignore rules do not remove previously published history. Rotate any credentials
that have been exposed; this project preparation does not rewrite Git history.
