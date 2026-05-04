# Hybe Reports Platform

사내 사용자가 Claude Code 로 만든 HTML 리포트를 안전하게 호스팅하고, 동료에게 URL 로 공유하며, MCP 를 통해 다른 Claude 인스턴스가 검색/조회할 수 있게 하는 플랫폼.

> 상세 스펙은 사용자 메시지의 기술 스펙 문서 참조. 실행 플랜: `~/.claude/plans/hybe-reports-platform-declarative-clock.md`.

## 레포 구조

```
dri_report_platform/
├── server/      # FastAPI 백엔드
├── skill/       # Claude Code skill (`hybe-reports` CLI)
├── deploy/      # Caddyfile, systemd, bootstrap
└── docs/
```

## 로컬 개발 (Slice 0~2)

```bash
# 1. 의존성 + DB 초기화
cd server
uv venv && source .venv/bin/activate
uv pip install -e .
alembic upgrade head

# 2. config 준비
cp ../deploy/config.example.toml ../config.toml
# config.toml 의 [dev] bypass_auth_email 를 본인 이메일로 설정

# 3. 백엔드 실행
HYBE_REPORTS_CONFIG=../config.toml uvicorn app.main:app --reload --port 8000

# 4. (선택) Caddy 실행 — forward_auth + 정적 서빙 통합
caddy run --config ../deploy/Caddyfile.dev
```

## 검증 시나리오

```bash
# Healthz
curl localhost:8000/healthz

# Auth check (bypass)
curl -i localhost:8000/auth/check

# 업로드 + 서빙 (Caddy 실행 중일 때)
echo '<h1>Hello</h1>' > /tmp/index.html
(cd /tmp && zip -r /tmp/r.zip index.html)
curl -k -X POST https://localhost/api/reports \
  -F "file=@/tmp/r.zip" \
  -F 'meta={"slug":"hello","title":"Hello","visibility":"internal"};type=application/json'
curl -k https://localhost/r/hello/
```
