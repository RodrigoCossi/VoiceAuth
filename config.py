"""Configuration loader — reads .env and validates required fields."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    value = os.getenv(key, "").strip()
    if not value:
        print(f"ERROR: required env var {key} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass
class Config:
    # Telegram
    telegram_bot_token: str
    authorized_user_id: int

    # Voice auth
    similarity_threshold: float
    passphrase_match_threshold: float
    enrollment_samples_required: int

    # Session
    session_timeout_minutes: int
    session_warning_minutes: int

    # Ntfy
    ntfy_topic: str
    ntfy_server_url: str

    # Agent backend
    agent_api_url: str
    agent_api_key: str
    agent_model_name: str

    # TTS
    tts_enabled: bool
    tts_voice: str

    # Paths
    voiceprint_path: Path
    enrollment_dir: Path
    auth_log_db: Path
    pin_hash_path: Path
    passphrase_path: Path
    model_cache_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
            authorized_user_id=int(_require("AUTHORIZED_USER_ID")),
            similarity_threshold=float(_optional("SIMILARITY_THRESHOLD", "0.50")),
            passphrase_match_threshold=float(_optional("PASSPHRASE_MATCH_THRESHOLD", "0.50")),
            enrollment_samples_required=int(_optional("ENROLLMENT_SAMPLES_REQUIRED", "8")),
            session_timeout_minutes=int(_optional("SESSION_TIMEOUT_MINUTES", "30")),
            session_warning_minutes=int(_optional("SESSION_WARNING_MINUTES", "2")),
            ntfy_topic=_optional("NTFY_TOPIC"),
            ntfy_server_url=_optional("NTFY_SERVER_URL", "https://ntfy.sh"),
            agent_api_url=_optional("AGENT_API_URL", "http://localhost:8000"),
            agent_api_key=_optional("AGENT_API_KEY"),
            agent_model_name=_optional("AGENT_MODEL_NAME", "local-agent"),
            tts_enabled=_optional("TTS_ENABLED", "true").lower() in ("true", "1", "yes"),
            tts_voice=_optional("TTS_VOICE", "en-US-AriaNeural"),
            voiceprint_path=Path(_optional("VOICEPRINT_PATH", "./data/voiceprint.npy")),
            enrollment_dir=Path(_optional("ENROLLMENT_DIR", "./data/enrollment_samples/")),
            auth_log_db=Path(_optional("AUTH_LOG_DB", "./data/auth_log.db")),
            pin_hash_path=Path(_optional("PIN_HASH_PATH", "./data/pin_hash.txt")),
            passphrase_path=Path(_optional("PASSPHRASE_PATH", "./data/passphrase.txt")),
            model_cache_dir=Path(_optional("MODEL_CACHE_DIR", "./data/models/")),
        )
