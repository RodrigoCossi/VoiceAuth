"""Voice authentication middleware for Telegram → any OpenAI-compatible agent backend.

Architecture (polling, no public webhook needed):
  Telegram ←─polling─→ This middleware ──auth──→ Agent backend /v1/chat/completions
                                                         ↓
                                              Response relayed back to Telegram

All messages from Telegram are intercepted here before the agent backend sees
them. The backend only ever receives authenticated, text-form requests.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from auth.voice_verifier import VoiceVerifier
from auth.enrollment import EnrollmentManager
from auth.session_manager import SessionManager
from auth.pin_auth import PinAuth
from auth.anti_replay import AntiReplayCache
from database.auth_log import AuthLogger
from notifications.ntfy import NtfyNotifier
from forwarding.agent_forwarder import AgentForwarder
from handlers.command_handler import CommandHandler
from handlers.voice_handler import VoiceHandler
from handlers.text_handler import TextHandler
from tts import TextToSpeech
from telegram_menu import set_command_menu


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        import pathlib
        pathlib.Path("./data/logs").mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            "./data/logs/voice-auth.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as exc:
        logging.warning("Could not set up file logging: %s", exc)


logger = logging.getLogger(__name__)


# ── Background monitor ────────────────────────────────────────────────────────

async def _monitor(config: Config, sessions: SessionManager, pin: PinAuth, bot) -> None:
    """Periodically checks for session expiry warnings and PIN timeouts."""
    while True:
        await asyncio.sleep(10)

        # Session expiry warnings
        for session in sessions.all_sessions():
            if sessions.needs_warning(session.user_id):
                sessions.mark_warning_sent(session.user_id)
                remaining = sessions.time_remaining(session.user_id)
                mins = config.session_warning_minutes
                try:
                    await bot.send_message(
                        chat_id=session.chat_id,
                        text=(
                            f"⏰ Your session expires in {mins} minute(s). "
                            "Send any message to extend, or it will lock automatically."
                        ),
                    )
                except Exception as exc:
                    logger.warning("Could not send session warning: %s", exc)

        # Session expiry — snapshot the list before iterating since get() may delete
        for session in list(sessions.all_sessions()):
            if sessions.time_remaining(session.user_id) is not None:
                continue  # still active
            # Session just expired (get() already removed it); notify the user
            try:
                await bot.send_message(chat_id=session.chat_id, text="🔒 Session expired.")
            except Exception as exc:
                logger.warning("Could not send session expiry message: %s", exc)
            await set_command_menu(bot, session.chat_id, authenticated=False)

        # PIN timeouts
        for user_id, prompt_msg_id in pin.timed_out_users():
            try:
                # Find chat_id — we use user_id as chat_id for DMs
                await bot.delete_message(chat_id=user_id, message_id=prompt_msg_id)
            except Exception:
                pass
            try:
                await bot.send_message(chat_id=user_id, text="🔒 Access timed out.")
            except Exception as exc:
                logger.warning("Could not send PIN timeout message: %s", exc)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def make_dispatcher(
    config: Config,
    sessions: SessionManager,
    pin: PinAuth,
    enrollment: EnrollmentManager,
    db: AuthLogger,
    notifier: NtfyNotifier,
    cmd_handler: CommandHandler,
    voice_handler: VoiceHandler,
    text_handler: TextHandler,
):
    async def dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return

        user = update.effective_user
        if not user:
            return

        # ── Gate 1: User ID whitelist ─────────────────────────────────────────
        if user.id != config.authorized_user_id:
            try:
                await msg.reply_text("⛔")
            except Exception:
                pass
            asyncio.create_task(
                notifier.unknown_user(user.id, user.username, msg.chat_id, _msg_type(msg))
            )
            asyncio.create_task(
                db.log(
                    telegram_user_id=user.id,
                    telegram_username=user.username,
                    chat_id=msg.chat_id,
                    result="BLOCKED_USER",
                )
            )
            return

        # ── PIN entry state ───────────────────────────────────────────────────
        if pin.is_waiting(user.id):
            if msg.text and not msg.text.startswith("/"):
                await _handle_pin_input(update, context, config, sessions, pin, db)
                return
            # Any other message type while waiting for PIN → cancel
            prompt_id = pin.cancel(user.id)
            if prompt_id:
                try:
                    await context.bot.delete_message(msg.chat_id, prompt_id)
                except Exception:
                    pass
            # fall through to normal dispatch

        # ── Enrollment passphrase state ───────────────────────────────────────
        if enrollment.is_awaiting_passphrase(user.id):
            if msg.text and not msg.text.startswith("/"):
                result = enrollment.save_passphrase(user.id, msg.text.strip())
                try:
                    await context.bot.delete_message(msg.chat_id, msg.message_id)
                except Exception:
                    pass
                await context.bot.send_message(msg.chat_id, result)
            else:
                await msg.reply_text("Please type your authentication passphrase as a text message.")
            return

        # ── Enrollment state ──────────────────────────────────────────────────
        if enrollment.is_enrolling(user.id):
            if msg.voice:
                await voice_handler.handle(update, context)
            else:
                await msg.reply_text("🎙️ Please send a voice message for enrollment.")
            return

        # ── Commands ──────────────────────────────────────────────────────────
        if msg.text and msg.text.startswith("/"):
            await cmd_handler.handle(update, context)
            return

        # ── Gate 2: Session check ─────────────────────────────────────────────
        has_session = sessions.is_active(user.id)

        if msg.voice:
            await voice_handler.handle(update, context)
            return

        if not has_session:
            await msg.reply_text("🔒 Session locked. Authenticate to continue.")
            return

        # Active session — forward to the agent backend
        if msg.text:
            await text_handler.handle_text(update, context)
        elif msg.photo:
            await text_handler.handle_photo(update, context)
        elif msg.document:
            await text_handler.handle_document(update, context)
        else:
            # Sticker, audio, video, etc. — describe and forward
            sessions.extend(user.id)
            mtype = _msg_type(msg)
            response = await text_handler._forwarder.forward_text(
                msg.chat_id, f"[{mtype} received]"
            )
            if response:
                await msg.reply_text(response[:4096])

    return dispatch


async def _handle_pin_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    config: Config,
    sessions: SessionManager,
    pin: PinAuth,
    db: AuthLogger,
) -> None:
    msg = update.message
    user = update.effective_user
    entered_pin = msg.text.strip()

    # Delete the user's PIN message and the bot's prompt immediately
    prompt_id = pin.consume(user.id)
    try:
        await context.bot.delete_message(msg.chat_id, msg.message_id)
    except Exception:
        pass
    if prompt_id:
        try:
            await context.bot.delete_message(msg.chat_id, prompt_id)
        except Exception:
            pass

    pin.record_attempt(user.id)

    if pin.verify_pin(entered_pin):
        pin.reset_rate(user.id)
        sessions.create(user.id, msg.chat_id, "pin")
        await db.log(
            telegram_user_id=user.id, telegram_username=user.username,
            chat_id=msg.chat_id, result="PASS",
        )
        await context.bot.send_message(msg.chat_id, "🔓 Auth accepted. Session started.")
        await set_command_menu(context.bot, msg.chat_id, authenticated=True)
    else:
        await db.log(
            telegram_user_id=user.id, telegram_username=user.username,
            chat_id=msg.chat_id, result="FAIL",
        )
        await context.bot.send_message(msg.chat_id, "⛔ Unauthorized.")


def _msg_type(msg) -> str:
    if msg.voice:
        return "Voice message"
    if msg.photo:
        return "Photo"
    if msg.document:
        return "Document"
    if msg.video:
        return "Video"
    if msg.audio:
        return "Audio"
    if msg.sticker:
        return "Sticker"
    if msg.text:
        return "Text"
    return "Unknown"


# ── Entry point ───────────────────────────────────────────────────────────────

async def run() -> None:
    setup_logging()
    config = Config.from_env()

    logger.info("Initializing voice authentication middleware…")

    # Init DB
    db = AuthLogger(config.auth_log_db)
    await db.init()

    # Init auth components
    verifier = VoiceVerifier(config.model_cache_dir)
    try:
        await verifier.load()
    except Exception as exc:
        logger.error("FATAL: Could not load SpeechBrain model: %s", exc)
        logger.error("All voice authentication attempts will be rejected.")

    enrollment = EnrollmentManager(config, verifier)
    sessions = SessionManager(config)
    pin = PinAuth(config.pin_hash_path)
    replay = AntiReplayCache()
    notifier = NtfyNotifier(config)
    forwarder = AgentForwarder(config)

    # Build PTB application
    app = Application.builder().token(config.telegram_bot_token).build()

    tts = TextToSpeech(config)

    cmd_handler = CommandHandler(config, sessions, pin, enrollment, db, notifier)
    voice_handler = VoiceHandler(config, verifier, enrollment, sessions, replay, db, notifier, forwarder, tts)
    text_handler = TextHandler(config, sessions, forwarder, tts)

    dispatch = make_dispatcher(
        config, sessions, pin, enrollment, db, notifier,
        cmd_handler, voice_handler, text_handler,
    )

    # Single catch-all handler
    app.add_handler(MessageHandler(filters.ALL, dispatch))

    logger.info("Starting Telegram polling…")

    async with app:
        await app.start()

        # No hints for strangers; authorized user starts locked (sessions
        # never survive a restart).
        try:
            await app.bot.set_my_commands([])
        except Exception as exc:
            logger.warning("Could not clear default command menu: %s", exc)
        await set_command_menu(app.bot, config.authorized_user_id, authenticated=False)

        await app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        # Start background monitor
        monitor_task = asyncio.create_task(
            _monitor(config, sessions, pin, app.bot)
        )

        logger.info("Middleware running. Waiting for messages…")

        # Run until interrupted
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            monitor_task.cancel()
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(run())
