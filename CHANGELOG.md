# Changelog

이 파일은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 포맷을 따릅니다.
프로젝트는 [Semantic Versioning](https://semver.org/spec/v2.0.0.html) 을 따릅니다.

## [Unreleased]

### Added
- GitHub Flow 워크플로우: `main` 브랜치를 유일한 trunk로, feature 브랜치는 `<type>/<slug>` 네이밍.
- `.github/workflows/ci.yml`: Python 3.10 / 3.12 × (ruff + mypy + pytest) + Docker 빌드 smoke.
- `CONTRIBUTING.md`: 브랜치 워크플로우, 커밋 메시지 규약, 릴리즈 절차.
- `.github/PULL_REQUEST_TEMPLATE.md`: PR 작성 체크리스트.
- `TODOS.md`: 이번 PR에서 의도적으로 deferred된 작업 목록.
- Dockerfile `BUILD_OFFLINE_TOLERANT` 빌드 ARG. 기본 `0` 은 프리베이크 필수(실패 시 빌드 실패), `1` 은 폐쇄망/CI 용 soft-fail.
- Dockerfile 런타임 기본값 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`. 베이크된 모델만 사용, hub ping 제거. 필요 시 `docker run -e HF_HUB_OFFLINE=0 ...` 로 override.
- `mineru_runner` 에 `_NETWORK_MARKERS` / `_looks_like_network_failure` / `_NETWORK_FAILURE_HINT` 분리. 네트워크 실패 시 vram 반감 재시도를 안 돌리고 즉시 명확한 해결 단계를 제시.
- `tests/test_mineru_error_classification.py` 15건: OOM vs 네트워크 분류가 다시 섞이지 않도록 박제.

### Fixed
- `src/paperslice/pipeline.py:136` 에서 정의되지 않은 `merge_chunk_outputs`를 호출하던 버그. 실제로는 `pdf_chunker` 에 존재하는 함수였고, import 문이 옛 이름 `merge_content_lists` 를 참조하고 있었음. 페이지 청킹 경로가 타는 순간 `NameError` 터지던 상태.
- Ruff lint 위반 5건: unused imports (`typing.Iterable`, `dataclasses`), `typing.Iterable` → `collections.abc.Iterable` 전환, `schemas.py` import 정렬.
- **Dockerfile 프리베이크 silent-fail 설계 결함 (#1).** 이전에는 `|| echo WARNING ... && true` 로 3개 fallback 전부 실패해도 빌드가 통과해서 "모델 없는 이미지"가 production 에 나갈 수 있었다. 기본값이 hard-fail + cache 검증으로 바뀌었고, 폐쇄망 빌드는 `--build-arg BUILD_OFFLINE_TOLERANT=1` 로 opt-in.
- **네트워크 실패가 OOM 으로 오분류되던 문제 (#3, #4, #7, #10).** `_OOM_MARKERS` 에 포함돼 있던 `remotedisconnected` / `connection reset` / `connection aborted` 가 실제로는 `huggingface.co` / `modelscope.cn` 연결 실패인 경우가 많았고, 이게 OOM 재시도(vram 반감)로 오인되어 무의미한 재시도 후 모호한 "MinerU exited with code 1" 만 전달됐다. 이제 네트워크 실패는 즉시 명확한 4단계 해결 hint 와 함께 실패.

### Changed
- Default branch: `claude/cross-platform-docker-fi22j` → `main`.
- CI `docker-build` job 은 이제 `BUILD_OFFLINE_TOLERANT=1` 로 빌드 — PR 마다 40분 걸리던 프리베이크를 skip, syntax + pip install 검증에 집중 (~3분). 릴리즈 빌드/사용자 로컬 빌드는 기본값 유지.

### Removed
- Stale AI-generated branches: `claude/fix-issues-readme-AvOMb`, `claude/optimize-cpu-performance-AiMcN`, `claude/cross-platform-docker-fi22j` (renamed to `main`).

## [0.1.0] - 2026-04-24

초기 릴리즈. 자세한 히스토리는 [`v0.1.0` 이전의 커밋](https://github.com/MustangYun/paper_slice/commits/v0.1.0) 참고.

### Added
- FastAPI 기반 newspaper PDF parser (paperslice).
- MinerU + PaddleOCR + PyMuPDF 오케스트레이션.
- Cross-platform Docker (macOS / Linux / Windows 동일 명령).
- v8: 세로쓰기 자동 감지, scanner vs digital PDF 분기.
- v9: CPU 자동 튜닝 (이슈 [#3](https://github.com/MustangYun/paper_slice/issues/3) / [#4](https://github.com/MustangYun/paper_slice/issues/4)), 페이지 청킹 (큰 PDF를 5페이지 단위로 분할 → OOM 회피), OOM 자동 재시도, MinerU 모델 프리베이크 (이슈 [#1](https://github.com/MustangYun/paper_slice/issues/1)), 기본 포트 8000 → 8100 (이슈 [#2](https://github.com/MustangYun/paper_slice/issues/2)).
- Provenance 보존 (페이지 번호 + bbox).
- OCR vs 텍스트-레이어 diff 모드.

[Unreleased]: https://github.com/MustangYun/paper_slice/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MustangYun/paper_slice/releases/tag/v0.1.0
