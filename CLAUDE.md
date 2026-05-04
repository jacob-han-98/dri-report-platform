# Hybe Reports Platform

사내 InsightLab(~20명) 사용자가 Claude Code 로 만든 HTML 리포트를 Slack 첨부 대신 URL 로 공유하기 위한 플랫폼. MCP 통해 다른 Claude 인스턴스가 같은 토큰으로 검색/조회 가능.

전체 스펙은 첫 conversation 의 기술 스펙 문서, 진행 플랜은 `~/.claude/plans/hybe-reports-platform-declarative-clock.md`.

## 레포 구조

```
dri_report_platform/
├── server/      # FastAPI (Python 3.12), uv venv at server/.venv
├── skill/       # `hybe-reports` Claude Code CLI (pip install -e ./skill)
├── deploy/      # Caddyfile.dev, config.example.toml
└── docs/
```

## 로컬 dev 굴리기

```bash
# 1) 서버
cd server
HYBE_REPORTS_CONFIG=/home/jacob/repos/dri_report_platform/config.toml \
  .venv/bin/uvicorn app.main:app --port 8000 --host 127.0.0.1 &

# 2) Caddy (https://localhost:8443, tls internal, forward_auth)
~/.local/bin/caddy run --adapter caddyfile \
  --config /home/jacob/repos/dri_report_platform/deploy/Caddyfile.dev &
```

- 인증은 `config.toml` 의 `[dev] bypass_auth_email` 로 우회 (운영 OIDC 는 Slice 1B).
- DB: `server/var/reports/_meta/reports.db` (SQLite WAL).
- 리포트 파일: `server/var/reports/{slug}/`.
- API 문서: `https://localhost:8443/openapi.json`.

## 기본 정책

### UI/Frontend 변경은 Playwright 로 직접 검증한다

HTML 페이지, 카드뷰, 폼, 버튼, 모달, 라우팅 등 **사람이 브라우저로 보는 결과물** 을 만들거나 고치면, curl/HTTP 응답만 보고 끝내지 말고 **Playwright 로 실제 브라우저 렌더링 + 인터랙션을 확인**한다.

**구체적으로:**
- 페이지를 새로 만들면 → Playwright 스크립트로 (a) 페이지 로드 200, (b) 핵심 DOM 요소 visible, (c) 주요 클릭/제출이 기대대로 동작 — 최소 이 3개를 검증.
- self-signed TLS 환경: `ignore_https_errors=True`.
- 인증 우회: `bypass_auth_email` 가 켜져 있으니 별도 로그인 자동화 불필요. Bearer 가 필요한 흐름은 `Authorization` 헤더 주입.
- 검증 스크립트는 `server/tests/e2e/test_*.py` 에 두고 재실행 가능하게 작성.
- 실패하거나 의심스러우면 `page.screenshot()` 으로 `/tmp/` 에 저장, 사용자에게 경로 공유.
- "잘 됐어요" 라고 말하기 전에 실제로 Playwright 로 본 화면을 근거로 둔다.

설치:
```bash
VIRTUAL_ENV=server/.venv uv pip install playwright
server/.venv/bin/playwright install chromium
```

### 작업 시작 전 파일 단위 계획 한 번 더 보여주기

새 슬라이스 / 5+ 파일 변경 / 새 모듈 도입 시, 큰 플랜 승인된 상태에서도 **"파일 경로 + 한 줄 요약"** 표를 채팅에 펼치고 한 번 더 확인 받는다. 한 슬라이스 안에서 후속 파일 추가는 반복 안 해도 됨. (별도 메모리 `feedback_step_plan.md` 와 동일 정책)

## 진행 슬라이스

`~/.claude/projects/-home-jacob-repos-dri-report-platform/memory/project_overview.md` 참조.

## 자주 쓰는 명령

```bash
# 마이그레이션
cd server && HYBE_REPORTS_CONFIG=../config.toml .venv/bin/alembic upgrade head

# 서버 routes 확인
curl -sk https://localhost:8443/openapi.json | python3 -c \
  "import sys, json; d=json.load(sys.stdin); print('\n'.join(sorted(d['paths'].keys())))"

# 토큰 발급 (dev bypass 활성 상태)
curl -sk -X POST https://localhost:8443/api/tokens \
  -H "Content-Type: application/json" -d '{"name":"dev","expires_in_days":7}' | jq .token

# DB tail (sqlite3 CLI 없을 때)
cd server && .venv/bin/python -c \
  "from app.db import SessionLocal; from app.models import AuditLog; \
   db=SessionLocal(); [print(r.id, r.action, r.resource) for r in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(10)]"

# 백그라운드 정리
ss -tlnp 2>&1 | grep -E "8000|8443" | grep -oP 'pid=\K\d+' | xargs -r kill
```
