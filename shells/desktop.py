#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Windows portable desktop shell for AniGamerDownloader (CHILD-PROCESS model).
#
# WHY child-process (NOT in-process): pywebview's native window runs the
# WebView2 message loop on the MAIN thread and never yields to the gevent hub.
# A gevent WSGIServer running as a greenlet in the SAME process is therefore
# STARVED — the port binds but requests are never served, so the window shows a
# blank page forever. (Verified empirically: in-process curl to the loopback
# port times out with HTTP 000.) This is the exact reason src/tray.py used two
# processes. So this shell runs the gevent daemon + web UI in a clean CHILD
# process and the PARENT only opens a pywebview window pointing at it, tearing
# the child down when the window closes.
#
# Two modes, one file:
#   * parent (no args)   -> spawn the child, wait for its web port, open window.
#   * child (``--serve``) -> monkey-patch, inject ffmpeg, run the daemon + web.
#
# Portability model (genuinely USB-stick portable, NO F: assumption):
#   * core/config.py resolves data/ + web/ from the EXE folder when frozen
#     (`getattr(sys, 'frozen', False)` -> os.path.dirname(sys.executable)). In a
#     PyInstaller onedir build everything lives beside the exe.
#   * Output/temp default to data/bangumi + data/temp beside the exe when the
#     configured paths are missing (core/config.py read_settings fallback). The
#     user picks a real output folder from the GUI Settings page on first run.
#   * ffmpeg is located here (sibling exe next to the bundle first, then PATH)
#     and injected into the child daemon — dropping ffmpeg.exe beside
#     AniGamerDownloader.exe is enough, no install required.
#
# The window connects to the dashboard host:port from config (default
# 127.0.0.1:5000). The single-instance lock (daemon port 47763) still guarantees
# only one daemon runs; if one is already up, the child exits and the parent
# reports it. pywebview is imported lazily (parent only) with an actionable
# error — it is NOT a hard repo dependency. To run:  pip install pywebview.

import os
import sys
import socket
import platform
import subprocess
import threading
import time


def _project_root():
    # Frozen onedir: the exe folder IS the root (data/ + web/ sit beside the exe),
    # matching core/config.py's frozen branch. From source: parent of shells/.
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# Put the project root on sys.path so `import app` / `import core` resolve both
# from source (python shells/desktop.py) and when frozen (the onedir root).
_ROOT = _project_root()
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def locate_ffmpeg():
    # Shell-side ffmpeg probe: prefer a sibling exe next to the bundle/script
    # (PyInstaller onedir layout — ffmpeg.exe dropped beside AniGamerDownloader.exe),
    # then fall back to PATH. Returns a path/command string to inject into the
    # daemon, or None if neither is found.
    exe_name = 'ffmpeg.exe' if platform.system() == 'Windows' else 'ffmpeg'
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _ROOT
    sibling = os.path.join(base, exe_name)
    if os.path.isfile(sibling):
        return sibling
    import shutil
    return shutil.which('ffmpeg')


# --------------------------------------------------------------------------- #
# CHILD MODE: the gevent daemon + web UI. No pywebview is imported here.
# --------------------------------------------------------------------------- #
def _ensure_child_streams():
    # A windowed/pythonw/frozen parent can hand the child a None std stream (no
    # console); the daemon's print()/err_print would then crash. Point any
    # missing stream at the child log so output is captured, not fatal.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name, None) is None:
            try:
                lp = os.path.join(_ROOT, 'data', 'logs', 'desktop-child.log')
                os.makedirs(os.path.dirname(lp), exist_ok=True)
                setattr(sys, name, open(lp, 'a', encoding='utf-8'))
            except OSError:
                pass


def _serve_child():
    # monkey-patch BEFORE importing app/core (the engine's threading/socket/ssl
    # calls rely on being patched), exactly like the docker entry + src shims.
    from gevent import monkey
    monkey.patch_all()
    _ensure_child_streams()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

    from app import daemon

    # Inject the shell-located ffmpeg so the engine receives our resolved path
    # (daemon.locate_ffmpeg short-circuits on this cached value). If None, the
    # daemon's own probe runs and may raise at download time — harmless here.
    ffmpeg = locate_ffmpeg()
    if ffmpeg:
        daemon._ffmpeg_path = ffmpeg

    # Blocks: starts the web dashboard (run_dashboard -> app.web.server.run on
    # the configured host:port) + the auto-download loop. Acquires the
    # single-instance lock; returns False quickly if another instance holds it.
    daemon.run_daemon()


# --------------------------------------------------------------------------- #
# PARENT MODE: spawn the child, wait for its web port, open the window.
# --------------------------------------------------------------------------- #
def _dashboard_target():
    # Where the child will serve, read from the same config the child reads.
    from core import config as Config
    s = Config.read_settings()
    host = s['dashboard']['host']
    port = int(s['dashboard']['port'])
    if host in ('0.0.0.0', '::'):  # bound to all interfaces -> connect via loopback
        host = '127.0.0.1'
    return host, port


def _wait_port(host, port, timeout=30.0):
    # Bind-before-window: poll until the child's WSGIServer accepts a connection,
    # so the window's first paint never hits a connection-refused (blank page).
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def _import_webview():
    # Lazy import (parent only) so py_compile / import-smoke of this module does
    # not require pywebview. Raise an actionable error if it is missing.
    try:
        import webview  # noqa: F401
        return webview
    except ImportError as e:
        raise SystemExit(
            '缺少 pywebview 套件, 無法開啟桌面視窗。\n'
            '請先安裝: pip install pywebview\n'
            '(Windows 會使用 WebView2 後端; 乾淨機器需要 WebView2 Evergreen runtime, '
            '可攜版已綑綁 bootstrapper — 詳見 docs/DESKTOP_BUILD.md)'
        ) from e


def _terminate_child(child):
    # Kill the child daemon AND its descendants (e.g. a gost proxy subprocess).
    if child.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                       capture_output=True)
    else:
        child.terminate()
    try:
        child.wait(timeout=5)
    except Exception:
        try:
            child.kill()
        except Exception:
            pass


def _child_python():
    # Prefer console python.exe for the --serve child even when the PARENT runs
    # under pythonw.exe (the aniGamer.bat launcher). pythonw gives a child a None
    # sys.stdout, which breaks print()/err_print in the daemon — the same lesson
    # src/tray.py's _python_exe() encodes. The child window is still hidden by
    # CREATE_NO_WINDOW below, so no console flashes.
    py = sys.executable
    if py.lower().endswith('pythonw.exe'):
        cand = py[:-len('pythonw.exe')] + 'python.exe'
        if os.path.exists(cand):
            return cand
    return py


def _child_cmd():
    # How to re-invoke ourselves in --serve (child) mode.
    #   frozen onedir -> re-run the exe itself:  [exe, '--serve'].
    #   from source   -> [console-python, this file, '--serve'].
    # In a frozen build __file__ is inside the bundle and is NOT runnable, so the
    # exe must relaunch itself; the '--serve' arg routes to _serve_child below.
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--serve']
    return [_child_python(), os.path.abspath(__file__), '--serve']


def _webview2_runtime_present():
    # Best-effort WebView2 Evergreen runtime probe (Win10/11 usually ship it).
    if os.name != 'nt':
        return True
    import winreg
    key = (r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'
           r'\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key) as k:
                pv, _ = winreg.QueryValueEx(k, 'pv')
                if pv and pv not in ('', '0.0.0.0'):
                    return True
        except OSError:
            continue
    return False


def _ensure_webview2_runtime():
    # On a clean machine without the WebView2 runtime, run the bundled Evergreen
    # bootstrapper (self-elevating online installer). Best-effort: failures fall
    # through to _import_webview's actionable error.
    if _webview2_runtime_present():
        return
    cands = []
    if getattr(sys, 'frozen', False):  # bundled into _internal/ (= sys._MEIPASS)
        cands.append(os.path.join(getattr(sys, '_MEIPASS', _ROOT),
                                  'webview2', 'MicrosoftEdgeWebview2Setup.exe'))
    cands.append(os.path.join(_ROOT, 'shells', 'webview2', 'MicrosoftEdgeWebview2Setup.exe'))
    for boot in cands:
        if os.path.isfile(boot):
            try:
                subprocess.run([boot], timeout=600)
            except Exception:
                pass
            return


def main():
    host, port = _dashboard_target()

    # Spawn the gevent daemon + web as a clean child process. Its stdout/stderr
    # go to a log so a boot failure is diagnosable (the window would otherwise
    # just time out below).
    log_f = None
    log_path = os.path.join(_ROOT, 'data', 'logs', 'desktop-child.log')
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        log_f = open(log_path, 'a', encoding='utf-8')
    except OSError:
        pass
    creationflags = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW
    child = subprocess.Popen(
        _child_cmd(),
        cwd=_ROOT,
        stdout=log_f if log_f else subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    if not _wait_port(host, port, timeout=30.0):
        _terminate_child(child)
        if log_f:
            log_f.close()
        raise SystemExit(
            'web 服務未能在 30 秒內於 %s:%d 啟動。\n'
            '可能原因: 已有一個 AniGamerDownloader 正在執行 (單一實例鎖), 或 port 被佔用。\n'
            '詳見 data/logs/desktop-child.log' % (host, port))

    _ensure_webview2_runtime()  # install the WebView2 runtime on a clean machine
    webview = _import_webview()
    url = 'http://%s:%d' % (host, port)
    window = webview.create_window('AniGamerDownloader', url,
                                   width=1180, height=820, min_size=(900, 600))

    # Watchdog: if the child daemon dies (crash / single-instance refusal /
    # web "Stop" button), close the window so the app exits instead of showing a
    # dead page.
    def _watch():
        child.wait()
        try:
            window.destroy()
        except Exception:
            os._exit(1)
    threading.Thread(target=_watch, name='child-watchdog', daemon=True).start()

    # Blocks on the GUI loop until the window closes (WebView2 backend on Windows).
    webview.start()

    # Window closed -> stop the child cleanly, then exit hard so any lingering
    # threads/subprocesses do not keep the process alive.
    _terminate_child(child)
    if log_f:
        try:
            log_f.close()
        except Exception:
            pass
    os._exit(0)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        _serve_child()
    else:
        main()
