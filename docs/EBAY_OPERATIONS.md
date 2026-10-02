# eBay operations

## Supported paths

- **Direct publish** is the default for one reviewed listing.
- **CSV bulk upload** is retained for large batches and recovery.
- **Order import** runs every ten minutes and reconciles eBay sales into local
  stock. It is independent of direct-publish business-policy readiness.

The direct path was production-validated on 3 October 2026 with Alcremie VMAX
#73: offer `293098215011`, listing `267801619646`, AUD 6.96, quantity one.
Inventory API and Trading API read-backs both confirmed it as active.

## Production prerequisites

The production OAuth grant must include `sell.inventory`,
`sell.fulfillment.readonly`, `commerce.identity.readonly` and `sell.account`.
Reconnect from Settings whenever the requested scope set changes; refresh cannot
add permission to an older grant.

Direct publication also requires these deployment settings:

- `EBAY_PAYMENT_POLICY_ID`: Trading Cards - Standard Payment Policy
- `EBAY_FULFILLMENT_POLICY_ID`: Trading Cards & Sealed TCG - Australia Post
- `EBAY_RETURN_POLICY_ID`: Trading Cards & Sealed TCG - Refund Policy
- `EBAY_MERCHANT_LOCATION_KEY`: `ezbay-sydney-nsw`

The location is an enabled Sydney NSW warehouse. Never replace these values with
truthy placeholders: write readiness deliberately rejects placeholders.

## Publish one listing

1. Confirm the canonical shop listing has the real inventory item, photo,
   quantity, price and condition.
2. Run the read-only audit:

   ```bash
   set -a; source .env.local; set +a
   .venv/bin/python -m scripts.ebay_publish_preflight --listing-id <uuid>
   ```

3. In EZBay, open the listing's **Review eBay publication** page. Read the
   rendered title, price, quantity, category and image.
4. Tick the live-publication confirmation and publish once.
5. Verify the link is active in EZBay and the item appears in eBay active
   listings. The audit trail must contain an approved request and a success
   event.

Publication is SKU-idempotent: a retry looks up and resumes the existing offer
instead of blindly creating another. A per-listing lock also rejects a concurrent
double-click.

## Failure and recovery

- If preflight is blocked, fix its stated local/configuration problem; do not
  bypass it.
- If a publish call fails, keep the failed link. Check eBay by its SKU and saved
  offer ID before retrying. The retry path is designed to reuse that remote
  state.
- If more than one offer exists for a SKU, stop and resolve the duplicate on
  eBay; EZBay fails closed rather than choosing one.
- Listings created through Inventory API should be revised or withdrawn through
  EZBay/API, not treated as ordinary Seller Hub-created listings.
- Before a manual production retry, create an online SQLite backup with
  `.backup` and verify `PRAGMA integrity_check`.

The order importer is expected to exit successfully even when there are no new
orders. Check its current result with:

```bash
systemctl status ebay-order-import.service ebay-order-import.timer --no-pager
tail -40 scan_ingest_data/logs/ebay-order-import.log
```
