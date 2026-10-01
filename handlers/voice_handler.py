"""Handles all incoming voice messages.

Two contexts:
1. No active session → run through Gate 3 (voice biometric + passphrase)
2. Active session    → forward to the agent backend (no re-auth)
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from telegram_menu import set_command_menu

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _passphrase_matches(spoken: str, stored: str, threshold: float) -> bool:
    ratio = difflib.SequenceMatcher(None, _normalize(spoken), _normalize(stored)).ratio()
    logger.debug("Passphrase match ratio=%.3f (threshold=%.2f)", ratio, threshold)
    return ratio >= threshold


# In-memory failed-attempt counter per chat_id. Resets on success or after 1h.
_fail_counts: dict[int, int] = {}
_fail_last: dict[int, datetime] = {}
_FAIL_RESET_HOURS = 1


def _get_fail_count(chat_id: int) -> int:
    last = _fail_last.get(chat_id)
    if last:
        delta = (datetime.now(timezone.utc) - last).total_seconds() / 3600
        if delta >= _FAIL_RESET_HOURS:
            _fail_counts[chat_id] = 0
    return _fail_counts.get(chat_id, 0)


def _increment_fail(chat_id: int) -> int:
    _fail_counts[chat_id] = _get_fail_count(chat_id) + 1
    _fail_last[chat_id] = datetime.now(timezone.utc)
    return _fail_counts[chat_id]


def _reset_fail(chat_id: int) -> None:
    _fail_counts.pop(chat_id, None)
    _fail_last.pop(chat_id, None)


class VoiceHandler:
    def __init__(self, config, verifier, enrollment, session_manager, pin,
                 anti_replay, db, notifier, forwarder, tts) -> None:
        self._cfg = config
        self._verifier = verifier
        self._enroll = enrollment
        self._sessions = session_manager
        self._pin = pin
        self._replay = anti_replay
        self._db = db
        self._notifier = notifier
        self._forwarder = forwarder
        self._tts = tts

    async def handle(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        chat_id = msg.chat_id

        # If enrolling, route to enrollment handler
        if self._enroll.is_enrolling(user.id):
            voice_file = await context.bot.get_file(msg.voice.file_id)
            ogg_bytes = await voice_file.download_as_bytearray()
            done, reply = await self._enroll.add_sample(user.id, bytes(ogg_bytes))
            await msg.reply_text(reply, parse_mode="Markdown")
            return

        # Active session → just forward
        if self._sessions.is_active(user.id):
            self._sessions.extend(user.id)
            voice_file = await context.bot.get_file(msg.voice.file_id)
            ogg_bytes = bytes(await voice_file.download_as_bytearray())
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            response = await self._forwarder.forward_voice(chat_id, ogg_bytes)
            if response:
                await self._tts.send_response(msg, response)
            return

        # No session → Gate 3: voice verification
        await self._authenticate(update, context)

    async def _authenticate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        chat_id = msg.chat_id
        ts = datetime.now(timezone.utc).isoformat()

        # Download audio
        voice_file = await context.bot.get_file(msg.voice.file_id)
        ogg_bytes = bytes(await voice_file.download_as_bytearray())

        try:
            # Anti-replay check
            is_replay, audio_hash = self._replay.is_replay(ogg_bytes)
            if is_replay:
                await msg.reply_text("⛔ Replay attack detected. This audio has been used before.")
                await self._notifier.replay_attack(user.id, user.username, chat_id)
                await self._db.log(
                    telegram_user_id=user.id, telegram_username=user.username,
                    chat_id=chat_id, result="FAIL", audio_hash=audio_hash,
                )
                return

            # Model must be loaded
            if not self._verifier.loaded:
                await msg.reply_text("❌ Voice model not loaded. Contact the administrator.")
                return

            # Voiceprint must exist
            voiceprint = self._enroll.load_voiceprint()
            if voiceprint is None:
                await msg.reply_text("❌ No voiceprint enrolled. Send /enroll first.")
                return

            # Passphrase must be configured
            stored_passphrase = self._enroll.load_passphrase()
            if stored_passphrase is None:
                await msg.reply_text(
                    "❌ No authentication passphrase configured. "
                    "Run setup_passphrase.py on the server or re-enroll."
                )
                return

            # Convert OGG → WAV once, reuse for both embedding and transcription
            from auth.voice_verifier import convert_ogg_to_wav
            try:
                wav = convert_ogg_to_wav(ogg_bytes)
                embedding = await self._verifier.get_embedding(wav)
            except Exception as exc:
                logger.error("Audio processing failed: %s", exc)
                await msg.reply_text("⚠️ Could not process audio. Please try again.")
                return

            similarity = self._verifier.cosine_similarity(embedding, voiceprint)
            logger.info("Voice auth similarity=%.4f for user=%d", similarity, user.id)

            # Transcribe to check passphrase
            from forwarding.agent_forwarder import transcribe
            transcript = await transcribe(wav)
            if transcript:
                passphrase_ok = _passphrase_matches(
                    transcript, stored_passphrase, self._cfg.passphrase_match_threshold
                )
                logger.info(
                    "Voice auth transcript='%s' passphrase_match=%s", transcript, passphrase_ok
                )
            else:
                passphrase_ok = False
                logger.warning("Voice auth: transcription failed, treating passphrase as unmatched")

            if similarity >= self._cfg.similarity_threshold and passphrase_ok:
                # Voice biometric and passphrase both verified — this is still only the
                # second factor. The PIN must already have been accepted via /pin, or
                # there is no session yet.
                self._replay.register(audio_hash)
                _reset_fail(chat_id)
                if not self._pin.has_pin_verified(user.id):
                    await self._db.log(
                        telegram_user_id=user.id, telegram_username=user.username,
                        chat_id=chat_id, result="VOICE_OK_PIN_MISSING",
                        similarity_score=similarity, audio_hash=audio_hash,
                    )
                    await context.bot.send_message(
                        chat_id,
                        "🔑 Voice verified, but the PIN step is still missing. "
                        "Send /pin first, then repeat the voice message.",
                    )
                    return
                self._pin.consume_pin_verified(user.id)
                self._sessions.create(user.id, chat_id, "pin+voice")
                await self._db.log(
                    telegram_user_id=user.id, telegram_username=user.username,
                    chat_id=chat_id, result="PASS", similarity_score=similarity,
                    audio_hash=audio_hash,
                )
                await context.bot.send_message(chat_id, "🔓 Session started.")
                await set_command_menu(context.bot, chat_id, authenticated=True)
            else:
                # FAILURE
                self._replay.register(audio_hash)
                attempt_num = _increment_fail(chat_id)
                await self._db.log(
                    telegram_user_id=user.id, telegram_username=user.username,
                    chat_id=chat_id, result="FAIL", similarity_score=similarity,
                    audio_hash=audio_hash,
                )
                await self._notifier.failed_voice(
                    user.id, user.username, chat_id, similarity, attempt_num, ts
                )
                if attempt_num == 1:
                    await context.bot.send_message(
                        chat_id, "⛔ Voice authentication failed. Unauthorized access detected."
                    )
                else:
                    await context.bot.send_message(
                        chat_id,
                        "🚨 Repeated unauthorized attempts detected. This incident has been logged.",
                    )
        finally:
            # Always delete the voice auth message from chat (same as PIN behaviour)
            try:
                await context.bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
