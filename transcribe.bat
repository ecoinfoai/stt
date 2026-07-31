@echo off
rem ------------------------------------------------------------
rem  Drag & drop media files onto this .bat to transcribe them.
rem  Or run:  transcribe.bat "video.mp4" "folder"
rem  Uses .venv in this folder (uv sync), falling back to the
rem  legacy venv at %USERPROFILE%\.venvs\stt. See INSTALL.md.
rem ------------------------------------------------------------
chcp 65001 >nul
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\stt\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] No virtual environment found. Run "uv sync" first.
  echo         See INSTALL.md for setup.
  pause
  exit /b 1
)
"%PY%" "%~dp0transcribe.py" %*
pause
