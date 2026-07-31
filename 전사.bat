@echo off
rem ------------------------------------------------------------
rem  Drag & drop media files onto this .bat to transcribe them.
rem  Or run:  전사.bat "video.mp4" "folder"
rem  Requires the venv from the install guide:
rem    %USERPROFILE%\.venvs\stt
rem ------------------------------------------------------------
chcp 65001 >nul
set "PY=%USERPROFILE%\.venvs\stt\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv not found: %USERPROFILE%\.venvs\stt
  echo         See: 설치_사용_안내.md
  pause
  exit /b 1
)
"%PY%" "%~dp0transcribe.py" %*
pause
