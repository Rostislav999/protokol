#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ ! -x .venv/bin/python ]]; then
  printf '%s\n' 'Run ./scripts/setup.sh first.' >&2
  exit 1
fi
if [[ -f "$project_dir/.local/tessdata/ukr.traineddata" ]]; then
  export TESSDATA_PREFIX="$project_dir/.local/tessdata"
fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PROTOKOL_PORT:-8000}"
