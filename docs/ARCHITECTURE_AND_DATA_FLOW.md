# EZBay architecture and data flow

## Runtime shape

EZBay is a private FastAPI application (`automation_control.api:app`) backed by a
single Alembic-versioned SQLite database. `ezbay.service` listens on localhost port
8080 and is privately proxied through Tailscale. NL Vault is a separate FastAPI
process on localhost port 8090, backed by the same business database. Scanner and
scheduled jobs run as systemd services/timers. This application is not deployed in
Docker.

SQLite remains appropriate while there is one operator and limited concurrent
writing. Reconsider a server database before adding multiple active operators or
independent writer processes; do not migrate merely to match a generic template.

## Sources of truth

| Concern | Canonical record | Compatibility only |
|---|---|---|
| Product identity | `catalog_items` plus type detail | legacy card-specific links |
| Owned stock | `inventory_items.catalog_item_id` | synchronized nullable `card_id` |
| Price history | `card_prices` addressed by catalogue item | physical historical column name |
| Sellable draft | `shop_listings` | empty `pending_listings` schema/detail |
| Purchase candidate | Market Watch listing candidate fields | empty Potential Stock schema/detail |
| Sales and customers | `sales`, `sale_items`, `customers` | none |
| Operational evidence | `audit_events`, job status files, recovery status | dormant generic control-plane tables |

Every channel must point back to canonical inventory. Channel-specific state may
reserve or consume it, but may not create an independent stock count.

## Primary flows

### 1. Stock and pricing

```text
scan or photo -> type-specific review -> canonical catalogue -> owned inventory
                                                        -> append-only price history
```

The review boundary is intentional: low-confidence identity and unresolved foil
state stay visible without becoming stock. Supported stale prices are work;
explicitly `manual_only` items are truthful unsupported coverage.

### 2. eBay

```text
inventory -> canonical shop listing -> production preflight -> direct offer
          -> imported order -> pack/complete -> inventory reconciliation
```

CSV export reads the same canonical drafts and remains the bulk/recovery path.
Direct publishing uses SKU/offer reconciliation so a retry after an uncertain
response does not casually duplicate the listing.

### 3. NL Vault

```text
listing + inventory -> atomic checkout reservation -> verified payment
                    -> paid -> packed -> shipped
                    -> cancellation/expiry restores once
                    -> refund optionally restocks only after physical return
```

Signed Stripe events and operator actions share guarded transitions. Payment and
expiry races therefore have one winner and cannot decrement or restore twice.

### 4. Other sales

```text
inventory -> Whatnot reservation -> sold / unsold / giveaway -> sale/customer
inventory -> direct sale ---------> sale/customer             -> optional one-time void
```

Whatnot, eBay and NL Vault own their channel records. The direct-sale form rejects
those channels to prevent double entry.

### 5. Purchasing

```text
retailer order -> purchase lot -> landed-cost inventory -> guarded refund
supplier product -> quoted lot -> actual cost allocation -> one-time receipt
```

Retailer purchase, fund ledger, lot and inventory changes commit together.
Supplier lines require canonical products and snapshot their quoted cost; receipt
allocates the exact final total proportionally.

### 6. Recovery

```text
online SQLite snapshot -> encrypted restic backup -> temporary restore
                       -> integrity/FK checks -> migrate to head -> app health
```

The restore and release-rollback drills never overwrite production. Their status
files feed the same dashboard used for scheduled-job health.

## Safety boundaries

- Alembic is authoritative; startup refuses a database revision mismatch.
- Business transitions use database transactions and idempotency guards.
- Audit events are append-only operational evidence and redact credential-shaped
  values.
- External publication, purchasing, payment/refund and destructive corrections
  require an explicit operator action.
- Generated media, databases, backups and secret configuration are outside Git.

Schema detail belongs in `automation_control/models.py` and Alembic revisions,
not a manually duplicated table catalogue. Operational detail belongs in the
[operator guide](OPERATOR_GUIDE.md) and
[security/recovery guide](SECURITY_AND_RECOVERY.md).
