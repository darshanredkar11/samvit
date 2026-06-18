# Security Policy

## Supported Versions

Samvit is currently alpha software. Security fixes are applied to the latest
minor release.

## Deployment Guidance

- Keep port 8765 private. The default Compose configuration binds to localhost.
- For multiple machines, use a private VPN such as Tailscale/WireGuard or a TLS
  reverse proxy with firewall rules.
- Replace all default database and admin secrets before shared deployment.
- Give every human or agent its own token. Rotate a token if it is exposed.
- Mount code repositories read-only and restrict `SAMVIT_CODE_ROOTS`.
- Do not expose PostgreSQL or Redpanda directly to agent machines.

## Reporting a Vulnerability

Do not open a public issue for credentials, authentication bypasses, data
exposure, or remote-code-execution concerns. Contact the repository owner
privately through the GitHub profile associated with this project.

Include the affected version, reproduction steps, impact, and any suggested
mitigation. Please allow time for a fix before public disclosure.
