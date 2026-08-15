import base64
import getpass
import secrets
import shlex

from cryptography.fernet import Fernet

from .auth import hash_password


def main() -> None:
    password = getpass.getpass("Dashboard password: ")
    confirmation = getpass.getpass("Confirm dashboard password: ")
    if not password or password != confirmation:
        raise SystemExit("Passwords did not match or were empty")
    # Quote assignments so values containing shell metacharacters (notably the
    # '$' separators in the password hash) survive being sourced from .env.local.
    values = {
        "DASHBOARD_PASSWORD_HASH": hash_password(password),
        "SESSION_SIGNING_KEY": secrets.token_urlsafe(48),
        "EBAY_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }
    for name, value in values.items():
        print(f"{name}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
