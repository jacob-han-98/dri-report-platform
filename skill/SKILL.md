---
name: hybe-reports
description: |
  Claude Code 로 만든 HTML 리포트, 분석 결과물, 대시보드를 사내
  reports.hybe.internal 플랫폼에 배포하고 동료에게 공유 URL 을
  전달할 때 사용. "리포트 배포", "reports에 올려줘", "슬랙으로
  공유할 URL 만들어줘", "deploy report", "이 분석 공유하고 싶어"
  같은 요청에 발동. 권한 부여, 목록 조회, 회수, 다시 업로드도 지원.
---

# Hybe Reports Skill

## 사전 준비

첫 사용 시 `hybe-reports login` 실행 필요.
- 토큰 발급은 https://reports.hybe.internal/settings/tokens 에서 (지금은 dev 환경에서 `POST /api/tokens` 로 직접 발급).
- 토큰은 OS keyring 에 저장. keyring 사용 불가 시 `~/.config/hybe-reports/config.json` (chmod 600).
- 환경변수 `HYBE_REPORTS_TOKEN`, `HYBE_REPORTS_BASE_URL` override 가능.
- dev (self-signed TLS) 에선 `HYBE_REPORTS_VERIFY=0` 으로 verify 끄기.

## 주요 명령

### 배포

```bash
hybe-reports deploy [경로] --slug q4-revenue --title "Q4 매출 분석" \
  --visibility restricted --tag analytics --tag q4-2026
```

- 경로 미지정 시 현재 디렉토리.
- entry point 자동 탐지: `index.html` → 없으면 사용자에게 질문.
- `.git`, `node_modules`, `__pycache__`, `.DS_Store`, `*.pyc`, `.venv`, `venv` 자동 제외.
- slug 미지정 시 디렉토리명 + 날짜 (`my-analysis-2026-04-30`).
- 업로드 후 URL 을 stdout 과 (가능하면) 클립보드에 출력.

### 권한 관리

```bash
hybe-reports share q4-revenue --add sarah@hybecorp.com
hybe-reports share q4-revenue --remove mike@hybecorp.com
hybe-reports share q4-revenue --visibility internal
```

### 목록 / 조회

```bash
hybe-reports list                      # 내가 owner인 것 + 공유받은 것
hybe-reports list --mine               # 내 것만
hybe-reports list --shared-with-me     # 공유받은 것만
hybe-reports info q4-revenue           # 상세 정보
hybe-reports open q4-revenue           # 브라우저로 열기
```

### 다시 업로드 / 삭제

```bash
hybe-reports redeploy q4-revenue [경로]   # 메타 유지, 파일만 교체
hybe-reports delete q4-revenue
```

## 환경 변수

- `HYBE_REPORTS_BASE_URL` — 서버 URL (기본은 keyring 저장값)
- `HYBE_REPORTS_TOKEN` — 토큰 (keyring 보다 우선)
- `HYBE_REPORTS_VERIFY` — `0/false` 면 TLS 검증 끔 (dev 전용)
