#!/usr/bin/env python3
"""Server-side enrollment reset. SSH access required — no bot command for this.

Deletes voiceprint.npy and all enrollment samples so /enroll can be run again.
This deliberate design prevents a phone thief from re-enrolling their own voice.
"""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    import os
    vp_path = Path(os.getenv("VOICEPRINT_PATH", "./data/voiceprint.npy"))
    enroll_dir = Path(os.getenv("ENROLLMENT_DIR", "./data/enrollment_samples/"))
    passphrase_path = Path(os.getenv("PASSPHRASE_PATH", "./data/passphrase.txt"))
    db_path = Path(os.getenv("AUTH_LOG_DB", "./data/auth_log.db"))

    print("This will delete your voiceprint, enrollment samples, and authentication passphrase.")
    confirm = input("Type YES to confirm: ").strip()
    if confirm != "YES":
        print("Aborted.")
        sys.exit(0)

    deleted = []

    if vp_path.exists():
        vp_path.unlink()
        deleted.append(str(vp_path))

    if enroll_dir.exists():
        for f in enroll_dir.glob("*.npy"):
            f.unlink()
            deleted.append(str(f))

    if passphrase_path.exists():
        passphrase_path.unlink()
        deleted.append(str(passphrase_path))

    if deleted:
        print(f"Deleted: {', '.join(deleted)}")
    else:
        print("Nothing to delete (no voiceprint found).")

    # Log the reset
    try:
        import aiosqlite
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO auth_log
                   (timestamp, telegram_user_id, telegram_username, chat_id, result)
                   VALUES (?, 0, 'SERVER_RESET', 0, 'RESET')""",
                (ts,),
            )
            await db.commit()
        print("Reset event logged to database.")
    except Exception as exc:
        print(f"Note: could not log to database: {exc}")

    print("✅ Enrollment reset complete. Send /enroll to the bot to re-enroll.")


if __name__ == "__main__":
    asyncio.run(main())
