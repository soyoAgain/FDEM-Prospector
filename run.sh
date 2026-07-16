#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-/Users/xiechushu/miniforge3/bin/python}"
export LANG="${LANG:-zh_CN.UTF-8}"
export LC_CTYPE="${LC_CTYPE:-zh_CN.UTF-8}"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
if [ ! -x "$PYTHON" ]; then
    echo "[错误] 未找到 Python: $PYTHON"
    exit 1
fi

echo "========================================"
echo "  FDEM 探测系统"
echo "========================================"
echo "[安全] ao0 的所有物理状态必须已用 DC 耦合示波器验证"
exec "$PYTHON" main.py
