"""Runs the MinerU CLI and returns its structured output.

This module is the only place that shells out to MinerU. If the MinerU
CLI changes, this is the single file that needs to follow along.

v7 변경점:
- run_mineru가 `method` 파라미터를 명시적으로 받음 (이전엔 하드코딩된 'ocr')
- pipeline.py에서 method를 결정해서 넘겨주는 구조로 바뀜

v9 변경점:
- 모든 호출에 cpu_tuning.build_mineru_env() 로 만든 env 를 주입
  (OMP/MKL/torch 스레드 캡 + MINERU_DEVICE_MODE=cpu + VIRTUAL_VRAM_SIZE)
- OOM / 연결 리셋 stderr 패턴이 감지되면 virtual_vram_gb 를 반감해 재시도
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MineruBackend, settings
from .cpu_tuning import build_mineru_env, detect_cpu_tuning

logger = logging.getLogger(__name__)


# Backends that require CUDA. Anything else runs on CPU.
GPU_BACKENDS: set[MineruBackend] = {MineruBackend.vlm, MineruBackend.hybrid}

# Methods accepted by the pipeline backend's -m flag.
_VALID_METHODS = {"auto", "ocr", "txt"}

# MinerU 서브프로세스가 OOM 으로 kill 될 때 stderr 에 남는 시그니처들.
# urllib3 의 RemoteDisconnected / ConnectionResetError 는 mineru-api 워커가
# 메모리 부족으로 죽었을 때 CLI 쪽에서 관측되는 증상.
_OOM_MARKERS: tuple[str, ...] = (
    "outofmemoryerror",
    "memoryerror",
    "killed",
    "signal 9",
    "sigkill",
    "remotedisconnected",
    "connectionreseterror",
    "connection aborted",
    "connection reset",
    "worker was killed",
    "cannot allocate memory",
)


def _looks_like_oom(stderr: str) -> bool:
    """stderr 에 OOM / 워커 사망 시그니처가 포함됐는지."""
    if not stderr:
        return False
    lowered = stderr.lower()
    return any(marker in lowered for marker in _OOM_MARKERS)


class MineruError(RuntimeError):
    """MinerU execution failed. Carries stdout/stderr for diagnosis."""

    def __init__(self, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass
class MineruResult:
    """What a successful MinerU run produces."""

    content_list: list[dict[str, Any]]
    raw_output_dir: Path
    backend_used: MineruBackend
    method_used: str  # v7: which -m was actually passed ('ocr'/'txt'/'auto'/'')


def _gpu_available() -> bool:
    """Check whether CUDA is usable from this process."""
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def resolve_backend(requested: MineruBackend) -> MineruBackend:
    """Translate the requested backend into the one we will actually run."""
    if requested not in GPU_BACKENDS:
        return requested
    if _gpu_available():
        return requested
    if settings.strict_gpu:
        raise MineruError(
            f"Backend '{requested}' requires GPU, but no CUDA device is available."
        )
    logger.warning(
        "Backend '%s' requested but no GPU is available. Falling back to 'pipeline'.",
        requested,
    )
    return MineruBackend.pipeline


def run_mineru(
    pdf_path: Path,
    output_dir: Path,
    backend: MineruBackend,
    language: str = "japan",
    method: str = "ocr",
) -> MineruResult:
    """Invoke the MinerU CLI and return its structured output.

    The CLI writes into `output_dir`. We read the content_list JSON back
    from there and surface both it and the directory path (so the caller
    can copy images out).

    Args:
        method: Only meaningful for the pipeline backend. One of:
            'ocr' - force OCR (legacy default; needed for scan PDFs)
            'txt' - force text-layer extraction (accurate for digital PDFs)
            'auto' - let MinerU decide
    """
    if method not in _VALID_METHODS:
        raise MineruError(
            f"Invalid method '{method}'. Must be one of {sorted(_VALID_METHODS)}."
        )

    resolved = resolve_backend(backend)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        settings.mineru_bin,
        "-p",
        str(pdf_path),
        "-o",
        str(output_dir),
        "-b",
        resolved.value,
        "-l",
        language,
    ]
    # -m is only accepted by the pipeline backend. vlm/hybrid handle this
    # internally, so we simply omit it there.
    method_used = ""
    if resolved is MineruBackend.pipeline:
        cmd += ["-m", method]
        method_used = method

    logger.info("Running MinerU: %s", " ".join(cmd))
    tuning = detect_cpu_tuning()
    attempts = max(1, settings.mineru_retry_on_oom + 1)
    vram_gb = max(1, settings.mineru_virtual_vram_gb)
    completed: subprocess.CompletedProcess[str] | None = None
    last_error: MineruError | None = None

    for attempt in range(1, attempts + 1):
        env = build_mineru_env(
            extra={"MINERU_VIRTUAL_VRAM_SIZE": str(vram_gb)},
        )
        logger.info(
            "MinerU attempt %d/%d: threads=%d vram_gb=%d device=%s",
            attempt,
            attempts,
            tuning.threads,
            vram_gb,
            settings.mineru_device_mode,
        )
        try:
            completed = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.mineru_timeout_sec,
                env=env,
            )
            break
        except subprocess.TimeoutExpired as e:
            # Timeout 은 메모리가 아니라 시간 문제 — 재시도해도 의미 없음.
            raise MineruError(
                f"MinerU timed out after {settings.mineru_timeout_sec}s",
            ) from e
        except subprocess.CalledProcessError as e:
            err = MineruError(
                f"MinerU exited with code {e.returncode}",
                stdout=e.stdout or "",
                stderr=e.stderr or "",
            )
            if attempt < attempts and _looks_like_oom(err.stderr):
                next_vram = max(1, vram_gb // 2)
                logger.warning(
                    "MinerU attempt %d failed with OOM-like stderr "
                    "(stderr tail: %s); retrying with vram_gb=%d",
                    attempt,
                    (err.stderr or "")[-400:],
                    next_vram,
                )
                vram_gb = next_vram
                last_error = err
                continue
            raise err from e

    if completed is None:
        # 이론상 도달 불가 — for-loop 이 성공 또는 raise 로 빠져나옴.
        raise last_error or MineruError("MinerU failed after retries")

    logger.debug("MinerU stdout: %s", completed.stdout[:500])
    content_list_path = _find_content_list(output_dir)
    if content_list_path is None:
        raise MineruError(
            "MinerU produced no content_list.json",
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    with content_list_path.open(encoding="utf-8") as f:
        content_list = json.load(f)

    return MineruResult(
        content_list=content_list,
        raw_output_dir=content_list_path.parent,
        backend_used=resolved,
        method_used=method_used,
    )


def _find_content_list(output_dir: Path) -> Path | None:
    """MinerU puts the content list JSON in a subdirectory per input file."""
    for path in output_dir.rglob("*_content_list.json"):
        return path
    return None


def get_mineru_version() -> str:
    """Return the installed mineru version string, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            [settings.mineru_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=build_mineru_env(),
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"
