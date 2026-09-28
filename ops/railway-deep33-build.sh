#!/usr/bin/env bash
set -euo pipefail

SHA="${1:?missing DEEP33 git sha}"

rm -rf /app/deep33 /tmp/deep33src
mkdir -p /app/deep33 /tmp/deep33src

python - "$SHA" <<'PY'
import io
import pathlib
import shutil
import sys
import urllib.request
import zipfile

sha = sys.argv[1]
url = f"https://github.com/caggrometal-svg/DEEP33/archive/{sha}.zip"
archive = urllib.request.urlopen(url, timeout=30).read()
zipfile.ZipFile(io.BytesIO(archive)).extractall("/tmp/deep33src")
src = pathlib.Path(f"/tmp/deep33src/DEEP33-{sha}")
shutil.copytree(src, "/app/deep33", dirs_exist_ok=True)
print("DEEP33_SOURCE_READY", sha, pathlib.Path("/app/deep33/backend/main.py").stat().st_size)
PY

test -f /app/deep33/backend/requirements.txt
/app/.venv/bin/pip install --no-cache-dir -r /app/deep33/backend/requirements.txt
/app/.venv/bin/python -m py_compile   /app/deep33/backend/main.py   /app/deep33/backend/gateway.py   /app/deep33/backend/memory.py

echo "DEEP33_BUILD_PASS $SHA"
