@echo off
rem ---------------------------------------------------------------
rem  Download (yt-dlp) + transcribe (faster-whisper) in one go.
rem
rem    run_all.bat                        uses urls.txt next to me
rem    run_all.bat mylist.txt
rem    run_all.bat mylist.txt --terms terms_example.txt
rem
rem  Stage 1 writes "title [ID].m4a" and "title [ID].info.json"
rem  into data\; stage 2 writes .txt and .meta.yaml beside them.
rem  Both stages skip what is already done, so re-running is safe.
rem  Extra options (up to 9) are passed to stage 2 only.
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\stt\Scripts\python.exe"
if not exist "%PY%" goto :no_venv

if "%~1"=="" goto :default_list
set "URLS=%~1"
shift
goto :run

:default_list
set "URLS=%HERE%urls.txt"
goto :run

:run
if not exist "%URLS%" goto :no_list

echo.
echo === 1/2  downloading (yt-dlp) ===
"%PY%" "%HERE%fetch.py" --urls "%URLS%" --out-dir "%HERE%data"
if errorlevel 1 echo [warn] some downloads failed - continuing with what arrived

echo.
echo === 2/2  transcribing (faster-whisper) ===
"%PY%" "%HERE%transcribe.py" "%HERE%data" %1 %2 %3 %4 %5 %6 %7 %8 %9
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (echo [stt] done.) else (echo [stt] finished with errors ^(exit %CODE%^))
pause
exit /b %CODE%

:no_venv
echo [ERROR] No virtual environment found.
echo         Run "uv sync" in %HERE% first - see INSTALL.md.
pause
exit /b 1

:no_list
echo [ERROR] URL list not found: %URLS%
echo         Put urls.txt next to this script, or pass one:
echo             run_all.bat mylist.txt
pause
exit /b 1
