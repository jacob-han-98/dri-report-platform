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

# uv build → dist/*.whl  (uv 가 없으면 python -m build 로 fallback)
if command -v uv >/dev/null 2>&1; then
    uv build --wheel --out-dir dist
elif [ -x "$VENV/bin/python" ]; then
    "$VENV/bin/python" -m pip install -q build
    "$VENV/bin/python" -m build --wheel --outdir dist
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
