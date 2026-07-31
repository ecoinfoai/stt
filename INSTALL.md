# INSTALL

Setup guide for the stt transcription CLI. GPU execution needs
NVIDIA cuBLAS for CUDA 12 and cuDNN 9 (faster-whisper/CTranslate2
requirement). CPU-only use needs none of that.

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

This creates `.venv/` and installs faster-whisper. uv downloads a
suitable Python automatically if none is present.

### 1-3. GPU libraries (cuBLAS + cuDNN 9)

1. Open https://github.com/Purfview/whisper-standalone-win/releases/tag/libs
2. Under **Assets**, download the newest
   `cuBLAS.and.cuDNN_CUDA12_win_*.7z` (v3 = cuBLAS 12.8 + cuDNN
   9.8; avoid CUDA11 and cuDNN 8.x bundles)
3. Extract and copy all DLL files directly into `.venv\Scripts\`
   (next to `python.exe` — not into a subfolder)

Alternative: install cuDNN 9 for CUDA 12 from NVIDIA's official
site and ensure the DLLs are on PATH.

### 1-4. Verify

```powershell
nvidia-smi
uv run python -c "import ctranslate2; print('GPU:', ctranslate2.get_cuda_device_count())"
```

`GPU: 1` means the GPU is visible. DLL loading is finally verified
on the first real run (a clear error message points here if the
DLLs are missing).

### 1-5. Run

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

Put the `LD_LIBRARY_PATH` export into the shell profile or the
service unit that runs the job. On NixOS, the equivalent goes into
your shell.nix/flake devShell.

### CPU-only servers

No NVIDIA libraries needed:

```sh
uv sync
uv run transcribe.py data/ --model small --beam 1
```

Re-running is safe: existing transcripts are skipped, which makes
folder-watching cron jobs trivial.

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

## 5. Legacy setup (plain venv)

A pre-existing venv keeps working without uv:

```powershell
python -m venv %USERPROFILE%\.venvs\stt
%USERPROFILE%\.venvs\stt\Scripts\python.exe -m pip install -r requirements.txt
```

`transcribe.bat` looks for `.venv\` in the repo first, then falls
back to `%USERPROFILE%\.venvs\stt`.
