@echo off
REM Build the portable Windows desktop app (PyInstaller onedir; runs with NO
REM system Python needed). Windows-only — PyInstaller cannot cross-compile.
REM Output: dist\AniGamerDownloader\  (copy the whole folder to a USB stick = portable).
REM
REM Prereqs in .venv: pyinstaller + pywebview (+ pythonnet/clr, pulled in by
REM pywebview's WebView2 backend). See docs\DESKTOP_BUILD.md.
setlocal
cd /d "%~dp0.."

REM WebView2 Evergreen bootstrapper is bundled so a clean machine can install the
REM runtime on first launch. Fetch it if missing (Microsoft official, ~2MB).
set "WV2=shells\webview2\MicrosoftEdgeWebview2Setup.exe"
if not exist "%WV2%" (
	echo Downloading WebView2 bootstrapper...
	if not exist "shells\webview2" mkdir "shells\webview2"
	curl -L -o "%WV2%" "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
)

REM default --contents-directory is _internal: keeps the top folder clean
REM (only AniGamerDownloader.exe + data\ + your ffmpeg.exe), deps tucked in _internal\.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --name AniGamerDownloader --windowed ^
		--icon "web\static\img\AniGamerDownloader.ico" ^
	--collect-all webview --collect-all clr_loader --collect-all pythonnet --collect-all gevent ^
	--collect-submodules core --collect-submodules app --collect-submodules shells ^
	--hidden-import webview.platforms.edgechromium --hidden-import clr ^
	--add-data "web;web" --add-data "data\DanmuTemplate.ass;data" --add-data "%WV2%;webview2" ^
	--distpath dist --workpath build\pyi shells\desktop.py

echo.
echo ============================================================
echo Done. Portable app -^> dist\AniGamerDownloader\AniGamerDownloader.exe
echo Drop ffmpeg.exe beside the exe before downloading (see docs).
echo ============================================================
endlocal
