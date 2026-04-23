# paperslice v8 배포 가이드 (Cross-platform)

Windows / macOS / Linux 모두를 대상으로 한 v8 배포 안내서. 원본(v8 zip) 안의
PowerShell 전용 절차를 bash 동등본과 함께 병기.

## v8에서 바뀐 것

1. **세로쓰기 PDF 감지** — detector가 평균 line당 글자수로 세로쓰기(일본·중국 신문) 자동 판정 → MinerU의 `-m txt` 버그(세로쓰기에서 글자 누락) 회피
2. **`[n/8]` 파이프라인 단계 로그** — 각 단계 완료 시점에 결과값+소요시간 순차 출력
3. **PyMuPDF 의존성 추가** — detector가 실제로 동작하려면 필요
4. **Dockerfile: pip → uv** — 빌드 5~10배 빠름

## v8 번들에 포함된 파일

```
DEPLOY_v8.md              ← 이 문서
Dockerfile                ← 완성본. 기존 Dockerfile 덮어쓰기
pyproject.toml            ← 완성본. 기존 pyproject.toml 덮어쓰기 (pymupdf 포함됨)
src/paperslice/
  ├── pdf_type_detector.py   ← v8 신규 (세로쓰기 감지 + DetectionResult)
  ├── pipeline.py            ← v8 수정 ([n/8] 단계 로그)
  ├── main.py                ← v7 그대로
  ├── schemas.py             ← v7 그대로
  ├── mineru_runner.py       ← v7 그대로
  └── diff_builder.py        ← v7 그대로
tests/
  ├── test_pdf_type_detector.py   ← v8 수정 (19개 테스트)
  └── test_diff_builder.py        ← v7 그대로
```

---

## 배포 절차

기존 설치가 `~/paperslice` (또는 `C:\Users\<you>\paperslice`)에 있고, 새 v8 소스가
repo 또는 zip으로 있다고 가정.

### macOS / Linux (bash)

```bash
# 0. repo를 내려받았다면 그대로 쓰면 됨. zip이면 풀기.
unzip -o paperslice_v8.zip -d /tmp/paperslice_v8

# 1. 소스 / 테스트 덮어쓰기
rsync -av --delete /tmp/paperslice_v8/src/paperslice/ ~/paperslice/src/paperslice/
rsync -av --delete /tmp/paperslice_v8/tests/         ~/paperslice/tests/

# 2. 기존 Dockerfile / pyproject.toml 백업 후 덮어쓰기
cd ~/paperslice
cp -f Dockerfile      Dockerfile.v7.bak      2>/dev/null || true
cp -f pyproject.toml  pyproject.toml.v7.bak  2>/dev/null || true
cp -f /tmp/paperslice_v8/Dockerfile     Dockerfile
cp -f /tmp/paperslice_v8/pyproject.toml pyproject.toml

# 3. pymupdf 들어갔는지 확인
grep -n 'pymupdf' pyproject.toml
# pyproject.toml:23:    "pymupdf>=1.24.0",

# 4. 포트 8000 쓰고 있는 기존 컨테이너 정리
docker ps --filter "publish=8000" --format "{{.ID}}" | xargs -r docker stop
docker rmi -f paperslice:latest 2>/dev/null || true

# 5. 재빌드 (uv 덕에 빠름)
./scripts/build.sh --corp-ca      # 사내망: 사내 CA 필요 시
# 또는
./scripts/build.sh                # 일반

# 6. 실행
./scripts/run_local.sh
# 또는 docker compose:
docker compose up --build
```

### Windows (PowerShell)

```powershell
# 1. zip 풀기
Expand-Archive -Force C:\Users\User-1\paperslice_v8.zip `
  -DestinationPath C:\Users\User-1\temp_paperslice_v8

# 2. 소스 코드 / 테스트 덮어쓰기
robocopy C:\Users\User-1\temp_paperslice_v8\src\paperslice `
         C:\Users\User-1\paperslice\src\paperslice /E /R:1 /W:1 /NFL /NDL
robocopy C:\Users\User-1\temp_paperslice_v8\tests `
         C:\Users\User-1\paperslice\tests /E /R:1 /W:1 /NFL /NDL

# 3. Dockerfile + pyproject.toml 백업 후 덮어쓰기
Copy-Item C:\Users\User-1\paperslice\Dockerfile     C:\Users\User-1\paperslice\Dockerfile.v7.bak     -Force
Copy-Item C:\Users\User-1\paperslice\pyproject.toml C:\Users\User-1\paperslice\pyproject.toml.v7.bak -Force
Copy-Item C:\Users\User-1\temp_paperslice_v8\Dockerfile     C:\Users\User-1\paperslice\Dockerfile     -Force
Copy-Item C:\Users\User-1\temp_paperslice_v8\pyproject.toml C:\Users\User-1\paperslice\pyproject.toml -Force

# 4. pymupdf 확인
Select-String -Path C:\Users\User-1\paperslice\pyproject.toml -Pattern "pymupdf"

# 5. 포트 8000 쓰는 기존 컨테이너 정리
cd C:\Users\User-1\paperslice
docker ps --filter "publish=8000" --format "{{.ID}}" | ForEach-Object { docker stop $_ }
docker rmi paperslice:latest -f

# 6. 재빌드
.\scripts\build.ps1 -CorpCa

# 7. 실행
.\scripts\run_local.ps1
# 또는 docker compose:
docker compose up --build
```

---

## 검증

### PyMuPDF가 제대로 설치됐는지

```bash
docker run --rm paperslice:latest python -c "import fitz; print('PyMuPDF OK')"
```

v8 정상 작동 시 로그에 이 줄이 나와야 함:
```
[1/8] PDF 타입 감지 완료 → method=ocr (auto 판별 → 평균 10582자/페이지, 1.1자/line < 5.0 (세로쓰기 의심 — 일본·중국 신문류)) [0.1s]
```

**아래 메시지가 나오면 실패** (이전 이슈 재현):
```
[1/8] PDF 타입 감지 완료 → method=ocr (auto 판별 → PyMuPDF 없음 (fallback)) [0.0s]
```

### 전체 `[n/8]` 로그 순서

```
Starting parse: document_id=... mode=auto ...
[1/8] PDF 타입 감지 완료 → ...
[2/8] MinerU 실행 완료 → ...
[3/8] Diff 보조 실행 완료 → ...
[4/8] 블록 Enrich 완료 → ...
[5/8] 이미지 저장 완료 → ...
[6/8] 블록 분류 완료 → ...
[7/8] 세그먼트 완료 → ...
[8/8] 응답 조립 완료 → ... [총 ...]
POST /parse 200 OK
```

---

## 추천 파라미터 조합

**일본 신문 PDF** (화학공업일보 등):
```
file=<pdf>
language=japan
reading_direction=rtl
mode=auto          ← detector가 알아서 ocr 선택
diff_report=false
```

**한국어 논문 / 영문 보고서**:
```
file=<pdf>
language=korean   # 또는 en
reading_direction=ltr
mode=auto          ← detector가 알아서 txt 선택
diff_report=false
```

---

## 문제 해결

### 빌드 실패: "Invalid statement (at line 1, column 1)"
pyproject.toml에 BOM(UTF-8 Byte Order Mark)이 붙었을 때. v8 번들은 BOM 없이 생성됨 — 그냥 번들의 것으로 덮어쓰면 해결.

### 빌드 실패: Dockerfile 10단계 근처에 `pymupdf not found`
pyproject.toml에 pymupdf 라인이 안 들어간 상태. 3단계 grep/Select-String 다시 실행해서 확인.

### `entrypoint.sh: /usr/bin/env: 'bash\r': No such file or directory`
Windows에서 체크아웃한 `.sh` 파일이 CRLF 라인엔딩으로 컨테이너에 들어간 경우.
이 저장소의 `.gitattributes`가 자동으로 처리하지만, 이미 섞여 들어갔다면:
```bash
# 한 번만 정규화
git add --renormalize .
git commit -m "chore: normalize line endings"
```

### Apple Silicon (M1/M2/M3) Mac에서 빌드는 되는데 실행이 이상함
MinerU는 amd64에서만 검증됨. `scripts/build.sh`는 자동으로 `--platform=linux/amd64`를 붙이므로 rosetta/qemu로 돌아감 (느리지만 동작).
GPU가 필요하면 Apple Silicon에서는 불가 → Linux+NVIDIA 호스트로 가세요.

### 컨테이너 안에서 직접 확인
```bash
docker run --rm paperslice:latest python -c "import fitz; print('PyMuPDF OK')"
```

### 되돌리기 (v7로 원복)
**bash**
```bash
cp -f Dockerfile.v7.bak     Dockerfile
cp -f pyproject.toml.v7.bak pyproject.toml
```
**PowerShell**
```powershell
Copy-Item .\Dockerfile.v7.bak     .\Dockerfile     -Force
Copy-Item .\pyproject.toml.v7.bak .\pyproject.toml -Force
```

### 단계별 소요시간 읽기
각 `[X/8]` 뒤의 `[XX.Xs]`로 어디서 시간 많이 쓰는지 바로 보임. MinerU가 전체의 95% 이상이 정상.
