# hybe-reports — Claude Code skill

Hybe Reports Platform 클라이언트 CLI. Claude Code 가 SKILL.md 의 description 으로 호출 시점을 판단해서 자동 실행.

## 설치

```bash
pip install -e ./skill
# 또는 (정식 배포 후)
pip install hybe-reports
```

## 사용

```bash
hybe-reports login                                 # 1회만
hybe-reports deploy /path/to/report-dir --slug q4
hybe-reports list
hybe-reports info q4
hybe-reports share q4 --add sarah@hybecorp.com
hybe-reports redeploy q4 /path/to/report-dir
hybe-reports delete q4
hybe-reports open q4
```

## 환경 변수

- `HYBE_REPORTS_BASE_URL` — 서버 URL override
- `HYBE_REPORTS_TOKEN` — 토큰 override
- `HYBE_REPORTS_VERIFY=0` — TLS 검증 끔 (dev self-signed 용)
