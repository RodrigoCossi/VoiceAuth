#!/usr/bin/env python3
"""One-time PIN setup utility. Run this on the server before starting the middleware."""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    try:
        import bcrypt
    except ImportError:
        print("ERROR: bcrypt not installed. Run: pip install bcrypt")
        sys.exit(1)

    import os
    pin_path = Path(os.getenv("PIN_HASH_PATH", "./data/pin_hash.txt"))
    pin_path.parent.mkdir(parents=True, exist_ok=True)

    if pin_path.exists() and pin_path.stat().st_size > 0:
        overwrite = input("A PIN is already set. Overwrite? (yes/no): ").strip().lower()
        if overwrite != "yes":
            print("Aborted.")
            sys.exit(0)

    import getpass
    while True:
        pin = getpass.getpass("Enter 6-digit PIN: ").strip()
        if not pin.isdigit() or len(pin) != 6:
            print("PIN must be exactly 6 digits.")
            continue
        confirm = getpass.getpass("Confirm PIN: ").strip()
        if pin != confirm:
            print("PINs do not match. Try again.")
            continue
        break

    hashed = bcrypt.hashpw(pin.encode(), bcrypt.gensalt())
    pin_path.write_bytes(hashed)
    pin_path.chmod(0o600)
    print(f"✅ PIN set and saved to {pin_path}")


if __name__ == "__main__":
    main()
