# INSTALL

Setup guide for the stt pipeline. Downloading needs `ffmpeg`
(yt-dlp uses it to extract audio). GPU transcription needs NVIDIA
cuBLAS for CUDA 12 and cuDNN 9 (faster-whisper/CTranslate2
requirement). CPU-only transcription needs none of that.

## 1. Windows 11

### 1-1. Install uv

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

(or `pip install uv` into any existing Python.)

### 1-2. Install dependencies

From the repository folder:

```powershell
uv sync
```

This creates `.venv/` and installs faster-whisper, yt-dlp and
PyYAML. uv downloads a suitable Python automatically if none is
present.

### 1-3. Install ffmpeg

```powershell
winget install Gyan.FFmpeg
```

Open a new terminal afterwards so `ffmpeg` is on `PATH`, then check
with `ffmpeg -version`. Only `fetch.py` needs it; transcription of
files you already have works without it.

### 1-4. GPU libraries (cuBLAS + cuDNN 9)

1. Open https://github.com/Purfview/whisper-standalone-win/releases/tag/libs
2. Under **Assets**, download the newest
   `cuBLAS.and.cuDNN_CUDA12_win_*.7z` (v3 = cuBLAS 12.8 + cuDNN
   9.8; avoid CUDA11 and cuDNN 8.x bundles)
3. Extract and copy all DLL files directly into `.venv\Scripts\`
   (next to `python.exe` — not into a subfolder)

Alternative: install cuDNN 9 for CUDA 12 from NVIDIA's official
site and ensure the DLLs are on PATH.

### 1-5. Verify

```powershell
nvidia-smi
uv run python -c "import ctranslate2; print('GPU:', ctranslate2.get_cuda_device_count())"
```

`GPU: 1` means the GPU is visible. DLL loading is finally verified
on the first real run (a clear error message points here if the
DLLs are missing).

### 1-6. Run

```powershell
copy urls_example.txt urls.txt
notepad urls.txt
run_all.bat urls.txt
```

For files you already have, transcribe directly:

```powershell
uv run transcribe.py "data\lecture.m4a" --terms terms_example.txt
```

Or drag & drop media files onto `transcribe.bat`.

## 2. Linux

### GPU machines

```sh
uv sync --extra cuda    # installs nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels
export LD_LIBRARY_PATH=$(uv run python -c 'import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ":" + os.path.dirname(nvidia.cudnn.lib.__file__))')
uv run transcribe.py data/lecture.mp4
```

`run_all.sh` and `run_list.sh` do this export for you (see
`_venv.sh`), so nothing extra is needed when you go through them.
For direct `uv run` calls, put the export into your shell profile
or the service unit that runs the job. On NixOS, the equivalent
goes into your shell.nix/flake devShell.

`ffmpeg` for downloads: `sudo apt install ffmpeg` (Debian/Ubuntu)
or the equivalent for your distribution.

### CPU-only servers

No NVIDIA libraries needed:

```sh
uv sync
uv run transcribe.py data/ --model small --beam 1
```

Re-running is safe: existing transcripts are skipped, which makes
folder-watching cron jobs trivial. The same holds for downloads —
`data/archive.txt` records what has already been fetched.

## 3. Model cache

First run downloads the model from Hugging Face into
`~/.cache/huggingface` (Windows: `%USERPROFILE%\.cache\huggingface`).
Approximate sizes: large-v3 3.1 GB, large-v3-turbo 1.6 GB,
small 0.5 GB. Interrupted downloads resume on retry.

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `cublas`/`cudnn` DLL error | DLLs must sit directly in `.venv\Scripts\` (Windows) or be on `LD_LIBRARY_PATH` (Linux) |
| `GPU: 0` | update the NVIDIA driver, re-check `nvidia-smi` |
| repeated out-of-memory | run with `--model small` or `--device cpu` (auto-fallback usually handles this) |
| too slow | `--beam 1` or `--model large-v3-turbo`; on CPU use `--model small` |
| "empty transcription" error | file may be silent; retry with `--language auto` |
| model download fails | check network and re-run (resumes) |
| `yt-dlp을(를) 찾지 못했습니다` | `uv sync`, or `uv run pip install -U yt-dlp` |
| `ffmpeg을(를) 찾지 못했습니다` | install ffmpeg (section 1-3 / Linux notes) and open a new terminal |
| YouTube asks to "confirm you're not a bot" | wait a few hours; add `--cookies-from-browser chrome`; avoid VPN/datacenter IPs |
| download stops partway | re-run — `data/archive.txt` makes it resume with the rest |

## 5. Legacy setup (plain venv)

A pre-existing venv keeps working without uv:

```powershell
python -m venv %USERPROFILE%\.venvs\stt
%USERPROFILE%\.venvs\stt\Scripts\python.exe -m pip install -r requirements.txt
```

`transcribe.bat` looks for `.venv\` in the repo first, then falls
back to `%USERPROFILE%\.venvs\stt`.
