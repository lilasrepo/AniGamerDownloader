@echo off
REM AniGamerDownloader — main launcher
REM - the ONE entry point: opens a standalone desktop window (pywebview / WebView2)
REM   hosting the dashboard, and runs the auto-download daemon + web UI in a hidden
REM   child process. No browser tab, no console window (pythonw).
REM - all controls live inside the window: Settings (incl. account Cookie) / Monitor / Download list /
REM   Database / Manual tasks / Help. Closing the window stops the daemon child.
REM - single-instance lock (daemon port 47763) prevents a second copy.
REM - the old system-tray launcher is src\tray.py (kept for reference); dashboard.bat
REM   and run-anime.bat are retired in archived/.
chcp 65001 >nul
set PYTHONUTF8=1
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0shells\desktop.py"
