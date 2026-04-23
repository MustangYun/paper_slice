"""CPU 코어 자동 탐지 및 MinerU 서브프로세스 env 조립.

v9에서 추가: CPU-only 배포 환경에서 OMP/MKL/OpenBLAS/torch 가 vCPU 전체를
점유해 스레드가 폭주하고 메모리가 터지는 현상을 막기 위한 모듈.

탐지 순서 (detect_cpu_tuning):
1. settings.cpu_threads 가 명시돼 있으면 그 값
2. cgroup v2 (/sys/fs/cgroup/cpu.max)
3. cgroup v1 (/sys/fs/cgroup/cpu/cpu.cfs_quota_us, cpu.cfs_period_us)
4. os.sched_getaffinity(0)
5. os.cpu_count()
→ 최종 값은 [_MIN_THREADS, _MAX_THREADS] 범위로 클램프.

os.environ 을 절대 직접 수정하지 않는다 (apply_in_process_thread_caps 예외).
서브프로세스에는 build_mineru_env() 가 만든 dict 사본을 env= 인자로 넘긴다.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache

from .config import settings

logger = logging.getLogger(__name__)

# 너무 적으면 parallelism 이익이 없고, 너무 많으면 BLAS 가 CPU-bound 에서 thrash.
# 경험적으로 MinerU pipeline 은 4 코어가 sweet spot, 8 이상에서는 수익 체감.
_MIN_THREADS = 2
_MAX_THREADS = 8


@dataclass(frozen=True)
class CpuTuning:
    """탐지된 CPU 튜닝 결과. 진단 로그용 메타데이터 포함."""

    threads: int
    source: str  # 'config' | 'cgroup_v2' | 'cgroup_v1' | 'affinity' | 'cpu_count' | 'fallback'
    raw_cpu_count: int


def _read_cgroup_v2_quota() -> int | None:
    """cgroup v2 (/sys/fs/cgroup/cpu.max) 로부터 가용 CPU 수 계산.

    포맷: "<quota> <period>"  (quota == "max" 이면 무제한)
    반환: ceil(quota / period) 또는 None
    """
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as f:
            raw = f.read().strip()
    except (OSError, FileNotFoundError):
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota = int(parts[0])
        period = int(parts[1])
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _read_cgroup_v1_quota() -> int | None:
    """cgroup v1 로부터 가용 CPU 수 계산."""
    try:
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", encoding="utf-8") as f:
            quota = int(f.read().strip())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", encoding="utf-8") as f:
            period = int(f.read().strip())
    except (OSError, ValueError, FileNotFoundError):
        return None
    if quota <= 0 or period <= 0:
        return None
    return max(1, math.ceil(quota / period))


def _read_affinity() -> int | None:
    """sched_getaffinity 는 Linux 에서만 제공되므로 hasattr 체크."""
    if not hasattr(os, "sched_getaffinity"):
        return None
    try:
        return len(os.sched_getaffinity(0))
    except OSError:
        return None


@lru_cache(maxsize=1)
def detect_cpu_tuning() -> CpuTuning:
    """CPU 스레드 캡과 그 출처를 계산. 프로세스 생애 1회만 수행."""
    raw = os.cpu_count() or _MIN_THREADS

    configured = settings.cpu_threads
    if configured is not None and configured > 0:
        return CpuTuning(
            threads=max(_MIN_THREADS, min(configured, _MAX_THREADS * 2)),
            source="config",
            raw_cpu_count=raw,
        )

    # 컨테이너 쿼터 먼저: Kubernetes/Docker --cpus 설정을 존중.
    for probe, label in (
        (_read_cgroup_v2_quota, "cgroup_v2"),
        (_read_cgroup_v1_quota, "cgroup_v1"),
        (_read_affinity, "affinity"),
    ):
        value = probe()
        if value is not None:
            clamped = max(_MIN_THREADS, min(value, _MAX_THREADS))
            return CpuTuning(threads=clamped, source=label, raw_cpu_count=raw)

    clamped = max(_MIN_THREADS, min(raw, _MAX_THREADS))
    return CpuTuning(threads=clamped, source="cpu_count", raw_cpu_count=raw)


# subprocess 에 주입할 스레드 캡 env 이름들.
# 하나라도 빠지면 해당 BLAS 는 여전히 모든 코어를 점유하므로 전부 설정.
_THREAD_ENV_KEYS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "NUMEXPR_MAX_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "TORCH_NUM_THREADS",
    "BLIS_NUM_THREADS",
)


def build_mineru_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """MinerU 서브프로세스에 넘길 env dict 생성.

    os.environ.copy() 로 사본을 만들고 그 위에 스레드 캡과 MinerU 전용 env 를
    덮는다. 호출자는 반드시 subprocess.run(..., env=return값) 형태로 사용.
    os.environ 은 변경하지 않는다.
    """
    tuning = detect_cpu_tuning()
    env = os.environ.copy()

    thread_str = str(tuning.threads)
    for key in _THREAD_ENV_KEYS:
        env[key] = thread_str
    # Tokenizers 는 fork 안전성 경고 + 내부 rayon 스레드 풀로 추가 부하 → 끄기.
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    # MinerU 튜닝 env. 이름은 MinerU 3.x 문서 기준.
    # (env 이름이 실제 배포 버전과 다르면 여기만 조정하면 됨.)
    env["MINERU_DEVICE_MODE"] = settings.mineru_device_mode
    env["MINERU_VIRTUAL_VRAM_SIZE"] = str(settings.mineru_virtual_vram_gb)
    env["MINERU_MODEL_SOURCE"] = settings.mineru_model_source
    env["MINERU_FORMULA_ENABLE"] = "true" if settings.mineru_formula_enable else "false"
    env["MINERU_TABLE_ENABLE"] = "true" if settings.mineru_table_enable else "false"

    if extra:
        env.update(extra)
    return env


def apply_in_process_thread_caps() -> None:
    """FastAPI 워커 프로세스 자체에 스레드 캡 적용.

    main.py 시작 시 가장 먼저 호출해야 함 — torch 가 처음 import 되는
    시점보다 앞서야 OMP/MKL 가 적은 스레드로 올라온다. setdefault 를 써서
    운영자가 미리 env 로 지정해둔 값이 있으면 존중.
    """
    tuning = detect_cpu_tuning()
    thread_str = str(tuning.threads)
    for key in _THREAD_ENV_KEYS:
        os.environ.setdefault(key, thread_str)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    try:
        import torch  # type: ignore[import-not-found]

        torch.set_num_threads(tuning.threads)
        torch.set_num_interop_threads(max(1, tuning.threads // 2))
    except Exception as e:
        logger.debug("torch 스레드 캡 설정 스킵: %s", e)

    logger.info(
        "CPU tuning: threads=%d (source=%s, cpu_count=%d)",
        tuning.threads,
        tuning.source,
        tuning.raw_cpu_count,
    )
