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
    port: int = Field(
        default=8100,
        description=(
            "기본 서비스 포트. v9 에서 8000 → 8100 으로 변경 (이슈 #2). "
            "docker compose 의 호스트 측 매핑에 사용. 컨테이너 내부는 8100 고정. "
            "구버전 호환이 필요하면 `PAPERSLICE_PORT=8000 docker compose up` 으로 "
            "호스트 쪽만 8000 으로 노출 가능."
        ),
    )
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

    # --- CPU tuning (auto-applied at MinerU invocation) ---
    # v9: CPU-only 배포에서 메모리 터짐/스레드 폭주를 막기 위한 튜닝 파라미터.
    # None / 기본값이면 cpu_tuning.detect_cpu_tuning() 이 cgroup·affinity·cpu_count
    # 순으로 탐지해 [2, 8]로 클램프한 값을 사용한다.
    cpu_threads: int | None = Field(
        default=None,
        description=(
            "Thread cap applied to OMP/MKL/OpenBLAS/NUMEXPR/torch. "
            "None = auto (cgroup quota → sched_getaffinity → cpu_count, "
            "clamped to [2, 8])."
        ),
    )
    mineru_virtual_vram_gb: int = Field(
        default=1,
        description=(
            "Value passed as MINERU_VIRTUAL_VRAM_SIZE to MinerU. "
            "Controls its internal window_size/batch heuristic. 1 is safe "
            "for ~8GB RAM hosts; only raise if you have >=16GB free."
        ),
    )
    mineru_device_mode: str = Field(
        default="cpu",
        description="Passed as MINERU_DEVICE_MODE. 'cpu' forces CPU paths.",
    )
    mineru_model_source: str = Field(
        default="modelscope",
        description=(
            "Passed as MINERU_MODEL_SOURCE. 'modelscope' or 'huggingface'."
        ),
    )
    mineru_formula_enable: bool = Field(
        default=False,
        description=(
            "Passed as MINERU_FORMULA_ENABLE. Off by default on CPU — "
            "formula detection is expensive and rarely useful for newspapers."
        ),
    )
    mineru_table_enable: bool = Field(
        default=True,
        description="Passed as MINERU_TABLE_ENABLE.",
    )
    mineru_retry_on_oom: int = Field(
        default=1,
        description=(
            "Number of extra retries when MinerU stderr indicates OOM or "
            "connection reset. Each retry halves virtual_vram_gb (min 1)."
        ),
    )

    # --- Page-level chunking (v9) ---
    # 문서를 작은 페이지 묶음으로 쪼개 MinerU를 여러 번 호출하면 한 번의 피크
    # 메모리가 [chunk_size] 페이지 분량으로 줄어들어 OOM을 근본적으로 회피할 수
    # 있다. 결과 content_list는 page_idx를 오프셋해 합친다.
    chunk_pages: int = Field(
        default=5,
        description=(
            "Pages per MinerU invocation. Lower = smaller peak memory but "
            "more subprocess overhead. Set to 0 to disable chunking "
            "(legacy single-call mode)."
        ),
    )
    chunk_threshold_pages: int = Field(
        default=10,
        description=(
            "Only chunk when the PDF has strictly more than this many "
            "pages. Small PDFs skip the splitting overhead."
        ),
    )


# Module-level singleton. Import this everywhere.
settings = Settings()
