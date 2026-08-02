#!/usr/bin/env bash
# ---------------------------------------------------------------
#  Shared helper sourced by run_all.sh and run_list.sh.
#  Sets PY (the interpreter command as an array) and, when the
#  NVIDIA wheels are installed, LD_LIBRARY_PATH for CTranslate2.
#  Expects HERE to be set by the caller. Not meant to be run.
# ---------------------------------------------------------------
if [ -z "${HERE:-}" ]; then
    echo "[ERROR] _venv.sh: HERE is not set (source me, don't run me)" >&2
    exit 1
fi

if [ -x "$HERE/.venv/bin/python" ]; then
    PY=("$HERE/.venv/bin/python")
elif command -v uv >/dev/null 2>&1; then
    PY=(uv run --project "$HERE" python)
else
    echo "[ERROR] No .venv and no uv found." >&2
    echo "        Run 'uv sync' in $HERE - see INSTALL.md." >&2
    exit 1
fi

CUDA_LIBS="$("${PY[@]}" - <<'PYEOF' 2>/dev/null || true
import os
try:
    import nvidia.cublas.lib as cublas
    import nvidia.cudnn.lib as cudnn
except ImportError:
    raise SystemExit(0)
print(":".join([
    os.path.dirname(cublas.__file__),
    os.path.dirname(cudnn.__file__),
]))
PYEOF
)"
if [ -n "$CUDA_LIBS" ]; then
    export LD_LIBRARY_PATH="$CUDA_LIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    echo "[stt] cuda : $CUDA_LIBS"
fi
