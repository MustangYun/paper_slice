# paperslice v9 배포 가이드 — CPU 자동 튜닝 + 페이지 청킹

v8 이후 운영 중 발견된 **CPU-only 환경에서의 OOM 크래시** 문제를 해결하기 위해 도입된
변경 사항과 배포 절차.

## 왜 v9 인가 — 해결한 4가지 근본 원인

v8 기준으로 119 페이지 PDF(이슈 #3)는 `mineru-api` 워커가 OOM killer 에 맞아 죽으며
`urllib3 _validate_conn connect()` 에러로 실패했고, 10 페이지 PDF 도 재발(#4)했습니다.
로그 상에는 네트워크 에러처럼 보이지만 **실제 원인은 메모리**입니다.

1. **스레드 폭주** — OMP / MKL / OpenBLAS / torch 스레드 캡이 하나도 설정돼 있지 않아
   8~16 vCPU 박스에서 DocAnalysis 모델 로딩 순간 수백 개 스레드가 동시에 메모리 잡음.
2. **MinerU 배치 기본값이 CPU 에 과함** — `GPU Memory: 1 GB, Batch Ratio: 1`
   로그 → `window_size=64` (64페이지 한 번에 추론). 거기에
   `Request concurrency limited to 3` 까지 겹쳐 피크 메모리 ×3.
3. **모델이 사전 다운로드 되지 않음** — 첫 요청에서 수 GB 모델을 런타임에 당겨오느라
   5~8 분 SLA 자체가 불가능.
4. **재시도 / 단계적 퇴행 없음** — subprocess 가 한 번 죽으면 그대로 실패 응답.

## v9 에서 바뀐 것

1. **CPU 자동 튜닝** — `src/paperslice/cpu_tuning.py` 가 기동 시 cgroup v2/v1 →
   `sched_getaffinity` → `cpu_count` 순으로 가용 코어를 탐지해 OMP/MKL/OpenBLAS/
   NUMEXPR/torch 스레드 수를 `[2, 8]` 로 클램프. 운영자가 `PAPERSLICE_CPU_THREADS`
   로 override 가능.
2. **페이지 단위 청킹** — `src/paperslice/pdf_chunker.py` 가 PyMuPDF 로 PDF 를
   `PAPERSLICE_CHUNK_PAGES` (기본 5) 단위로 잘라 MinerU 를 여러 번 호출.
   `PAPERSLICE_CHUNK_THRESHOLD_PAGES` (기본 10) 초과 시에만 활성화. 결과는 자동으로
   page_idx 오프셋 + 이미지 unique-prefix 복사로 병합.
3. **MinerU env 주입** — `subprocess.run(..., env=build_mineru_env())` 로
   `MINERU_DEVICE_MODE=cpu`, `MINERU_VIRTUAL_VRAM_SIZE=1`, `FORMULA_ENABLE=false` 등
   전달. `os.environ` 은 건드리지 않음.
4. **OOM 자동 재시도** — stderr 에서 `OutOfMemoryError` / `Killed` / `signal 9` /
   `RemoteDisconnected` / `ConnectionReset` / `Cannot allocate memory` 등을 감지하면
   `vram_gb //= 2` (최소 1) 후 재시도. `PAPERSLICE_MINERU_RETRY_ON_OOM` 횟수만큼.
5. **모델 프리베이크** — Dockerfile 이 빌드 중 `mineru-models-download` 로 pipeline
   모델을 `/home/paperslice/.cache` 에 받아둠. 네트워크 단절 빌드는 `|| echo WARNING`
   으로 이미지 자체는 성공.

## v9 번들에 포함된 파일

```
DEPLOY_v9.md              ← 이 문서
Dockerfile                ← 수정 (ENV 캡 + 모델 프리베이크)
src/paperslice/
  ├── cpu_tuning.py          ← v9 신규 — CPU 탐지 + env 조립
  ├── pdf_chunker.py         ← v9 신규 — PDF 분할 + 결과 병합
  ├── config.py              ← v9 수정 — 10개 필드 추가
  ├── main.py                ← v9 수정 — 기동 시 스레드 캡 적용
  ├── mineru_runner.py       ← v9 수정 — env 주입 + OOM 재시도
  └── pipeline.py            ← v9 수정 — 청킹 분기 + 진단 로그
tests/
  ├── test_cpu_tuning.py     ← v9 신규 (5 tests)
  └── test_pdf_chunker.py    ← v9 신규 (6 tests)
```

---

## 배포 절차

기존 v8 설치를 덮어쓰는 방식. 데이터 볼륨(`./output`, HF/ModelScope 캐시)은
그대로 유지됩니다.

### macOS / Linux (bash)

```bash
# 0. v9 소스를 clone 또는 zip 해제
git fetch origin
git checkout claude/optimize-cpu-performance-AiMcN
# 또는
unzip -o paperslice_v9.zip -d /tmp/paperslice_v9

# 1. 기존 컨테이너 정지
cd ~/paperslice
docker compose down

# 2. 파일 덮어쓰기 (zip 으로 받았을 때)
rsync -av /tmp/paperslice_v9/src/paperslice/ ~/paperslice/src/paperslice/
rsync -av /tmp/paperslice_v9/tests/          ~/paperslice/tests/
cp -f /tmp/paperslice_v9/Dockerfile     ~/paperslice/Dockerfile
cp -f /tmp/paperslice_v9/DEPLOY_v9.md   ~/paperslice/DEPLOY_v9.md

# 3. 새 모듈이 실제로 존재하는지 확인
ls src/paperslice/cpu_tuning.py src/paperslice/pdf_chunker.py

# 4. 재빌드 — 모델 프리베이크까지 들어가므로 시간 걸림
docker compose build --no-cache

# 5. 실행
docker compose up -d

# 6. 기동 로그 확인
docker compose logs paperslice | grep -E "CPU tuning|Uvicorn running"
# 기대 출력 예:
#   paperslice | INFO [paperslice.cpu_tuning] CPU tuning: threads=4 (source=cgroup_v2, cpu_count=8)
#   paperslice | INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Windows (PowerShell)

```powershell
# 0. v9 zip 해제
Expand-Archive -Force C:\Users\User-1\paperslice_v9.zip `
  -DestinationPath C:\Users\User-1\temp_paperslice_v9

# 1. 기존 컨테이너 정지
cd C:\Users\User-1\paperslice
docker compose down

# 2. 파일 덮어쓰기
robocopy C:\Users\User-1\temp_paperslice_v9\src\paperslice `
         C:\Users\User-1\paperslice\src\paperslice /E /R:1 /W:1 /NFL /NDL
robocopy C:\Users\User-1\temp_paperslice_v9\tests `
         C:\Users\User-1\paperslice\tests /E /R:1 /W:1 /NFL /NDL
Copy-Item C:\Users\User-1\temp_paperslice_v9\Dockerfile   .\Dockerfile   -Force
Copy-Item C:\Users\User-1\temp_paperslice_v9\DEPLOY_v9.md .\DEPLOY_v9.md -Force

# 3. 신규 모듈 확인
Get-ChildItem .\src\paperslice\cpu_tuning.py, .\src\paperslice\pdf_chunker.py

# 4. 재빌드
docker compose build --no-cache

# 5. 실행
docker compose up -d

# 6. 기동 로그 확인
docker compose logs paperslice | Select-String "CPU tuning|Uvicorn running"
```

---

## 검증

### 기동 로그에 CPU 튜닝 라인이 찍히는지

```bash
docker compose logs paperslice | grep "CPU tuning"
# 기대 출력:
#   CPU tuning: threads=4 (source=cgroup_v2, cpu_count=16)
```

`source` 가 `cgroup_v2` / `cgroup_v1` / `affinity` / `cpu_count` 중 하나여야 정상.
`config` 이면 `PAPERSLICE_CPU_THREADS` 를 명시 지정한 상태.

### env 가 서브프로세스로 주입되는지

```bash
docker exec $(docker compose ps -q paperslice) env | grep -E "^(OMP|MKL|OPENBLAS|MINERU)_"
# 기대 (값은 박스마다 다름):
#   OMP_NUM_THREADS=4
#   MKL_NUM_THREADS=4
#   OPENBLAS_NUM_THREADS=4
#   MINERU_DEVICE_MODE=cpu
#   MINERU_VIRTUAL_VRAM_SIZE=1
#   MINERU_MODEL_SOURCE=modelscope
#   MINERU_FORMULA_ENABLE=false
#   MINERU_TABLE_ENABLE=true
```

### 페이지 청킹이 도는지 (10 페이지 초과 PDF)

```bash
curl -s -X POST http://localhost:8000/parse \
  -F "file=@big_sample_119p.pdf" \
  -F "language=korean" -F "mode=auto" -o /dev/null

docker compose logs paperslice | grep -E "MinerU chunk|chunk 결과 병합"
# 기대 출력 예 (24 chunks):
#   MinerU chunk primary 1/24: pages 1-5 (5 pages)
#   MinerU chunk primary 1/24 완료: blocks=47 [12.3s]
#   ...
#   chunk 결과 병합: chunks=24, blocks=1234, images_copied=38
```

### 10 페이지 이하 PDF 는 청킹이 스킵되는지

```bash
curl -s -X POST http://localhost:8000/parse \
  -F "file=@small_8p.pdf" -o /dev/null

docker compose logs paperslice | grep -E "\[2/8\]" | tail -2
# 기대: "chunks=N" 로그 없음 — 1회 호출만.
```

### 단위 테스트 (호스트에서)

빌드 이미지에는 `pytest` 가 안 들어 있으므로 로컬 venv 에서 돌립니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
# 30 passed 확인 (cpu_tuning 5 + pdf_chunker 6 + 기존 19)
```

---

## 추천 파라미터 조합

### 8 GB / 4 vCPU 박스 (권장 기본)
```
# 모두 기본값 — 별도 설정 불필요
# 예상 성능: 119 페이지 ≤ 8 분
```

### 16 GB / 8 vCPU 박스 (throughput 중심)
```bash
docker run --rm -p 8000:8000 \
  --cpus=8 --memory=16g \
  -e PAPERSLICE_CPU_THREADS=6 \
  -e PAPERSLICE_MINERU_VIRTUAL_VRAM_GB=2 \
  -e PAPERSLICE_CHUNK_PAGES=10 \
  paperslice:latest
```

### 4 GB / 2 vCPU 극소형 박스
```bash
docker run --rm -p 8000:8000 \
  --cpus=2 --memory=4g \
  -e PAPERSLICE_CPU_THREADS=2 \
  -e PAPERSLICE_CHUNK_PAGES=3 \
  -e PAPERSLICE_MINERU_VIRTUAL_VRAM_GB=1 \
  -e PAPERSLICE_MINERU_FORMULA_ENABLE=false \
  paperslice:latest
```

### GPU 박스로 돌릴 때 (v9 기능 비활성화)
CPU 튜닝 / 청킹 은 GPU 에서도 무해하지만, 필요 시 비활성화:
```bash
docker run --rm -p 8000:8000 \
  --gpus all \
  -e PAPERSLICE_DEFAULT_BACKEND=vlm \
  -e PAPERSLICE_MINERU_DEVICE_MODE=cuda \
  -e PAPERSLICE_CHUNK_PAGES=0 \
  paperslice:latest
```

---

## 문제 해결

### `MinerU attempt 1/2 failed with OOM-like stderr` 이후 재시도 성공
정상 동작. `virtual_vram_gb` 가 반감된 상태로 2번째 시도가 통과한 것.
운영 관점에서는 `PAPERSLICE_CHUNK_PAGES` 를 낮춰 애초에 재시도가 안 일어나게 하는 편이 안정적.

### `CPU tuning: threads=1` 이 찍힘
cgroup 이 vCPU 1개만 할당. `--cpus=2` 이상으로 기동하거나 K8s
`resources.requests.cpu` / `resources.limits.cpu` 상향.

### `PyMuPDF 없음 — chunk 분할 불가, 원본 그대로 처리`
`pyproject.toml` 의 기본 의존성에서 `pymupdf` 가 빠졌을 때. v8 에서 이미 추가됐으므로
v9 에서는 발생하면 안 됨. 재빌드 후에도 나오면 `uv pip list | grep -i pymupdf`.

### `WARNING: MinerU 모델 프리베이크 실패` 가 빌드 로그에 남음
빌드 중 `modelscope` / `huggingface` 가 모두 막혀 모델 다운로드가 안 된 상태.
이미지 자체는 성공하므로 런타임 첫 요청에서 모델을 받습니다. 네트워크가 복구됐는지
확인 후 `docker compose build --no-cache` 재빌드 권장.

### 청킹이 도는데 오히려 느려짐
chunk 당 MinerU 콜드 시작 오버헤드가 크면 `PAPERSLICE_CHUNK_PAGES` 를 10 이상으로
올리세요. 메모리 여유만 있으면 호출 횟수가 절반으로 줄어 총 소요 시간 감소.

### `/documents/{id}/pages/{n}/blocks` 가 404 로 뜸
청킹된 요청의 debug raw 는 `document/raw/merged_content_list.json` 로 저장됩니다.
`main.py:_load_raw_content_list` 가 `*_content_list.json` glob 으로 찾으므로 문제
없어야 하지만, 혹시 실패하면 `output/<doc_id>/raw/` 디렉터리 확인.

### v8 로 되돌리기
```bash
git checkout <v8-commit-sha> -- Dockerfile src/paperslice/
rm src/paperslice/cpu_tuning.py src/paperslice/pdf_chunker.py
docker compose build --no-cache
```
단, v8 이후에 추가된 이슈(CPU OOM)는 다시 재현됩니다.

---

## 단계별 소요시간 읽기

각 `[X/8]` 뒤의 `[XX.Xs]` / `[Xm..s]` 로 어디서 시간 쓰는지 보입니다. CPU 모드에서는
`[2/8] MinerU 실행` 이 전체의 95% 이상을 차지하는 게 정상. 그 안에서 chunk 별
`MinerU chunk primary N/M 완료` 로그가 개별 chunk 시간을 알려줍니다 — chunk 간
편차가 크면 해당 페이지 범위의 이미지 밀도 차이가 원인인 경우가 대부분.
