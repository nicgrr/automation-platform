# Phase 1B private configuration

## Network boundary

The application defaults to `127.0.0.1:8080` and must not be bound to `0.0.0.0`.
For access from the existing tailnet, keep that localhost binding and use
Tailscale Serve as a private HTTPS reverse proxy. Do not use Tailscale Funnel.
No router forwarding or firewall change is required.

The eBay Accepted Auth URL is:

```text
https://<this-machine's-tailscale-dns-name>/auth/ebay/callback
```

Set `APP_PUBLIC_BASE_URL` to the same URL without the callback path. The exact DNS
name must be read from the operator's existing Tailscale configuration before
OAuth can be enabled. Phase 1B does not alter that configuration automatically.

## Secret file

Create a file outside Git, readable only by the service account (mode `0600`).
Populate the variable names from `.env.example`; do not copy the file into source
control. The application reads values from the process environment and does not
read arbitrary secret files.

Generate the dashboard password hash and independent cryptographic keys
interactively:

```bash
./scripts/generate-secrets.sh
```

Store the three printed assignments in the protected environment file. Do not
paste them into prompts, documentation, frontend code, or Git.

After filling those assignments, start the localhost-only control plane with:

```bash
./scripts/start-control-plane.sh
```

## Required eBay scopes

User authorization-code token:

```text
https://api.ebay.com/oauth/api_scope/sell.inventory.readonly
https://api.ebay.com/oauth/api_scope/commerce.identity.readonly
```

Application client-credentials token for Browse market search:

```text
https://api.ebay.com/oauth/api_scope
```

Only Sandbox authorization and API hosts are present in code.
