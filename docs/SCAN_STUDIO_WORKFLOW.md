# Scan Studio workflow

Open `/scan-studio` from the CRM navigation. This is the responsive home-screen web app, not a separate native app.

## Capture and identify

Choose New stock, Additional copies, or Photo update before uploading. Raw cards use this flow; slabs and collectibles retain the Add stock route linked on the capture screen. Use continuous camera on a secure connection, or the phone photo picker if camera access is unavailable. Captures remain local until Identify photos is pressed. A batch holds up to 40 photos. Front/back mode expects alternating pairs. Camera tracks stop when the page is hidden or closed.

Review the actual photo against catalogue artwork and printed number, set, language, rarity and parallel. Missing catalogue details remain unknown. Confirm finish separately; similar artwork does not establish a Poké Ball, Master Ball, Cosmos or gold printing. Confirm & next commits the intake before advancing. Review later leaves a card pending. Repeated confirmation does not increment stock twice.

## Stock and storage

Receipt history records the new copies in each batch. Photo updates add no stock. Storage labels reserve a place; Confirm storage records physical put-away.

`inventory_quantity_events` captures quantity changes from both ORM and direct SQL through SQLite triggers, including checkout, eBay, Whatnot and Vinted paths. The migration establishes existing stock as a baseline without changing quantities. New Studio intake tags its positive event with its photo pair. Other additions and returns stay unassigned.

Remaining-source counts use an explicit accounting rule: reductions consume newest photographed intake first, then other additions newest first. They cannot identify which identical physical copy was actually picked. Historical pre-tracking sales are not reconstructed. Ledger events roll back with failed transactions. Do not edit or remove the triggers independently of an Alembic migration.

## Price review

Batch prices shows distinct confirmed listings, photos, offered quantities and the copies added by that batch. Sold and active eBay AU searches are evidence links, not automatic valuations. Save reviewed price records Single or Consider for bulk, with optional upward .99 rounding.

Approval is tied to catalogue identity, printing, finish, condition, currency and price. Changing these requires review again. New Scan Studio listings are blocked from single publication until their current price is approved as a single. Existing legacy listings retain their previous workflow. A $5 threshold suggests singles versus bulk; it does not estimate profit, create bundles, or end existing offers.

## Verify channels

Channel audit (`/scan-studio/channel-audit`) shows saved proposals versus observed eBay details. Listing Studio has Check eBay now, which reads the exact linked item through GetItem. It refreshes title, price, available quantity, status, photos and description in the read mirror. It does not apply saved proposals to eBay. Network/incomplete responses preserve the previous observation.

The catalogue storefront projects linked active eBay values and photos from that mirror. It does not display an unpublished photo change as though eBay already has it.

Compare saved photos with eBay/storefront opens both galleries. Confirm photos match records the operator's visual acknowledgement tied to local image content, remote URLs and readback timestamp. Changing photos or refreshing evidence invalidates the acknowledgement. Automated image similarity is triage and is never recorded as operator approval.

`scripts.audit_listing_photo_content --output <report.json> [--listing-id <id>]` compares image content without marketplace changes. It only fetches eBay image hosts and the configured public photo bucket. Review mismatches; do not infer a different physical copy from differing pixels alone.

## Validation and rollout, 2026-11-09

- Migration `20261109_01`: stock/listing counts, quantity totals and price totals preserved; every inventory item balances to its events.
- 263 focused regression tests across intake, stock, publication, commerce, Whatnot, Vinted and storefront; additional price-override/photo-invalidation guards passed separately.
- Isolated workflow covers upload, deterministic recognition output, printing/quantity confirmation, storage, price approval, mocked publication, duplicate-publication rejection and fresh readback into storefront. No test listing was published to production.
- Six screens checked at 390px and 1440px. Continuous capture, individual removal, camera shutdown and CSRF upload transition verified using simulated camera hardware.
- 351 active eBay items and their full photo/description sets refreshed. 340 explicitly linked listings photo-compared: 335 visually similar; five catalogue-art holdovers corrected via photo-only revision and rechecked. Price, quantity, title and description preserved on each correction.

Backups and operational JSON reports stay in `backups/` and are not committed. Physical iPhone camera behaviour has not been tested on the user's hardware.
