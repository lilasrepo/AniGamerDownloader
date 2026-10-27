@echo off
REM AniGamerDownloader — main launcher
REM - the ONE entry point: opens a standalone desktop window (pywebview / WebView2)
REM   hosting the dashboard, and runs the auto-download daemon + web UI in a hidden
REM   child process. No browser tab, no console window (pythonw).
REM - all controls live inside the window: Dashboard / Queue / Library / Settings (incl. account
REM   Cookie). Closing the window stops the daemon child.
REM - single-instance lock (daemon port 47763) prevents a second copy.
REM - runs from the project .venv (Python 3.13). If .venv is missing (a fresh clone
REM   or a new PC), it prints rebuild instructions (scripts\venv-missing.txt) instead
REM   of failing silently. See docs\SETUP_GUIDE.md.
chcp 65001 >nul
set PYTHONUTF8=1

REM Fail loudly with rebuild instructions when the venv is absent. The CJK message
REM lives in an external UTF-8 file shown via `type` — cmd's batch parser mangles
REM inline multibyte text, but `type` renders it correctly under chcp 65001.
if not exist "%~dp0.venv\Scripts\pythonw.exe" goto no_venv

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0shells\desktop.py"
goto :eof

:no_venv
type "%~dp0scripts\venv-missing.txt"
echo.
pause
exit /b 1
