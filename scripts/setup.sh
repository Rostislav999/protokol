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

# Download pinned official OCR models. Never use an unverified partial download.
.venv/bin/python - <<'PY'
from hashlib import sha256
from pathlib import Path
import os
import urllib.request

commit = "87416418657359cb625c412a48b6e1d6d41c29bd"
checksums = {
    "ukr": "d59e53e2bded32f4445f124b4b00240fcac7e8044c003ab822ccb94f0b3db59b",
    "rus": "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
directory = Path(".local/tessdata")
directory.mkdir(parents=True, exist_ok=True)
for language, expected in checksums.items():
    target = directory / f"{language}.traineddata"
    if target.exists() and sha256(target.read_bytes()).hexdigest() == expected:
        print(f"OCR model {language}: verified")
        continue
    url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{commit}/{language}.traineddata"
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(30 * 1024 * 1024)
    if sha256(data).hexdigest() != expected:
        raise SystemExit(f"Checksum mismatch for {language}; model has not been installed")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    print(f"OCR model {language}: installed and verified")
PY

TESSDATA_PREFIX="$project_dir/.local/tessdata" tesseract --list-langs
printf '%s\n' 'Setup complete. Start with: ./scripts/run.sh'
