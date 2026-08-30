"""Environment-derived settings.

Everything secret lives here and comes from the environment (i.e. from .env,
which is gitignored). Nothing in config/campaigns/*.yml may contain a secret —
those files are committed.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- paths -------------------------------------------------------------
    app_root: Path = Path("/app")
    config_dir: Path = Path("/app/config")
    assets_dir: Path = Path("/app/assets")
    data_dir: Path = Path("/app/data")
    out_dir: Path = Path("/app/out")
    models_dir: Path = Path("/app/models")

    # --- local LLM ---------------------------------------------------------
    ollama_host: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:14b-instruct-q4_K_M"
    ollama_model_fast: str = "qwen2.5:7b-instruct-q4_K_M"
    ollama_timeout: int = 600

    # --- content sources (secrets) -----------------------------------------
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "short-tale/0.1"
    pexels_api_key: str = ""

    # --- speech ------------------------------------------------------------
    tts_engine: str = "auto"  # auto | kokoro | piper
    tts_voice: str = "af_heart"
    whisper_model: str = "base.en"
    whisper_device: str = "auto"  # auto | cuda | cpu

    # --- render ------------------------------------------------------------
    video_encoder: str = "auto"  # auto | nvenc | cpu
    video_fps: int = 30
    video_width: int = 1080
    video_height: int = 1920

    # --- app ---------------------------------------------------------------
    app_token: str = ""
    log_level: str = "INFO"
    tz: str = "UTC"
    publisher_url: str = "http://publisher:8090"
    publish_enabled: bool = True

    @field_validator("reddit_user_agent")
    @classmethod
    def _ua_must_be_descriptive(cls, v: str) -> str:
        # Reddit rejects generic user agents and will 429 you into the ground.
        return v or "short-tale/0.1"

    # --- convenience -------------------------------------------------------
    @property
    def has_reddit_credentials(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shorttale.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.out_dir, self.models_dir, self.work_dir):
            d.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict:
        """Safe-to-log view. Used by `shorttale doctor` and the /healthz route."""
        def mask(v: str) -> str:
            if not v:
                return "(unset)"
            return f"set ({len(v)} chars, ends …{v[-3:]})"

        return {
            "ollama_host": self.ollama_host,
            "ollama_model": self.ollama_model,
            "reddit_client_id": mask(self.reddit_client_id),
            "reddit_client_secret": mask(self.reddit_client_secret),
            "pexels_api_key": mask(self.pexels_api_key),
            "app_token": mask(self.app_token),
            "tts_engine": self.tts_engine,
            "whisper_model": self.whisper_model,
            "video_encoder": self.video_encoder,
        }


#: Path settings and the env var that overrides each one.
_PATH_FIELDS = {
    "config_dir": "CONFIG_DIR",
    "assets_dir": "ASSETS_DIR",
    "data_dir": "DATA_DIR",
    "out_dir": "OUT_DIR",
    "models_dir": "MODELS_DIR",
}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    if s.app_root.exists():
        return s

    # Running outside the container (tests, local dev): rebase the defaults
    # onto SHORTTALE_ROOT. An explicitly set env var still wins — otherwise
    # pointing just DATA_DIR somewhere else would be silently ignored, since
    # keyword arguments outrank the environment in pydantic-settings.
    root = Path(os.environ.get("SHORTTALE_ROOT", Path.cwd()))
    overrides = {"app_root": root}
    for field, env_name in _PATH_FIELDS.items():
        explicit = os.environ.get(env_name)
        overrides[field] = Path(explicit) if explicit else root / field.removesuffix("_dir")
    return Settings(**overrides)
