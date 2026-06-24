#!/usr/bin/env bash
# 对练场生产启动（单 worker · SQLite WAL + 进程内任务注册表要求）
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m uvicorn sparring.api:app \
  --host 127.0.0.1 --port "${SPARRING_PORT:-8788}" \
  --workers 1 --log-level info
