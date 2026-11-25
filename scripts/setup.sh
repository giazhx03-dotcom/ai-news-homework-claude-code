#!/bin/bash
# 创建独立虚拟环境（仅标准库，无第三方依赖）
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT"
[ -x .venv/bin/python ] || /usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt 2>/dev/null || true
.venv/bin/python -c "import zoneinfo, sqlite3; print('环境就绪', __import__('sys').version.split()[0])"
