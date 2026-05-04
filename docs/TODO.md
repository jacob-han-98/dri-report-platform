# TODO — 배포 후 사용성 평가 다음에 진행

## 운영다지기 (Slice 9)

배포 후 실제 사용 패턴 보고 우선순위 정해서 진행.

- [ ] **Rate limit** (slowapi) — 토큰당 분당 60, MCP search 30/min, MCP fetch 60/min, 업로드 5/min (스펙 §11)
- [ ] **EC2 부트스트랩 스크립트** — Ubuntu 24.04 → uv + Python 3.12 + Caddy + systemd + 첫 alembic 까지 자동화
- [ ] **systemd unit** — `hybe-reports.service` (uvicorn workers 4), `hybe-reports-caddy.service`. 재시작 정책, journald 로깅
- [ ] **EBS 백업** — 일 1회 EBS snapshot (cron) + 주간 SQLite `.backup` → S3 lifecycle 30일 보존
- [ ] **HSTS preload** — `Strict-Transport-Security` 에 `preload` + `includeSubDomains` 추가, [hstspreload.org](https://hstspreload.org/) 등록
- [ ] **CSP / nonce** — 정적 리포트가 외부 리소스 로드 가능해야 하므로 `/r/*` 와 그 외 페이지에 다른 CSP. 작성자 신뢰 가정이지만 sandbox iframe 또는 slug-별 subdomain 검토
- [ ] **audit_logs 보존 정책** — config `[audit] retention_days = 365` 그대로, 일 1회 cron 으로 오래된 것 DELETE

## OAuth / 인증

- [ ] **OAuth consent screen Internal 로 전환** — Google Cloud Console → Audience → "Make Internal". 사내 hybecorp.com 도메인 자동 허용, 화이트리스트 관리 불필요
- [ ] **`allowed_domain = "hybecorp.com"` 활성화** — Internal 전환 후 config.toml 에서 도메인 강제. 비-hybe 계정 차단
- [ ] **세션 idle timeout** — 현재 7일 absolute timeout 만 있음. 활동 없으면 자동 만료 (예: 8시간 idle) 추가 검토
- [ ] **계정 삭제 (admin)** — disable 까지만 있음. 완전 삭제 + 소유 리포트 처리 정책 (delete vs reassign) 결정

## Polish (nice-to-have)

- [ ] **카드뷰 썸네일** — 업로드 시 Playwright headless 로 first-page screenshot → cache → 카드에 표시
- [ ] **리포트 파일 트리** — 상세 페이지에 storage 파일 리스트 (assets/, data.json 등) + 개별 파일 링크
- [ ] **리포트 버전 히스토리** — `redeploy` 시 이전 버전 보관 (선택), 롤백 가능
- [ ] **만료 임박 토큰 알림** — 7일 전 Slack/이메일 알림 (Slack 봇 별도 슬라이스)
