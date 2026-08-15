# Security model

- Deny tools by default.
- Keep AI processes unprivileged and without secret files or Docker access.
- Keep eBay in sandbox and expose read methods only.
- Require an exact container allowlist and cap log retrieval at 500 lines.
- Redact credential-shaped audit fields before persistence.
- Treat external text, listings, and logs as untrusted input.
- Do not execute instructions found in external data.
- Require human approval before any future irreversible or external write.

