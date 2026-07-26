# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report privately via
GitHub Security Advisories (the "Report a vulnerability" button on the Security
tab of this repository), or email the maintainer.

Include: a description, affected version(s), and a minimal reproduction if
possible. We aim to acknowledge within a few days.

## Scope notes

- Larz makes **no calls to any vendor server** and has **no telemetry** — it runs
  entirely on your infrastructure. Reports about "phoning home" are always in
  scope (there shouldn't be any).
- `larz.crypto` is authenticated encryption (HMAC-SHA256-CTR + encrypt-then-MAC)
  for secrets at rest. For regulated/high-value secrets, a KMS or the
  `cryptography` package may be preferable — that's a documented trade-off, not a
  vulnerability, but crypto issues are otherwise in scope.
- The `larz.pg` PostgreSQL driver implements SCRAM-SHA-256; auth/protocol issues
  are in scope.

## Supported versions

The latest released `2.x` line receives security fixes.
