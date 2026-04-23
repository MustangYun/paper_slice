# paper_slice

Paper Slice 프로젝트 저장소입니다.

## 개요

이 저장소는 논문/문서 처리용 도커 환경과 스크립트를 포함합니다.
원본은 Windows 환경에서 개발되었으며, 현재 브랜치에서 macOS / Linux 호환성 작업을 진행 중입니다.

## 구조 (예정)

```
.
├── docker/              # Dockerfile 및 도커 관련 설정
├── scripts/             # 실행 스크립트 (cross-platform)
│   ├── run.sh           # macOS / Linux
│   └── run.bat          # Windows
├── src/                 # 소스 코드
├── docker-compose.yml
└── README.md
```

## 브랜치

- `main` — 안정 버전
- `claude/cross-platform-docker-fi22j` — Windows 외 환경(macOS, Linux)에서도 실행되도록 수정 중인 브랜치
