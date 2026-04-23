"""cpu_tuning 의 탐지·env 조립·재시도 마커 로직 회귀 테스트."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_detect_cache():
    """settings 가 파라미터에 따라 달라지는 것을 검증하려면 lru_cache 를
    매 테스트마다 초기화해야 한다."""
    from paperslice.cpu_tuning import detect_cpu_tuning

    detect_cpu_tuning.cache_clear()
    yield
    detect_cpu_tuning.cache_clear()


def test_detect_threads_are_clamped_into_sensible_range():
    from paperslice.cpu_tuning import detect_cpu_tuning

    tuning = detect_cpu_tuning()
    assert 2 <= tuning.threads <= 8
    assert tuning.source in {
        "config",
        "cgroup_v2",
        "cgroup_v1",
        "affinity",
        "cpu_count",
        "fallback",
    }


def test_build_mineru_env_injects_thread_caps_and_mineru_vars(monkeypatch):
    from paperslice.cpu_tuning import build_mineru_env, detect_cpu_tuning

    env = build_mineru_env()
    threads = str(detect_cpu_tuning().threads)
    for key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "TORCH_NUM_THREADS",
    ):
        assert env[key] == threads
    assert env["MINERU_DEVICE_MODE"] == "cpu"
    assert env["MINERU_VIRTUAL_VRAM_SIZE"] == "1"
    assert env["MINERU_FORMULA_ENABLE"] == "false"
    assert env["MINERU_TABLE_ENABLE"] == "true"
    assert env["TOKENIZERS_PARALLELISM"] == "false"


def test_build_mineru_env_does_not_mutate_os_environ():
    from paperslice.cpu_tuning import build_mineru_env

    before = os.environ.get("MINERU_VIRTUAL_VRAM_SIZE")
    build_mineru_env(extra={"MINERU_VIRTUAL_VRAM_SIZE": "99"})
    assert os.environ.get("MINERU_VIRTUAL_VRAM_SIZE") == before


def test_extra_overrides_win_over_defaults():
    from paperslice.cpu_tuning import build_mineru_env

    env = build_mineru_env(extra={"MINERU_VIRTUAL_VRAM_SIZE": "4"})
    assert env["MINERU_VIRTUAL_VRAM_SIZE"] == "4"


def test_oom_marker_detection():
    from paperslice.mineru_runner import _looks_like_oom

    assert _looks_like_oom("torch.cuda.OutOfMemoryError: ...")
    assert _looks_like_oom("Killed\n")
    assert _looks_like_oom("urllib3.exceptions.RemoteDisconnected: Remote end closed")
    assert _looks_like_oom("ConnectionResetError: [Errno 104] Connection reset by peer")
    assert _looks_like_oom("Worker was killed while processing request")
    assert _looks_like_oom("Cannot allocate memory")
    assert not _looks_like_oom("")
    assert not _looks_like_oom("ValueError: bad arg")
