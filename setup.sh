#!/usr/bin/env bash
# aggregate-search 项目环境初始化:uv venv (Python 3.12) + 安装依赖
# 用法:在项目根目录执行 bash setup.sh(路径无关,任何位置解压均可)
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "[setup] uv 未安装。可先安装: pip install uv  (或改用手动方式见 README)" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  uv venv --python 3.12 .venv
fi

# Windows 是 .venv/Scripts/python.exe,Linux/macOS 是 .venv/bin/python
PY=".venv/Scripts/python.exe"
[ -f "$PY" ] || PY=".venv/bin/python"

uv pip install --python "$PY" -r requirements.txt -r requirements-dev.txt

"$PY" -c "import mcp, httpx; print('mcp ok'); print('httpx', httpx.__version__)"
echo "setup done —— 下一步:cp .env.example .env 并填入 key(流程见 KEYS.md)"
