#!/bin/bash
# DRI Report Platform — 서버 첫 부트스트랩
# 대상: ubuntu@cp.tech2.hybe.im
# URL:  https://cp.tech2.hybe.im/dri_report/
#
# 전제:
#   - 서버에 git clone 완료: /home/ubuntu/dri_report/
#   - /home/ubuntu/dri_report/config.toml 작성됨 (deploy/config.prod.toml.example 참고)
#   - sudo 권한
#
# 멱등하게 동작 — 여러 번 돌려도 안전.

set -euo pipefail

APP_DIR="/home/ubuntu/dri_report"
SERVER_DIR="$APP_DIR/server"
VENV_DIR="$SERVER_DIR/.venv"
CONFIG_FILE="$APP_DIR/config.toml"
SERVICE_NAME="dri-report"

echo "=== DRI Report Platform · 첫 부트스트랩 ==="

# ── 1. 코드 / config 존재 확인 ──
echo "[1/7] 사전 조건 확인..."
[ -d "$SERVER_DIR" ] || { echo "  ❌ $SERVER_DIR 가 없음. git clone 먼저."; exit 1; }
[ -f "$CONFIG_FILE" ] || {
    echo "  ❌ $CONFIG_FILE 가 없음."
    echo "     cp $APP_DIR/deploy/config.prod.toml.example $CONFIG_FILE"
    echo "     vim $CONFIG_FILE  # secret_key, google.client_id/secret, admin_emails 채우기"
    exit 1
}
chmod 600 "$CONFIG_FILE"

# ── 2. uv 설치 (없으면) ──
echo "[2/7] uv 확인..."
if ! command -v uv >/dev/null 2>&1; then
    echo "  uv 설치..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

# ── 3. venv + 의존성 ──
echo "[3/7] Python 가상환경..."
cd "$SERVER_DIR"
if [ ! -d "$VENV_DIR" ]; then
    uv venv .venv --python 3.12
fi
VIRTUAL_ENV="$VENV_DIR" uv pip install -e .
VIRTUAL_ENV="$VENV_DIR" uv pip install -e ../skill

# ── 4. var dirs ──
echo "[4/7] 데이터 디렉토리..."
mkdir -p "$SERVER_DIR/var/reports/_meta"

# ── 5. alembic ──
echo "[5/7] DB 마이그레이션..."
HYBE_REPORTS_CONFIG="$CONFIG_FILE" "$VENV_DIR/bin/alembic" upgrade head

# ── 5b. skill wheel ──
echo "[5b] skill wheel 빌드..."
bash "$APP_DIR/deploy/build_skill_wheel.sh"

# ── 6. systemd ──
echo "[6/7] systemd 등록..."
sudo cp "$APP_DIR/deploy/systemd/dri-report.service" /etc/systemd/system/
sudo touch /var/log/dri-report.log
sudo chown ubuntu:ubuntu /var/log/dri-report.log
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

# ── 7. nginx snippet ──
echo "[7/7] nginx snippet..."
sudo cp "$APP_DIR/deploy/nginx/dri-report.conf" /etc/nginx/snippets/dri-report.conf
echo ""
echo "  → /etc/nginx/sites-enabled/community-pulse 의 server { } 블록 안에"
echo "     아래 라인이 추가되어야 합니다 (수동):"
echo "       include /etc/nginx/snippets/dri-report.conf;"
echo ""
echo "  추가 후:"
echo "       sudo nginx -t && sudo systemctl reload nginx"
echo ""

# 상태 확인
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager | head -12

echo ""
echo "=== 완료 ==="
echo "  URL:    https://cp.tech2.hybe.im/dri_report/"
echo "  로그:   sudo journalctl -u $SERVICE_NAME -f"
echo "          tail -f /var/log/dri-report.log"
echo "  헬스:   curl -s http://127.0.0.1:8092/healthz"
