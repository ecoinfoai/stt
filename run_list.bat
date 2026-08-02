@echo off
rem ---------------------------------------------------------------
rem  Batch transcription from a list file (.txt / .yaml).
rem
rem    run_list.bat                       uses list.txt next to me
rem    run_list.bat mylist.yaml
rem    run_list.bat mylist.txt --dry-run
rem    run_list.bat mylist.txt --keep-going --srt
rem
rem  A list file dropped onto this .bat works too.
rem  Media is looked up in the data\ folder next to this script.
rem  Up to 9 extra options can follow the list file name.
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=%HERE%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\stt\Scripts\python.exe"
if not exist "%PY%" goto :no_venv

if "%~1"=="" goto :default_list
set "LIST=%~1"
shift
goto :run

:default_list
set "LIST=%HERE%list.txt"
goto :run

:run
if not exist "%LIST%" goto :no_list
echo [stt] list : %LIST%
echo [stt] data : %HERE%data
"%PY%" "%HERE%batch_stt.py" --list "%LIST%" --base-dir "%HERE%data" %1 %2 %3 %4 %5 %6 %7 %8 %9
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" echo [stt] finished with errors (exit %CODE%)
pause
exit /b %CODE%

:no_venv
echo [ERROR] No virtual environment found.
echo         Run "uv sync" in %HERE% first - see INSTALL.md.
pause
exit /b 1

:no_list
echo [ERROR] List file not found: %LIST%
echo         Put list.txt next to this script, or pass one:
echo             run_list.bat mylist.yaml
pause
exit /b 1
