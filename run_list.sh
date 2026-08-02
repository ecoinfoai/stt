#!/usr/bin/env bash
# ---------------------------------------------------------------
#  Transcribe the media listed in a list file (.txt / .yaml).
#  Use this when you already have the files and want to pick a
#  subset or give some of them different options. For the full
#  download + transcribe run, use run_all.sh instead.
#
#    ./run_list.sh                      uses list.txt next to me
#    ./run_list.sh mylist.yaml
#    ./run_list.sh mylist.txt --dry-run
#    nohup ./run_list.sh mylist.txt --keep-going > run.log 2>&1 &
#
#  Media is looked up in the data/ folder next to this script.
#  Needs bash 4.4+ (any current distro).
# ---------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$HERE/_venv.sh"

LIST="${1:-$HERE/list.txt}"
if [ "$#" -gt 0 ]; then shift; fi

if [ ! -f "$LIST" ]; then
    echo "[ERROR] List file not found: $LIST" >&2
    echo "        Put list.txt next to this script, or pass one:" >&2
    echo "            ./run_list.sh mylist.yaml" >&2
    exit 1
fi

echo "[stt] list : $LIST"
echo "[stt] data : $HERE/data"
exec "${PY[@]}" "$HERE/batch_stt.py" \
    --list "$LIST" --base-dir "$HERE/data" "$@"
