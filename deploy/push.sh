#!/bin/bash
# DRI Report Platform — 증분 배포 (git pull + 의존성 + alembic + restart)
# 로컬 → 서버. push 후 실행.
#
# 사용:
#   bash deploy/push.sh
#
# 환경변수 override:
#   DEPLOY_SERVER=ubuntu@cp.tech2.hybe.im
#   DEPLOY_DIR=/home/ubuntu/dri_report
#   DEPLOY_KEY=/home/jacob/repos/proj-k/jacob.pem

set -euo pipefail

SERVER="${DEPLOY_SERVER:-ubuntu@cp.tech2.hybe.im}"
REMOTE_DIR="${DEPLOY_DIR:-/home/ubuntu/dri_report}"
SERVICE_NAME="${SERVICE_NAME:-dri-report}"
KEY="${DEPLOY_KEY:-/home/jacob/repos/proj-k/jacob.pem}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

echo "=== [1/3] 로컬 git status 확인 ==="
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "  ⚠️  uncommitted changes 가 있음 — push 안 한 변경은 서버에 반영 안 됨."
    git status --short
    read -p "그래도 진행? [y/N] " yn
    [[ "$yn" =~ ^[Yy]$ ]] || exit 1
fi

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse @{u} 2>/dev/null || echo "no-upstream")
if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    echo "  ⚠️  local HEAD ($LOCAL_HEAD) ≠ upstream ($REMOTE_HEAD)"
    echo "     git push 먼저 하세요."
    exit 1
fi

echo "  ✓ local = upstream ($LOCAL_HEAD)"

echo ""
echo "=== [2/3] 서버에서 pull + 의존성 + alembic + restart ==="
ssh -i "$KEY" -o StrictHostKeyChecking=no "$SERVER" bash <<REMOTE
set -e
cd "$REMOTE_DIR"
git pull origin main

cd server
VIRTUAL_ENV=.venv uv pip install -e .
VIRTUAL_ENV=.venv uv pip install -e ../skill

HYBE_REPORTS_CONFIG=$REMOTE_DIR/config.toml .venv/bin/alembic upgrade head

sudo systemctl restart $SERVICE_NAME
sleep 2
sudo systemctl status $SERVICE_NAME --no-pager | head -8
echo ""
echo "최근 로그:"
tail -n 5 /var/log/dri-report.log
REMOTE

echo ""
echo "=== [3/3] 운영 헬스체크 ==="
curl -sk -o /dev/null -w "  GET /dri_report/auth/check : %{http_code}\n" \
    https://cp.tech2.hybe.im/dri_report/auth/check
# 401 이면 정상 (anonymous → 인증 필요)

echo ""
echo "=== 배포 완료 ==="
echo "  URL:   https://cp.tech2.hybe.im/dri_report/"
echo "  로그:  ssh -i $KEY $SERVER 'sudo journalctl -u $SERVICE_NAME -f'"
