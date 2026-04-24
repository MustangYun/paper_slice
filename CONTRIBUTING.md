# paperslice에 기여하기

2026년 4월부터 이 프로젝트는 **GitHub Flow**를 따릅니다. `main`이 유일한 long-lived 브랜치입니다.

## 브랜치 워크플로우

1. `main`에서 새 브랜치를 뗍니다.
   ```bash
   git checkout main
   git pull --ff-only
   git checkout -b <type>/<slug>
   ```
2. 브랜치 이름은 `<type>/<slug>` 형식:
   - `feature/` — 새 기능 (예: `feature/layout-detect-v10`)
   - `fix/` — 버그 수정 (예: `fix/ocr-timeout`)
   - `hotfix/` — main에 긴급 수정 (예: `hotfix/cve-2026-xxxx`)
   - `chore/` — 빌드/설정/리팩터 (예: `chore/ci-matrix`)
   - `docs/` — 문서만 (예: `docs/readme-korean`)
3. 작업 후 commit, push.
4. PR을 `main`으로 엽니다. PR 템플릿이 자동 populate됩니다.
5. CI가 통과하면 머지. **Squash merge 권장** — main 히스토리가 깔끔해집니다.
6. 머지 후 feature 브랜치 삭제 (GitHub UI에 버튼 뜸).

## 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/) 권장:

- `feat(scope): ...` — 새 기능
- `fix(scope): ...` — 버그 수정
- `docs: ...` — 문서만
- `chore: ...` — 빌드/설정
- `refactor: ...` — 동작 변경 없는 내부 개선
- `perf: ...` — 성능 개선
- `test: ...` — 테스트만
- Breaking change: 본문에 `BREAKING CHANGE:` 블록 추가 후 다음 릴리즈에서 MAJOR 번프

## 로컬 개발

```bash
# 최초 1회
pip install -e ".[dev]"

# PR 올리기 전에 매번
ruff check .
pytest
```

선택: `mypy src/paperslice`는 점진적 strict 전환 중이라 로컬에서 경고로 확인 (CI에서도 non-blocking).

## 릴리즈

릴리즈는 `main`의 특정 커밋에 SemVer 태그(`vMAJOR.MINOR.PATCH`) 를 찍는 것으로 합니다.

1. **CHANGELOG.md 업데이트 PR** — `[Unreleased]` 섹션 항목을 새 버전 섹션으로 이동.
2. 머지 후 `main`에서 태그:
   ```bash
   git checkout main && git pull --ff-only
   git tag -a v0.2.0 -m "릴리즈 요약"
   git push origin v0.2.0
   ```
3. [GitHub Releases](https://github.com/MustangYun/paper_slice/releases/new) 에서 릴리즈 노트 작성 (CHANGELOG 해당 섹션 그대로 붙여넣기).

SemVer 규칙:
- **MAJOR** — Breaking change. API 시그니처 변경, 설정 필드 제거 등.
- **MINOR** — 호환 가능한 기능 추가.
- **PATCH** — 버그 수정만.

## Branch protection

`main`은 보호되어 있습니다:
- 직접 push 불가 (PR만)
- CI (`lint-type-test`, `docker-build`) 통과 필수
- Force push 불가

이 설정을 변경하려면 레포 owner(MustangYun) 승인 필요.
