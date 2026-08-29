"""Handles non-command text and media messages during active sessions."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class TextHandler:
    def __init__(self, config, session_manager, forwarder, tts) -> None:
        self._cfg = config
        self._sessions = session_manager
        self._forwarder = forwarder
        self._tts = tts

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward plain text to the agent backend (only called when session is active)."""
        msg = update.message
        chat_id = msg.chat_id
        self._sessions.extend(update.effective_user.id)
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._forwarder.forward_text(chat_id, msg.text or "")
        if response:
            await self._tts.send_response(msg, response, voice_reply=False)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward photo + caption to the agent backend."""
        msg = update.message
        chat_id = msg.chat_id
        self._sessions.extend(update.effective_user.id)

        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await file.download_as_bytearray())

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._forwarder.forward_image(
            chat_id, image_bytes, msg.caption, "image/jpeg"
        )
        if response:
            await self._tts.send_response(msg, response, voice_reply=False)

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward document to the agent backend."""
        msg = update.message
        chat_id = msg.chat_id
        self._sessions.extend(update.effective_user.id)

        doc = msg.document
        file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await file.download_as_bytearray())

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._forwarder.forward_document(
            chat_id, file_bytes, doc.file_name or "unknown", msg.caption
        )
        if response:
            await self._tts.send_response(msg, response, voice_reply=False)
