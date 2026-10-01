#!/usr/bin/env python
"""Scan active collectible watches in Australia and Japan.

No purchase, message or offer is ever sent. eBay uses its official Browse
API; optional public-web results require a Brave Search plan whose licence
permits EzBay to retain results.
"""

import argparse
import asyncio

from automation_control.config import get_settings
from automation_control.database import SessionLocal
from automation_control.market_alert_import import (
    import_unseen_alerts,
    imap_configured,
    mark_imported_alerts_seen,
)
from automation_control.market_scanner import scan_market_watches


async def run(limit: int = 20, dry_run: bool = False, send: bool = False,
              watch_ids: list[str] | None = None, fetch_alerts: bool = True) -> int:
    settings = get_settings()
    email_result = None
    with SessionLocal() as session:
        result = await scan_market_watches(
            session,
            settings,
            watch_ids=watch_ids,
            limit=min(max(limit, 1), 200),
            send=send,
        )
        email_note = "mailbox=not-configured"
        if fetch_alerts and not dry_run and imap_configured(settings):
            try:
                email_result = await import_unseen_alerts(session, settings)
                email_note = f"mailbox_new={email_result.imported} mailbox_updated={email_result.updated}"
            except Exception as exc:
                email_note = f"mailbox_error={type(exc).__name__}"
                result.errors.append(f"Alert mailbox: {type(exc).__name__}")
        if dry_run:
            session.rollback()
        else:
            session.commit()
    if email_result is not None:
        try:
            await mark_imported_alerts_seen(settings, email_result)
        except Exception as exc:
            email_note += f" mailbox_mark_error={type(exc).__name__}"
            result.errors.append(f"Alert mailbox mark-read: {type(exc).__name__}")
    print(
        f"watches={result.watches} found={result.found} created={result.created} "
        f"updated={result.updated} skipped={result.skipped} alerts={result.alerts} "
        f"errors={len(result.errors)} {email_note} dry_run={dry_run}"
    )
    for error in result.errors:
        print(f"warning: {error}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="maximum results per watch/source/country")
    parser.add_argument("--watch-id", action="append", dest="watch_ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--send-alerts", action="store_true")
    parser.add_argument("--skip-alert-mailbox", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(run(
        args.limit, args.dry_run, args.send_alerts, args.watch_ids,
        fetch_alerts=not args.skip_alert_mailbox,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
