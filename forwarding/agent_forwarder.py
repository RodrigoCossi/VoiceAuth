"""Forwards authenticated messages to the configured agent backend.

Uses an OpenAI-compatible /v1/chat/completions endpoint, so any backend that
speaks that protocol works here unmodified — a self-hosted LLM gateway, a
local model server (vLLM, Ollama behind a compatible proxy, LiteLLM), or a
hosted API. Conversation continuity is left to the backend, keyed by the
X-Session-Id header (set to the Telegram chat_id) so each chat gets its own
thread on the backend side.

Voice messages during active sessions are transcribed with faster-whisper
before forwarding. Images are forwarded as base64 multimodal content.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

_WHISPER_MODEL = None
_WHISPER_LOCK = None


async def transcribe(wav_bytes: bytes) -> Optional[str]:
    """Transcribe WAV bytes using faster-whisper (lazy-loaded, shared model)."""
    global _WHISPER_MODEL, _WHISPER_LOCK
    import asyncio

    if _WHISPER_LOCK is None:
        _WHISPER_LOCK = asyncio.Lock()

    async with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            try:
                from faster_whisper import WhisperModel
                _WHISPER_MODEL = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: WhisperModel("base", device="cpu", compute_type="int8"),
                )
                logger.info("faster-whisper model loaded.")
            except Exception as exc:
                logger.warning("Could not load faster-whisper: %s", exc)
                return None

    try:
        import io
        import asyncio
        buf = io.BytesIO(wav_bytes)

        def _run():
            segments, _ = _WHISPER_MODEL.transcribe(buf, beam_size=5)
            return " ".join(s.text.strip() for s in segments)

        return await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:
        logger.warning("Transcription failed: %s", exc)
        return None


class AgentForwarder:
    def __init__(self, config) -> None:
        self._url = config.agent_api_url.rstrip("/") + "/v1/chat/completions"
        self._key = config.agent_api_key
        self._model = config.agent_model_name

    def _headers(self, chat_id: int) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Session-Id": str(chat_id),
        }
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    async def forward_text(self, chat_id: int, text: str) -> Optional[str]:
        """Send a text message to the agent backend and return the response."""
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": text}],
        }
        return await self._call(chat_id, payload)

    async def forward_voice(self, chat_id: int, ogg_bytes: bytes, transcript: Optional[str] = None) -> Optional[str]:
        """Transcribe voice and forward the text. Pass a pre-computed transcript to skip re-transcription."""
        if transcript is None:
            from auth.voice_verifier import convert_ogg_to_wav
            try:
                wav = convert_ogg_to_wav(ogg_bytes)
            except Exception as exc:
                logger.error("Audio conversion failed: %s", exc)
                return "⚠️ Could not process voice message."
            transcript = await transcribe(wav)

        if transcript:
            content = f"[Voice message]: {transcript}"
        else:
            content = "[Voice message received — transcription unavailable. Please send text.]"

        return await self.forward_text(chat_id, content)

    async def forward_image(self, chat_id: int, image_bytes: bytes, caption: Optional[str], mime: str = "image/jpeg") -> Optional[str]:
        """Forward an image as multimodal content."""
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"
        content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        if caption:
            content.insert(0, {"type": "text", "text": caption})
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": content}],
        }
        return await self._call(chat_id, payload)

    async def forward_document(self, chat_id: int, file_bytes: bytes, filename: str, caption: Optional[str]) -> Optional[str]:
        """Forward a document. Text files are inlined; others are described."""
        mime, _ = mimetypes.guess_type(filename)
        if mime and mime.startswith("image/"):
            return await self.forward_image(chat_id, file_bytes, caption, mime)

        # Try to decode as text
        text_content: Optional[str] = None
        if mime and mime.startswith("text/"):
            try:
                text_content = file_bytes.decode("utf-8", errors="replace")
            except Exception:
                pass

        if text_content is not None:
            msg = f"[File: {filename}]\n{caption or ''}\n\n{text_content}"
        else:
            size_kb = len(file_bytes) // 1024
            msg = f"[File received: {filename}, {size_kb} KB]"
            if caption:
                msg += f"\n{caption}"

        return await self.forward_text(chat_id, msg)

    async def _call(self, chat_id: int, payload: dict) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._url,
                    json=payload,
                    headers=self._headers(chat_id),
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
                    body = await resp.text()
                    logger.error("Agent backend error %d: %s", resp.status, body[:200])
                    if resp.status == 401:
                        return "❌ Agent backend authentication failed. Check AGENT_API_KEY."
                    return f"❌ Agent backend returned status {resp.status}."
        except aiohttp.ClientConnectorError:
            logger.error("Cannot connect to agent backend at %s", self._url)
            return "❌ Cannot reach the agent backend. Is it running and reachable at AGENT_API_URL?"
        except Exception as exc:
            logger.error("Agent backend call failed: %s", exc)
            return f"❌ Error contacting the agent backend: {exc}"
