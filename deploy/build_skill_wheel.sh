#!/bin/bash
# Skill wheel 빌드 → /home/ubuntu/dri_report/static/
#
# 결과: $STATIC_DIR/hybe_reports-<ver>-py3-none-any.whl
#       + $STATIC_DIR/hybe_reports-latest.whl (심볼릭 링크)
#
# pip install URL 예:
#   pip install --upgrade https://cp.tech2.hybe.im/dri_report/static/hybe_reports-latest.whl

set -euo pipefail

APP_DIR="/home/ubuntu/dri_report"
STATIC_DIR="$APP_DIR/static"
SKILL_DIR="$APP_DIR/skill"
VENV="$APP_DIR/server/.venv"

mkdir -p "$STATIC_DIR"

cd "$SKILL_DIR"
rm -rf dist/

# uv 먼저, 없으면 venv 의 hatchling 으로 직접 빌드 (pip 없어도 됨)
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"

if [ -n "$UV" ]; then
    "$UV" build --wheel --out-dir dist
elif [ -x "$VENV/bin/python" ]; then
    # hatchling 은 server venv 에 이미 들어있음 (build dep)
    "$VENV/bin/python" -m hatchling build -t wheel
    mkdir -p dist
    mv dist/*.whl dist/ 2>/dev/null || true
else
    echo "  ❌ uv 도 venv 도 없음. 빌드 불가."
    exit 1
fi

WHEEL=$(ls dist/*.whl | head -1)
[ -n "$WHEEL" ] || { echo "  ❌ 빌드 결과물 없음"; exit 1; }

cp "$WHEEL" "$STATIC_DIR/"
WHEEL_NAME=$(basename "$WHEEL")
ln -sf "$WHEEL_NAME" "$STATIC_DIR/hybe_reports-latest.whl"

echo "✓ $STATIC_DIR/$WHEEL_NAME"
echo "✓ $STATIC_DIR/hybe_reports-latest.whl → $WHEEL_NAME"
