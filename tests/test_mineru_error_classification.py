"""Regression tests for _looks_like_oom / _looks_like_network_failure 분리.

이슈 #3, #4, #7, #10 에서 ``huggingface.co`` / ``modelscope.cn`` 연결 실패가
``_OOM_MARKERS`` 에 포함된 "remotedisconnected" / "connection reset" 패턴으로
잡혀서 vram 반감 재시도를 돌았다. 이게 실제로는 네트워크 문제였기 때문에
재시도는 전부 실패했고, 사용자에게는 모호한 "MinerU exited with code 1" 만
전달됐다. 이 테스트는 두 분류가 다시는 섞이지 않도록 고정한다.
"""
from __future__ import annotations

from paperslice.mineru_runner import (
    _NETWORK_FAILURE_HINT,
    _looks_like_network_failure,
    _looks_like_oom,
)

# 실제 이슈에서 뽑아낸 stderr 샘플들. 문자열 매칭만 테스트하므로 전체 traceback
# 을 복붙할 필요는 없고, 핵심 host 문자열만 있으면 됨.
_ISSUE_3_TAIL = """
DocAnalysis init, this may take some times......
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 464, in _make_request
    self._validate_conn(conn)
    │    │              └ <HTTPSConnection(host='huggingface.co', port=443) at 0x72ca1d254440>
"""

_ISSUE_7_TAIL = """
Async task failed: dc13b2d0-ff0e-4df2-bdc9-0ab513690841
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/urllib3/connectionpool.py", line 464, in _make_request
    self._validate_conn(conn)
    │    │              └ <HTTPSConnection(host='www.modelscope.cn', port=443) at 0x74489bdfef90>
"""


class TestNetworkFailureDetection:
    def test_huggingface_connection_attempt_classified_as_network(self) -> None:
        assert _looks_like_network_failure(_ISSUE_3_TAIL)

    def test_modelscope_connection_attempt_classified_as_network(self) -> None:
        assert _looks_like_network_failure(_ISSUE_7_TAIL)

    def test_dns_failure_classified_as_network(self) -> None:
        stderr = "socket.gaierror: [Errno -3] Temporary failure in name resolution"
        assert _looks_like_network_failure(stderr)

    def test_ssl_verify_failure_classified_as_network(self) -> None:
        stderr = "ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"
        assert _looks_like_network_failure(stderr)

    def test_localentrynotfound_classified_as_network(self) -> None:
        # HF_HUB_OFFLINE=1 상태에서 캐시에 모델 없을 때 떠지는 에러
        stderr = "huggingface_hub.utils._errors.LocalEntryNotFoundError: ..."
        assert _looks_like_network_failure(stderr)

    def test_empty_stderr_not_classified_as_network(self) -> None:
        assert not _looks_like_network_failure("")

    def test_pure_oom_stderr_not_classified_as_network(self) -> None:
        stderr = "torch.cuda.OutOfMemoryError: CUDA out of memory"
        assert not _looks_like_network_failure(stderr)


class TestOomDetectionNoLongerIncludesNetwork:
    """이슈 #3, #4 의 원인이었던 오분류를 박제."""

    def test_huggingface_connection_is_not_oom(self) -> None:
        # 이전 버전에서는 False 가 아니라 True 였다 (_OOM_MARKERS 에
        # "connection reset" 등이 포함돼 있었음). 이 분류 변경이 이번 fix 의 핵심.
        assert not _looks_like_oom(_ISSUE_3_TAIL)

    def test_modelscope_connection_is_not_oom(self) -> None:
        assert not _looks_like_oom(_ISSUE_7_TAIL)

    def test_remotedisconnected_is_not_oom(self) -> None:
        stderr = "urllib3.exceptions.RemoteDisconnected: Remote end closed connection"
        assert not _looks_like_oom(stderr)

    def test_connection_reset_is_not_oom(self) -> None:
        stderr = "ConnectionResetError: [Errno 104] Connection reset by peer"
        assert not _looks_like_oom(stderr)

    def test_real_oom_still_detected(self) -> None:
        # vram 반감 재시도가 의미 있는 실제 OOM 시그니처는 유지.
        assert _looks_like_oom("torch.cuda.OutOfMemoryError: CUDA out of memory")
        assert _looks_like_oom("killed by signal 9 (SIGKILL)")
        assert _looks_like_oom("Worker was killed due to cannot allocate memory")


class TestNetworkFailureHintMessage:
    def test_hint_references_build_offline_tolerant(self) -> None:
        # 사용자가 이 에러를 보고 바로 어떻게 재빌드해야 하는지 알 수 있어야 함.
        assert "BUILD_OFFLINE_TOLERANT" in _NETWORK_FAILURE_HINT

    def test_hint_references_corp_ca_flag(self) -> None:
        assert "WITH_CORP_CA" in _NETWORK_FAILURE_HINT

    def test_hint_references_offline_env_override(self) -> None:
        assert "HF_HUB_OFFLINE" in _NETWORK_FAILURE_HINT
