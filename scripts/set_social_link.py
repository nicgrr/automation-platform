"""Set or clear a social link's URL and active state.

    .venv/bin/python -m scripts.set_social_link instagram https://instagram.com/nlvault
    .venv/bin/python -m scripts.set_social_link ebay https://ebay.com.au/str/nlvault
    .venv/bin/python -m scripts.set_social_link whatnot https://whatnot.com/user/nlvault
    .venv/bin/python -m scripts.set_social_link instagram --off     # hide without losing the url
    .venv/bin/python -m scripts.set_social_link --list
"""

import argparse

from sqlalchemy import select

from automation_control.database import SessionLocal
from automation_control.models import SocialLink


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("platform", nargs="?", help="e.g. instagram, ebay, whatnot")
    parser.add_argument("url", nargs="?", help="the full link, e.g. https://instagram.com/yourhandle")
    parser.add_argument("--off", action="store_true", help="deactivate without clearing the url")
    parser.add_argument("--on", action="store_true", help="reactivate")
    parser.add_argument("--list", action="store_true", help="show every configured link")
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if args.list or not args.platform:
            rows = session.scalars(select(SocialLink).order_by(SocialLink.sort_order)).all()
            if not rows:
                print("no social links configured -- run the migration first.")
                return 0
            for row in rows:
                state = "active" if row.is_active else "off"
                print(f"  {row.platform:<12} {row.label:<14} {row.url or '(no url set)':<40} [{state}]")
            return 0

        row = session.scalar(select(SocialLink).where(SocialLink.platform == args.platform.lower()))
        if row is None:
            raise SystemExit(f"no social link row for {args.platform!r} -- run the migration to seed it first.")

        if args.url:
            row.url = args.url.strip()
        if args.off:
            row.is_active = False
        if args.on:
            row.is_active = True
        session.commit()
        print(f"{row.platform}: url={row.url or '(none)'} active={row.is_active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
