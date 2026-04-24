# TODOS

GitHub Flow 마이그레이션(2026-04-24) 에서 의도적으로 deferred된 항목들. 각 항목은 독립 PR로 처리.

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
