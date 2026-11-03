"""Move figurines unsold on Vinted for 14 days to eBay. Prints what is due without --apply.

    .venv/bin/python -m scripts.vinted_ebay_fallback [--apply]
"""

from __future__ import annotations

import argparse
import asyncio

from automation_control.database import SessionLocal
from automation_control.vinted_fallback import run_fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="publish to eBay; otherwise only list what is due")
    args = parser.parse_args()
    with SessionLocal() as session:
        results = asyncio.run(run_fallback(session, apply=args.apply))
    print("\n".join(results) or "Nothing due.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
