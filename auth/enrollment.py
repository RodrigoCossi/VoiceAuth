"""Voice enrollment flow.

Collects N voice samples, extracts embeddings, saves the mean as voiceprint.npy.
Individual embeddings are also saved for potential re-calibration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_PHRASES = [
    # English (samples 1–5)
    "The quick brown fox jumps over the lazy dog",
    "I authorize this voice as my identity",
    "My voice is my password, verify me",
    "Open sesame, let me through the gate",
    "Security and privacy are my top priorities",
    # Brazilian Portuguese (samples 6–8)
    "Minha voz é minha senha, me verifique",
    "Autorizo esta voz como minha identidade",
    "Segurança e privacidade são minhas prioridades",
]


@dataclass
class _EnrollState:
    samples: list[np.ndarray] = field(default_factory=list)
    awaiting_passphrase: bool = False


class EnrollmentManager:
    def __init__(self, config, verifier) -> None:
        self._cfg = config
        self._verifier = verifier
        self._pending: dict[int, _EnrollState] = {}

    # ── State checks ──────────────────────────────────────────────────────────

    def voiceprint_exists(self) -> bool:
        p = self._cfg.voiceprint_path
        return p.exists() and p.stat().st_size > 0

    def is_enrolling(self, user_id: int) -> bool:
        state = self._pending.get(user_id)
        return state is not None and not state.awaiting_passphrase

    def is_awaiting_passphrase(self, user_id: int) -> bool:
        state = self._pending.get(user_id)
        return state is not None and state.awaiting_passphrase

    def passphrase_exists(self) -> bool:
        p = self._cfg.passphrase_path
        return p.exists() and p.stat().st_size > 0

    def load_passphrase(self) -> Optional[str]:
        p = self._cfg.passphrase_path
        if not p.exists():
            return None
        return p.read_text().strip() or None

    def save_passphrase(self, user_id: int, passphrase: str) -> str:
        p = self._cfg.passphrase_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(passphrase.strip())
        self.cancel(user_id)
        logger.info("Authentication passphrase saved to %s", p)
        return (
            "✅ Passphrase set. Enrollment complete!\n\n"
            "Say this passphrase when starting a new session. "
            "Both your voice and the passphrase will be verified."
        )

    def start(self, user_id: int) -> None:
        self._pending[user_id] = _EnrollState()

    def cancel(self, user_id: int) -> None:
        self._pending.pop(user_id, None)

    def sample_count(self, user_id: int) -> int:
        state = self._pending.get(user_id)
        return len(state.samples) if state else 0

    def next_phrase(self, user_id: int) -> str:
        n = self.sample_count(user_id)
        return _PHRASES[n % len(_PHRASES)]

    # ── Sample processing ─────────────────────────────────────────────────────

    async def add_sample(self, user_id: int, ogg_bytes: bytes) -> tuple[bool, str]:
        """
        Process one voice sample.
        Returns (done, message) — done=True when all required samples collected.
        """
        from auth.voice_verifier import convert_ogg_to_wav

        state = self._pending.get(user_id)
        if not state:
            return False, "Enrollment not started."

        try:
            wav = convert_ogg_to_wav(ogg_bytes)
            embedding = await self._verifier.get_embedding(wav)
        except Exception as exc:
            logger.error("Enrollment sample processing failed: %s", exc)
            return False, f"⚠️ Could not process audio: {exc}. Please try again."

        state.samples.append(embedding)
        collected = len(state.samples)
        required = self._cfg.enrollment_samples_required

        if collected < required:
            next_phrase = self.next_phrase(user_id)
            return False, (
                f"✅ Sample {collected}/{required} saved.\n\n"
                f"Now say: _{next_phrase}_"
            )

        # All samples collected — save voiceprint, then prompt for passphrase
        try:
            self._save(user_id, state.samples)
        except Exception as exc:
            logger.error("Failed to save voiceprint: %s", exc)
            self.cancel(user_id)
            return False, f"❌ Failed to save voiceprint: {exc}"

        state.awaiting_passphrase = True
        return False, (
            f"✅ {required} voice samples saved.\n\n"
            "Now type your authentication passphrase — the phrase you will need to "
            "say each time you start a new voice session:"
        )

    def _save(self, user_id: int, samples: list[np.ndarray]) -> None:
        voiceprint = np.mean(samples, axis=0)

        # Save mean voiceprint
        vp_path = self._cfg.voiceprint_path
        vp_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(str(vp_path), voiceprint)

        # Save individual embeddings
        enroll_dir = self._cfg.enrollment_dir
        enroll_dir.mkdir(parents=True, exist_ok=True)
        for i, emb in enumerate(samples):
            np.save(str(enroll_dir / f"sample_{i}.npy"), emb)

        logger.info("Voiceprint saved to %s", vp_path)

    def load_voiceprint(self) -> Optional[np.ndarray]:
        p = self._cfg.voiceprint_path
        if not p.exists():
            return None
        return np.load(str(p))
