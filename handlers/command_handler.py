"""Handles bot commands: /enroll, /pin, /lock, /status, /help, /auth_log."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_menu import set_command_menu

logger = logging.getLogger(__name__)


class CommandHandler:
    def __init__(self, config, session_manager, pin_auth, enrollment, db, notifier) -> None:
        self._cfg = config
        self._sessions = session_manager
        self._pin = pin_auth
        self._enroll = enrollment
        self._db = db
        self._notifier = notifier

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg or not msg.text:
            return
        cmd = msg.text.split()[0].lower().lstrip("/").split("@")[0]
        user_id = update.effective_user.id
        has_session = self._sessions.is_active(user_id)

        if cmd == "enroll":
            await self._cmd_enroll(update, context)
        elif cmd == "pin":
            await self._cmd_pin(update, context)
        elif cmd == "lock":
            await self._cmd_lock(update, context)
        elif cmd in ("status", "help", "auth_log"):
            if not has_session:
                await msg.reply_text("🔒 Session locked. Authenticate to continue.")
            elif cmd == "status":
                await self._cmd_status(update, context)
            elif cmd == "help":
                await self._cmd_help(update, context)
            elif cmd == "auth_log":
                await self._cmd_auth_log(update, context)
        else:
            # Unknown command while locked → minimal response
            if not has_session:
                await msg.reply_text("🔒 Session locked. Authenticate to continue.")

    # ── /enroll ───────────────────────────────────────────────────────────────

    async def _cmd_enroll(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user_id = update.effective_user.id

        if self._enroll.voiceprint_exists():
            await msg.reply_text(
                "⚠️ Voiceprint already enrolled. "
                "To re-enroll, use the server-side reset tool (see README)."
            )
            return

        if not self._cfg.voiceprint_path.parent.exists():
            self._cfg.voiceprint_path.parent.mkdir(parents=True, exist_ok=True)

        self._enroll.start(user_id)
        phrase = self._enroll.next_phrase(user_id)
        await msg.reply_text(
            f"🎙️ Enrollment started. I need {self._cfg.enrollment_samples_required} voice samples.\n\n"
            f"Send a voice message saying:\n_{phrase}_",
            parse_mode="Markdown",
        )

    # ── /pin ──────────────────────────────────────────────────────────────────

    async def _cmd_pin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user_id = update.effective_user.id

        # Delete the /pin command immediately
        try:
            await context.bot.delete_message(msg.chat_id, msg.message_id)
        except Exception:
            pass

        if not self._pin.hash_exists():
            await context.bot.send_message(
                msg.chat_id,
                "⚠️ No PIN configured. Run `python setup_pin.py` on the server first.",
            )
            return

        if self._pin.is_rate_limited(user_id):
            await self._notifier.pin_lockout(user_id, msg.chat_id)
            await context.bot.send_message(msg.chat_id, "⛔ Too many attempts. Try again later.")
            return

        prompt = await context.bot.send_message(msg.chat_id, "🔑 Enter your 6-digit PIN:")
        self._pin.start(user_id, prompt.message_id)

    # ── /lock ─────────────────────────────────────────────────────────────────

    async def _cmd_lock(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        self._sessions.end(user_id)
        await update.message.reply_text("🔒 Session locked.")
        await set_command_menu(context.bot, update.message.chat_id, authenticated=False)

    # ── /status ───────────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        session = self._sessions.get(user_id)
        remaining = self._sessions.time_remaining(user_id)
        enrolled = self._enroll.voiceprint_exists()

        sample_count = 0
        if self._cfg.enrollment_dir.exists():
            sample_count = len(list(self._cfg.enrollment_dir.glob("*.npy")))

        failed_24h = await self._db.failed_last_24h(user_id)

        lines = [
            f"*Voice Auth Middleware Status*",
            f"",
            f"Voiceprint: {'✅ enrolled' if enrolled else '❌ not enrolled'} ({sample_count} samples)",
            f"Session: ✅ active" if session else "Session: ❌ inactive",
        ]
        if session and remaining:
            mins = int(remaining.total_seconds() // 60)
            secs = int(remaining.total_seconds() % 60)
            lines.append(f"Time remaining: {mins}m {secs}s")
            lines.append(f"Auth method: {session.auth_method}")
            started = session.started_at.strftime("%H:%M:%S UTC")
            lines.append(f"Session started: {started}")
        lines.append(f"Failed attempts (24h): {failed_24h}")
        lines.append(f"Model loaded: {'✅' if self._enroll._verifier.loaded else '❌'}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ── /help ─────────────────────────────────────────────────────────────────

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "*Voice Auth Middleware — Commands*\n\n"
            "*Always available:*\n"
            "/pin — Step 1: enter your PIN (message deleted automatically)\n"
            "/lock — End current session immediately\n"
            "/enroll — Enroll voiceprint (only if not yet enrolled)\n\n"
            "*Requires active session:*\n"
            "/status — Show system status\n"
            "/help — Show this message\n"
            "/auth\\_log — Show last 10 authentication attempts\n\n"
            "_Two factors open a session: /pin first, then a voice message with your "
            "passphrase within 5 minutes._"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    # ── /auth_log ─────────────────────────────────────────────────────────────

    async def _cmd_auth_log(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        rows = await self._db.recent(10)
        if not rows:
            await update.message.reply_text("No authentication attempts logged yet.")
            return

        lines = ["*Last 10 authentication attempts:*\n"]
        for r in rows:
            ts = r["timestamp"][:19].replace("T", " ")
            score = f"{r['similarity_score']:.3f}" if r["similarity_score"] is not None else "—"
            lines.append(f"`{ts}` {r['result']} score={score}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
