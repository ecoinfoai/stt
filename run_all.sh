#!/usr/bin/env bash
# ---------------------------------------------------------------
#  Download (yt-dlp) + transcribe (faster-whisper) in one go.
#
#    ./run_all.sh                       uses urls.txt next to me
#    ./run_all.sh mylist.txt
#    ./run_all.sh mylist.txt --terms terms_example.txt
#    nohup ./run_all.sh mylist.txt > run.log 2>&1 &
#
#  Stage 1 writes "title [ID].m4a" and "title [ID].info.json" into
#  data/; stage 2 writes .txt and .meta.yaml beside them. Both
#  stages skip what is already done, so re-running is safe.
#  Extra options are passed to stage 2 only.
# ---------------------------------------------------------------
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_venv.sh"

URLS="${1:-$HERE/urls.txt}"
if [ "$#" -gt 0 ]; then shift; fi

if [ ! -f "$URLS" ]; then
    echo "[ERROR] URL list not found: $URLS" >&2
    echo "        Put urls.txt next to this script, or pass one:" >&2
    echo "            ./run_all.sh mylist.txt" >&2
    exit 1
fi

echo
echo "=== 1/2  downloading (yt-dlp) ==="
"${PY[@]}" "$HERE/fetch.py" --urls "$URLS" --out-dir "$HERE/data" || \
    echo "[warn] some downloads failed - continuing with what arrived"

echo
echo "=== 2/2  transcribing (faster-whisper) ==="
"${PY[@]}" "$HERE/transcribe.py" "$HERE/data" "$@"
CODE=$?

echo
if [ "$CODE" -eq 0 ]; then
    echo "[stt] done."
else
    echo "[stt] finished with errors (exit $CODE)"
fi
exit "$CODE"
