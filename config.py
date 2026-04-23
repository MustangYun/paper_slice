"""Application configuration loaded from environment variables.

All runtime-configurable values live here so that individual modules don't
read os.environ directly. This makes the app easier to test and deploy.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MineruBackend(str, Enum):
    """MinerU parsing backend. Controls accuracy vs hardware requirements."""

    pipeline = "pipeline"  # CPU friendly, lower accuracy (~82+)
    vlm = "vlm"  # GPU required, high accuracy (~90+)
    hybrid = "hybrid"  # GPU required, balanced


class Settings(BaseSettings):
    """Global application settings loaded from environment variables.

    Override any field by setting PAPERSLICE_<FIELD_NAME_UPPER> in the env.
    Example: PAPERSLICE_DEFAULT_BACKEND=vlm
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPERSLICE_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- MinerU execution ---
    mineru_bin: str = Field(
        default="mineru",
        description="Path to the mineru CLI binary.",
    )
    default_backend: MineruBackend = Field(
        default=MineruBackend.pipeline,
        description="Default backend if the API request doesn't specify one.",
    )
    default_language: str = Field(
        default="japan",
        description="Default OCR language code passed to MinerU.",
    )
    strict_gpu: bool = Field(
        default=False,
        description=(
            "If true, requesting a GPU backend when no GPU is available "
            "raises an error. If false, falls back to pipeline with a warning."
        ),
    )

    # --- Paths ---
    # All produced artifacts (JSON, images) live under this root.
    # Mount this as a Docker volume for persistence.
    output_root: Path = Field(
        default=Path("/app/output"),
        description="Root directory for persisted parse outputs.",
    )
    # Scratch directory for MinerU intermediate files. Cleared per request.
    scratch_root: Path = Field(
        default=Path("/tmp/paperslice-scratch"),
        description="Temporary workspace for in-flight parses.",
    )

    # --- Server ---
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description="CORS allowed origins. Set to your frontend domain in prod.",
    )

    # --- Limits ---
    max_upload_mb: int = Field(
        default=100,
        description="Max PDF upload size in megabytes.",
    )
    mineru_timeout_sec: int = Field(
        default=1800,  # 30 minutes — first-run model download can be slow
        description="Timeout for a single MinerU invocation.",
    )


# Module-level singleton. Import this everywhere.
settings = Settings()
