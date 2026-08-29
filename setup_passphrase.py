#!/usr/bin/env python3
"""Server-side passphrase setup. SSH access required.

Use this if you already have a voiceprint enrolled and want to set or change
the authentication passphrase without re-enrolling from scratch.

Run in a real terminal (not piped) — same requirement as setup_pin.py.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    import os
    passphrase_path = Path(os.getenv("PASSPHRASE_PATH", "./data/passphrase.txt"))

    print("Voice Auth Middleware — Authentication Passphrase Setup")
    print("=" * 55)

    if passphrase_path.exists() and passphrase_path.stat().st_size > 0:
        current = passphrase_path.read_text().strip()
        print(f"Current passphrase: {current}")
        overwrite = input("A passphrase already exists. Overwrite? (yes/no): ").strip().lower()
        if overwrite not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    print("\nEnter the passphrase you will say to start a voice session.")
    print("Keep it 4–10 words, something natural to say aloud.")
    passphrase = input("Passphrase: ").strip()

    if not passphrase:
        print("Error: passphrase cannot be empty.")
        sys.exit(1)

    confirm = input("Confirm passphrase: ").strip()
    if passphrase != confirm:
        print("Error: passphrases do not match.")
        sys.exit(1)

    passphrase_path.parent.mkdir(parents=True, exist_ok=True)
    passphrase_path.write_text(passphrase)
    print(f"\n✅ Passphrase saved to {passphrase_path}")
    print("Say this phrase when authenticating via voice message to start a session.")


if __name__ == "__main__":
    main()
