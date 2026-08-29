"""Text-to-speech via Microsoft Edge TTS (edge-tts package).

Synthesises text → MP3 → OGG/Opus so it can be sent as a Telegram voice message.
Falls back to plain text when:
  - TTS is disabled
  - The response contains code blocks (code doesn't read well as speech)
  - Synthesis raises an exception

Voice selection is automatic: langdetect determines the language of each response
and selects the matching voice from the LANG_VOICES map. Unknown languages fall
back to the configured default voice.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

from telegram import Message

logger = logging.getLogger(__name__)

# Maps langdetect language codes → edge-tts voice names.
# Only languages that need a different voice from the default are listed.
LANG_VOICES: dict[str, str] = {
    "pt": "pt-BR-FranciscaNeural",
    "en": "en-US-AriaNeural",
}


def _has_code(text: str) -> bool:
    return "```" in text


def _detect_voice(text: str, default_voice: str) -> str:
    """Return the best edge-tts voice for *text*'s language."""
    try:
        from langdetect import detect
        lang = detect(text)
        return LANG_VOICES.get(lang, default_voice)
    except Exception:
        return default_voice


class TextToSpeech:
    def __init__(self, config) -> None:
        self._enabled: bool = config.tts_enabled
        self._voice: str = config.tts_voice

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def synthesize(self, text: str) -> Optional[bytes]:
        """Return OGG/Opus bytes for *text*, or None on failure."""
        try:
            import edge_tts
        except ImportError:
            logger.error("edge-tts not installed — cannot synthesize speech")
            return None

        voice = _detect_voice(text, self._voice)
        logger.debug("TTS voice selected: %s", voice)

        try:
            communicate = edge_tts.Communicate(text, voice)
            mp3_chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_chunks.append(chunk["data"])
            if not mp3_chunks:
                return None
            return self._mp3_to_ogg(b"".join(mp3_chunks))
        except Exception as exc:
            logger.warning("TTS synthesis failed: %s", exc)
            return None

    @staticmethod
    def _mp3_to_ogg(mp3_bytes: bytes) -> bytes:
        from pydub import AudioSegment
        audio = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        buf = io.BytesIO()
        audio.export(buf, format="ogg", codec="libopus")
        return buf.getvalue()

    async def send_response(self, msg: Message, text: str, voice_reply: bool = True) -> None:
        """
        Send the agent's reply back to the user.

        - voice_reply=False → always text (used when the user typed a message)
        - Code blocks       → text only  (unreadable as speech)
        - TTS disabled      → text only
        - Everything else   → voice message; text fallback if synthesis fails
        """
        if not text:
            return

        if not voice_reply or not self._enabled or _has_code(text):
            await _send_text(msg, text)
            return

        ogg = await self.synthesize(text)
        if ogg:
            await msg.reply_voice(ogg)
        else:
            await _send_text(msg, text)


async def _send_text(msg: Message, text: str) -> None:
    limit = 4096
    for i in range(0, len(text), limit):
        await msg.reply_text(text[i:i + limit])
