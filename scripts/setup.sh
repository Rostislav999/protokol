#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
python_bin="${PROTOKOL_PYTHON:-python3}"

"$python_bin" -c 'import sys; sys.exit("Python 3.12+ is required") if sys.version_info < (3, 12) else None'
if ! command -v tesseract >/dev/null 2>&1; then
  printf '%s\n' 'Install Tesseract first (see README.md), then run this script again.' >&2
  exit 1
fi

if [[ ! -x .venv/bin/python ]]; then
  "$python_bin" -m venv .venv
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
else
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    .venv/bin/python -m ensurepip
  fi
  .venv/bin/python -m pip install --require-hashes -r requirements.lock
fi

# Share the verified OCR preparation with the Windows launcher.
.venv/bin/python scripts/prepare_ocr.py

TESSDATA_PREFIX="$project_dir/.local/tessdata" tesseract --list-langs
printf '%s\n' 'Setup complete. Start with: ./scripts/run.sh'
