# TODOS

GitHub Flow 마이그레이션(2026-04-24) 및 후속 코드 점검에서 의도적으로 deferred된 항목들. 각 항목은 독립 PR로 처리.

---

## columns.py mypy flow narrowing (12건)

- **What:** `src/paperslice/utils/columns.py:148-177` 의 `positioned` 리스트 순회 시작에 `assert cb.block.bbox is not None` 추가. 135-141줄의 None 필터링을 mypy 가 flow-narrow 못 해서 12개 false positive 발생.
- **Why:** 지금 CI 는 `mypy || true` 로 무시 중이지만, mypy 를 blocking 하려면(후속 TODO) 이 false positive 부터 처리.
- **Pros:** mypy strict 로 가는 발판. 런타임 동작 변경 없음 (assert 는 optimized build 에서 제거).
- **Cons:** 패턴에 따라 `bbox = cb.block.bbox` 로컬 변수 + assert 조합이 더 깔끔할 수 있음.
- **Depends on:** None. 작은 PR 하나로 정리 가능.

## fitz (PyMuPDF) 타입 스텁

- **What:** `pyproject.toml` 에 다음 추가:
  ```toml
  [[tool.mypy.overrides]]
  module = "fitz"
  ignore_missing_imports = true
  ```
- **Why:** PyMuPDF 는 공식 타입 스텁 제공 안 함. `pdf_chunker.py:48 import fitz` 에서 mypy 에러. 위 override 로 침묵.
- **Pros:** mypy 전체 passes 에 한 발 더 가까워짐.
- **Cons:** fitz API 타입 검증 포기 — trade-off 는 받아들일 만함 (MinerU 가 fitz 를 통째로 래핑 중이라 우리는 직접 호출 적음).
- **Depends on:** None.

## Dockerfile.gpu 결정 (dangling reference)

- **What:** 현재 `scripts/build.sh --gpu` / `scripts/build.ps1 -Gpu` 가 `Dockerfile.gpu` 를 참조하는데 파일 없음. README 에는 "Dockerfile.gpu(CUDA, vlm/hybrid)" 적혀 있어 문서-현실 불일치.
- **선택지:**
  - (a) Dockerfile.gpu 작성 (CUDA base image, mineru[vlm] extra 로 변경)
  - (b) `--gpu` 플래그 제거 + README 수정
  - (c) 플래그 남기되 "not yet implemented" 로 명확히 exit 시키기
- **Why:** 사용자가 `--gpu` 쓰면 즉시 실패 ("Dockerfile.gpu: no such file"). 혼란 유발.
- **Pros (옵션 a):** README 약속 이행. GPU 사용자에게 실질 가치.
- **Cons (옵션 a):** CUDA base 크고 빌드 복잡. 우선순위 낮음.
- **Depends on:** GPU 타겟 사용자 유무 판단.

## docker-compose.yml 에 BUILD_OFFLINE_TOLERANT 노출

- **What:** `docker-compose.yml` 의 `build.args` 에 추가:
  ```yaml
  BUILD_OFFLINE_TOLERANT: ${BUILD_OFFLINE_TOLERANT:-0}
  ```
- **Why:** 현재 `BUILD_OFFLINE_TOLERANT=1` 을 쓰려면 `docker compose build --build-arg BUILD_OFFLINE_TOLERANT=1` 로 CLI 에서 명시해야 함. compose 에 노출하면 env 로 override 가능: `BUILD_OFFLINE_TOLERANT=1 docker compose build`.
- **Pros:** 폐쇄망 개발자 onboarding 개선. 1줄 추가.
- **Cons:** 없음.
- **Depends on:** None.

## scripts/ 에 BUILD_OFFLINE_TOLERANT 플래그

- **What:** `scripts/build.sh` / `scripts/build.ps1` 에 `--tolerate-offline` / `-TolerateOffline` 플래그 추가해서 `BUILD_OFFLINE_TOLERANT=1` 전달.
- **Why:** 스크립트가 `--corp-ca` 는 지원하는데 tolerant 는 없음. 사내망 사용자가 두 옵션 다 쓰려면 스크립트 우회 필요.
- **Pros:** 폐쇄망 workflow 일관성.
- **Cons:** 스크립트 4개 모두 수정 (bash/ps1 × build/run 2종).
- **Depends on:** compose 에 먼저 노출 여부.

## end-to-end prebake 검증 워크플로우 (on-demand)

- **What:** `.github/workflows/prebake-verify.yml` 을 `workflow_dispatch` 트리거로 새로 만듬. `secrets.CRT_FILE_TEST` 심고 `WITH_CORP_CA=1 BUILD_OFFLINE_TOLERANT=0` 으로 풀 빌드 → 성공 시 green.
- **Why:** PR #13 에 초기 포함됐다가 GHA 빌드 시간(1시간+) 문제로 뺐음. on-demand 로 분리하면 매 PR 비용 없이 필요 시에만 검증.
- **Pros:** 릴리즈 전 prebake 정상 동작 확인 가능. corp CA 경로 문서화.
- **Cons:** workflow_dispatch 는 수동 트리거라 잊기 쉬움. release 브랜치 정책 생길 때 자동화 검토.
- **Depends on:** None.

---

---

## Docker 이미지 GHCR 퍼블리싱

- **What:** `.github/workflows/ci.yml` 에 publish job 추가. `main` push 시 `ghcr.io/mustangyun/paperslice:main` 태그로, semver 태그 push 시 `:v0.1.0` 및 `:latest` 로.
- **Why:** 현재 사용자 onboarding은 `git clone && docker compose up --build` 로 5-15분 걸려 로컬 빌드. GHCR 이미지가 공개되면 `docker pull ghcr.io/mustangyun/paperslice:latest` 한 줄에 30초.
- **Pros:**
  - 사용자 first-request 체감 시간 대폭 단축.
  - 버전별 이미지 pin 가능 (`paperslice:v0.1.0`).
  - 폐쇄망 사내 환경도 mirror registry에 한 번 당겨두면 배포 용이.
- **Cons:**
  - GHCR packages 권한 설정 필요 (workflow의 `permissions: packages: write`).
  - 이미지 크기 주의 — MinerU pipeline 모델 프리베이크 포함 시 수 GB. Multi-stage 빌드 최적화 같이 필요할 수 있음.
- **Context:** `Dockerfile` 은 이미 cross-platform CPU 빌드로 구성되어 있음. Docker Hub 대신 GHCR 선택한 이유: GitHub 통합이 간결하고 public 이미지 pulls 무료.
- **Depends on:** GitHub Flow 마이그레이션 PR 머지 후.

---

## mypy strict mode 전환

- **What:** `pyproject.toml` 에 `[tool.mypy]` 섹션 추가, `strict = true` 로. 첫 번째 패스에서 터지는 에러 목록 만들고 incremental 하게 해결.
- **Why:** 현재 mypy는 설정 없이 돌아서 any 타입에 관대함. 타입 힌트의 실효 낮음. CI에서도 `|| true` 로 non-blocking.
- **Pros:**
  - 런타임 오류 사전 차단 (예: `merge_chunk_outputs` 같은 undefined name을 커밋 전에 잡음).
  - IDE autocomplete 향상.
- **Cons:**
  - MinerU / PaddleOCR / PyMuPDF 외부 라이브러리 타입 스텁이 부재 → `ignore_missing_imports` 등 세팅 필요.
  - 초기 에러가 수십 개 터질 가능성.
- **Depends on:** GitHub Flow CI 안정화 후.

---

## Pre-commit hooks

- **What:** `.pre-commit-config.yaml` 추가 (ruff + ruff-format). 선택: commitlint.
- **Why:** PR 올리기 전에 로컬에서 자동 포맷/린트. CI 실패로 인한 재작업 회피.
- **Pros:**
  - CI에서 lint 실패로 인한 PR 재push 루프 줄어듦.
  - 일관된 코드 스타일 강제.
- **Cons:**
  - 기여자가 `pre-commit install` 1회 실행 필요 (onboarding 단계 1개 추가).
- **Depends on:** None. 언제든지 추가 가능.

---

## Issue templates

- **What:** `.github/ISSUE_TEMPLATE/` 에 bug_report.md, feature_request.md 추가.
- **Why:** 현재 이슈 작성 가이드 없어서 재현 정보 놓치는 이슈가 있음.
- **Pros:** 더 구조화된 버그 리포트.
- **Cons:** 이슈가 많지 않으면 오버헤드.
- **Depends on:** 이슈 트래픽이 더 늘어나면.

---

## Dependabot

- **What:** `.github/dependabot.yml` 로 pip / docker 의존성 주 1회 자동 PR.
- **Why:** MinerU, PyMuPDF, PaddleOCR 등 핵심 의존성의 보안 패치 자동 추적.
- **Pros:** 보안 CVE 놓치지 않음.
- **Cons:** PR 노이즈. 2명 팀에 트리아지 부담 가능성. 적절한 `labels`, `schedule`, `open-pull-requests-limit` 설정 필요.
- **Depends on:** None.

---

## v0.1.0 git tag

- **What:** `main` 머지 직후 `v0.1.0` 태그 찍고 GitHub Release 생성.
  ```bash
  git checkout main && git pull --ff-only
  git tag -a v0.1.0 -m "Initial release: cross-platform Docker + v9 CPU 튜닝"
  git push origin v0.1.0
  ```
- **Why:** CHANGELOG `[0.1.0]` 섹션과 실제 태그 매핑. SemVer 시작점.
- **Depends on:** GitHub Flow 마이그레이션 PR 머지 후 **즉시**.

---

## main branch protection rule 활성화

- **What:** GitHub Settings → Branches → Add rule for `main`.
  ```
  Require a pull request before merging (approvals: 0, dismiss stale: yes)
  Require status checks: lint-type-test (3.10), lint-type-test (3.12), docker-build
  Do not allow force pushes
  Do not allow deletions
  ```
- **Why:** 플랜 결정 A3 (Light protection) 구현. CI 통과 없이 main 머지 불가.
- **Pros:** 실수 예방.
- **Cons:** 초기 1회 설정. CI가 한 번 돌고 나서 해야 check 이름이 잡힘.
- **Depends on:** 이 마이그레이션 PR이 머지되어 CI 이름이 최소 한 번 등록된 후. gh api로 자동화 가능:
  ```bash
  gh api -X PUT repos/MustangYun/paper_slice/branches/main/protection \
    --input protection.json
  ```
