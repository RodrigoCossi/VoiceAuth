"""Speaker verification using SpeechBrain ECAPA-TDNN.

Loads the model once at startup and keeps it in memory.
All inference is run in a thread pool to avoid blocking the event loop.
A semaphore prevents concurrent inference on the same model instance.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class VoiceVerifier:
    def __init__(self, model_cache_dir: Path) -> None:
        self._model_dir = model_cache_dir / "speechbrain"
        self._model = None
        self._sem = asyncio.Semaphore(1)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def load(self) -> None:
        """Download (first run) and load the ECAPA-TDNN model."""
        self._loop = asyncio.get_running_loop()
        self._model_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Loading SpeechBrain ECAPA-TDNN model (may download ~80 MB on first run)…")
        await self._loop.run_in_executor(None, self._load_sync)
        logger.info("SpeechBrain model ready.")

    def _load_sync(self) -> None:
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier  # type: ignore[no-redef]

        self._model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(self._model_dir),
            run_opts={"device": "cpu"},
        )

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def get_embedding(self, wav_bytes: bytes) -> np.ndarray:
        """Extract a 192-dim speaker embedding from 16 kHz mono WAV bytes."""
        if not self._model:
            raise RuntimeError("Model not loaded")
        async with self._sem:
            return await self._loop.run_in_executor(None, self._embed_sync, wav_bytes)

    def _embed_sync(self, wav_bytes: bytes) -> np.ndarray:
        import torch
        import torchaudio

        buf = io.BytesIO(wav_bytes)
        waveform, sr = torchaudio.load(buf)

        if sr != 16000:
            waveform = torchaudio.functional.resample(waveform, sr, 16000)

        if waveform.shape[0] > 1:
            waveform = waveform.mean(dim=0, keepdim=True)

        with torch.no_grad():
            emb = self._model.encode_batch(waveform)  # (1, 1, 192)

        return emb.squeeze().cpu().numpy()

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))


def convert_ogg_to_wav(ogg_bytes: bytes) -> bytes:
    """Convert Telegram OGG/opus to 16 kHz mono WAV in memory."""
    from pydub import AudioSegment

    audio = AudioSegment.from_ogg(io.BytesIO(ogg_bytes))
    audio = audio.set_frame_rate(16000).set_channels(1)
    buf = io.BytesIO()
    audio.export(buf, format="wav")
    return buf.getvalue()
