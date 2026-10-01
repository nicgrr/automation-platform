# Collectible market discovery

EzBay's `/market-watch` workflow monitors user-defined collectible searches
while admitting only Australia (`AU`) and Japan (`JP`). It never purchases,
messages a seller, logs into a marketplace, or runs an authenticated scraper.

## Sources

- **eBay** uses Browse API `itemLocationCountry` and therefore has an exact
  seller/item-location signal.
- **Public web** is optional Brave Search discovery. It is disabled until
  `BRAVE_SEARCH_API_KEY` is set and
  `BRAVE_SEARCH_STORAGE_PERMITTED=true`. Only enable the latter when the
  selected Brave plan explicitly permits retaining results.
- **Marketplace alert email** accepts exported `.eml` files from Gumtree,
  Buyee, ZenMarket, FROM JAPAN, Depop and Facebook. An optional dedicated
  IMAP mailbox can be checked by the twice-daily job.
- **Share/manual intake** accepts public listing links from those same
  services. It stores the supplied details without fetching the page.

Facebook Marketplace and Depop are deliberately not crawled: neither offers
a generally available marketplace-search API suitable for this feature.

## Optional configuration

```dotenv
BRAVE_SEARCH_API_KEY=
BRAVE_SEARCH_STORAGE_PERMITTED=false
MARKET_PUBLIC_SEARCH_DOMAINS=gumtree.com.au,depop.com,buyee.jp,zenmarket.jp,fromjapan.co.jp

MARKET_ALERT_IMAP_HOST=
MARKET_ALERT_IMAP_PORT=993
MARKET_ALERT_IMAP_USERNAME=
MARKET_ALERT_IMAP_PASSWORD=
MARKET_ALERT_IMAP_MAILBOX=INBOX
MARKET_ALERT_IMAP_USE_SSL=true
```

Use a dedicated mailbox and a revocable app password rather than a personal
mail password. Secrets are environment-only and never rendered by `/settings`.

## Schedule

`market-watch-scan.timer` runs at approximately 08:07 and 20:07 local time,
with up to ten minutes of randomized delay. It refreshes active watches and,
when configured, imports unread alert email. The job records findings only;
alerts and purchases are not sent automatically by the installed timer.

```bash
systemctl status market-watch-scan.timer
tail -f scan_ingest_data/logs/market-watch-scan.log
```
