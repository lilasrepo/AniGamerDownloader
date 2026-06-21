#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author  : Miyouzi (original Server.py); refactored into app.web by local fork
"""The Flask dashboard, refactored from ``src/Server.py``.

Changes vs the original:

* ``from aniGamerPlus import Config, __cui, read_db_all, reset_db_status,
  redownload`` -> imports from :mod:`core.config` and :mod:`app.daemon`.
* The six ``Config.*`` runtime globals (``tasks_progress_rate`` / ``pending_tasks``
  / ``batch_download_paused`` / ``daemon_running`` / ``shutting_down`` /
  ``force_check_now``) -> the ONE shared :class:`AppState` (``daemon.state``).
* NEW routes ``GET /data/cookie`` (current cookie, masked) + ``POST /cookie``
  (write via :meth:`core.cookies.CookieStore.write`), mirroring the
  ``/data/sn_list`` + ``/sn_list`` pair.
* :func:`run` gains a ``force_basic_auth`` option for the Docker shell.

``gevent.monkey.patch_all()`` is NOT done here — the shell/entry does it before
importing this module (the original did it at the top of Server.py, but with the
app/shell split the patch belongs to the entry layer; the ``src/Server.py`` shim
and ``shells/docker/entrypoint.py`` patch first).
"""

import json
import sys
import os
import re
import threading
import traceback
import subprocess
import shutil
import signal
import time

from gevent import spawn
from flask import Flask, request, jsonify
from flask import render_template
from flask_basicauth import BasicAuth
import logging
import termcolor
from logging.handlers import TimedRotatingFileHandler
import mimetypes
import ssl
from gevent.pywsgi import WSGIServer

from core import config as Config
from core.logging import err_print
from app import daemon
from app.daemon import __cui as cui
from app.daemon import read_db_all, reset_db_status, redownload, cookies

# Shared runtime state (progress / pending / flags) — the SAME object the daemon
# mutates. Read it through `daemon.state` so both layers stay coherent.
state = daemon.state

# UTF-8 console I/O regardless of launcher (avoids cp950 UnicodeEncodeError).
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/x-javascript', '.js')
template_path = os.path.join(Config.get_web_dir(), 'templates')
static_path = os.path.join(Config.get_web_dir(), 'static')
app = Flask(__name__, template_folder=template_path, static_folder=static_path)
app.debug = False

# Logging setup
logger = logging.getLogger('AniGamerDownloader.web')
logging.basicConfig(level=logging.INFO)  # log access
web_log_path = os.path.join(Config.get_working_dir(), 'data', 'logs', 'web.log')
handler = TimedRotatingFileHandler(filename=web_log_path, when='midnight', backupCount=7, encoding='utf-8')
handler.suffix = '%Y-%m-%d.log'
handler.extMatch = re.compile(r'^\d{4}-\d{2}-\d{2}.log')
logger.addHandler(handler)
logger.propagate = False  # do not output to the console


# Handle the issue of Flask writing color control characters to the log file
def colored(text, color=None, on_color=None, attrs=None):
    who_invoked = traceback.extract_stack()[-2][2]  # caller of the function
    if who_invoked == 'log_request':
        # if the call comes from Flask/werkzeug
        return text
    else:
        # calls from elsewhere are highlighted normally
        COLORS = termcolor.COLORS
        HIGHLIGHTS = termcolor.HIGHLIGHTS
        ATTRIBUTES = termcolor.ATTRIBUTES
        RESET = termcolor.RESET
        if os.getenv('ANSI_COLORS_DISABLED') is None:
            fmt_str = '\033[%dm%s'
            if color is not None:
                text = fmt_str % (COLORS[color], text)
            if on_color is not None:
                text = fmt_str % (HIGHLIGHTS[on_color], text)
            if attrs is not None:
                for attr in attrs:
                    text = fmt_str % (ATTRIBUTES[attr], text)
            text += RESET
        return text


termcolor.colored = colored
app.logger.addHandler(handler)

# Read the list of config names the web needs
id_list_path = os.path.join(Config.get_web_dir(), 'static', 'js', 'settings_id_list.js')
with open(id_list_path, 'r', encoding='utf-8') as f:
    id_list = re.sub(r'(var id_list\s*=\s*|\s*\n?)', '', f.read()).replace('\'', '"')
    id_list = json.loads(id_list)


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/monitor')
def monitor():
    # single-page app; the SPA reads the path/hash and opens the monitor tab
    return render_template('index.html')


@app.route('/data/config.json', methods=['GET'])
def config():
    settings = Config.read_settings()
    web_settings = {}
    for id in id_list:
        web_settings[id] = settings[id]  # only return the config the web needs

    return jsonify(web_settings)


@app.route('/uploadConfig', methods=['POST'])
def recv_config():
    data = json.loads(request.get_data(as_text=True))
    new_settings = Config.read_settings()
    for id in id_list:
        new_settings[id] = data[id]  # update config
    Config.write_settings(new_settings)  # save config
    err_print(0, 'Dashboard', '通過 Web 控制臺更新了 config.json', no_sn=True, status=2)
    return '{"status":"200"}'


@app.route('/manualTask', methods=['POST'])
def manual_task():
    data = json.loads(request.get_data(as_text=True))
    settings = Config.read_settings()

    # download resolution
    if data['resolution'] not in ('360', '480', '540', '720', '1080'):
        resolution = settings['download_resolution']
    else:
        resolution = data['resolution']

    # download mode
    if data['mode'] not in ('single', 'latest', 'all', 'largest-sn'):
        mode = 'single'
    else:
        mode = data['mode']

    # download thread count
    if data['thread']:
        thread = int(data['thread'])
    else:
        thread = 1
    if thread > Config.get_max_multi_thread():
        thread_limit = Config.get_max_multi_thread()
    else:
        thread_limit = thread

    def run_cui():
        cui(data['sn'], resolution, mode, thread_limit, [], classify=data['classify'], realtime_show=False, cui_danmu=data['danmu'])

    server = threading.Thread(target=run_cui)
    err_print(0, 'Dashboard', '通過 Web 控制臺下達了手動任務', no_sn=True, status=2)
    server.start()  # start the manual-task thread
    return '{"status":"200"}'


@app.route('/data/sn_list', methods=['GET'])
def show_sn_list():
    return Config.get_sn_list_content()


@app.route('/data/cookie', methods=['GET'])
def show_cookie():
    # Return the current cookie.txt content for GUI editing, masking sensitive values
    # (only to confirm whether it is set / which keys exist).
    # Mirrors /data/sn_list. Returns an empty string if the file is not found.
    path = Config.cookie_path
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return jsonify({'masked': ''})
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    # Mask: for each key=value, only show the first and last 2 chars of value, replacing the middle with ***.
    masked_parts = []
    for part in raw.split('; '):
        if '=' in part:
            k, v = part.split('=', 1)
            if len(v) > 4:
                v = v[:2] + '***' + v[-2:]
            else:
                v = '***'
            masked_parts.append(k + '=' + v)
        else:
            masked_parts.append(part)
    # Only return the masked value; the raw cookie is never sent to the browser (avoids leaking sensitive credentials in DevTools/the network layer).
    return jsonify({'masked': '; '.join(masked_parts)})


@app.route('/cookie', methods=['POST'])
def set_cookie():
    # GUI writes the cookie: write the pasted Cookie: header string into cookie.txt (via CookieStore.write).
    # Mirrors /sn_list. After writing, CookieStore resets its in-memory cache and re-parses on the next read().
    data = request.get_data(as_text=True)
    cookies.write(data)
    err_print(0, 'Dashboard', '通過 Web 控制臺更新了 cookie', no_sn=True, status=2)
    return '{"status":"200"}'


@app.route('/data/tasks_progress_json', methods=['GET'])
def tasks_progress_json():
    # Polling endpoint: active = tasks currently downloading/parsing (have a progress bar);
    # pending = scheduled tasks still waiting for a concurrency slot.
    return jsonify({'active': state.tasks_progress_rate, 'pending': state.pending_tasks})


@app.route('/sn_list', methods=['POST'])
def set_sn_list():
    data = request.get_data(as_text=True)
    Config.write_sn_list(data)
    err_print(0, 'Dashboard', '通過 Web 控制臺更新了 sn_list', no_sn=True, status=2)
    return '{"status":"200"}'


# ============================================================================
# DB inventory view / reset / download now (Feature 1) + batch-download pause (Feature 2)
# ============================================================================

def _ffprobe_ok(path, exe):
    # Deep-verify using the resolved ffprobe path: parses successfully and duration > 0 is treated as complete.
    try:
        p = subprocess.run(
            [exe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
            creationflags=(0x08000000 if os.name == 'nt' else 0))
        if p.returncode != 0:
            return False
        duration = float(p.stdout.decode('utf-8', 'ignore').strip() or 0)
        return duration > 0
    except Exception:
        return False


def _episode_state(r):
    # Annotate one db record: whether the file exists / actual size / status.
    path = r['local_file_path']
    db_size = r['file_size'] or 0
    exists = bool(path) and os.path.exists(path)
    actual_mb = 0
    if exists:
        try:
            actual_mb = int(os.path.getsize(path) / float(1024 * 1024))
        except OSError:
            exists = False

    if r['status'] == 1:
        if not exists:
            state_ = 'missing'
        elif db_size > 0 and actual_mb < db_size * 0.98:
            state_ = 'suspect'  # size quick-filter suspicious, needs ffprobe re-verification
        else:
            state_ = 'ok'
    else:
        state_ = 'ok' if exists else 'not_downloaded'

    return {
        'sn': r['sn'], 'episode': r['episode'], 'title': r['title'],
        'resolution': r['resolution'], 'db_size': db_size, 'actual_size': actual_mb,
        'exists': exists, 'status': r['status'], 'state': state_,
    }


def _episode_sort_key(ep):
    # Episode sort: pure numbers sort by value, non-numeric (movies/specials) go last
    s = str(ep.get('episode', ''))
    m = re.match(r'^\d+', s)
    return (0, int(m.group())) if m else (1, s)


@app.route('/data/db_inventory', methods=['GET'])
def db_inventory():
    # Group all db records by anime, with a file-exists/size quick-filter status (no ffprobe, so fast).
    series = {}
    for r in read_db_all():
        name = r['anime_name'] or '(未分類)'
        series.setdefault(name, []).append(_episode_state(r))
    result = []
    for name in sorted(series.keys()):
        eps = sorted(series[name], key=_episode_sort_key)
        counts = {'ok': 0, 'missing': 0, 'suspect': 0, 'not_downloaded': 0}
        for e in eps:
            counts[e['state']] = counts.get(e['state'], 0) + 1
        result.append({'anime_name': name, 'episodes': eps, 'counts': counts})
    return jsonify(result)


@app.route('/db_verify', methods=['POST'])
def db_verify():
    # Run ffprobe deep verification on the given sn list, return {sn: 'ok'|'corrupt'|'missing'}
    data = json.loads(request.get_data(as_text=True))
    sns = {int(s) for s in data.get('sns', [])}
    exe = shutil.which('ffprobe')  # resolve once; if not on PATH report no_ffprobe (must not be misjudged as corrupt)
    rows = {r['sn']: r for r in read_db_all()}
    results = {}
    for sn in sns:
        r = rows.get(sn)
        path = r['local_file_path'] if r else None
        if not path or not os.path.exists(path):
            results[sn] = 'missing'
        elif not exe:
            results[sn] = 'no_ffprobe'
        else:
            results[sn] = 'ok' if _ffprobe_ok(path, exe) else 'corrupt'
    return jsonify(results)


@app.route('/db_reset', methods=['POST'])
def db_reset():
    # Reset (status->0) one episode or a whole series (multiple sn), without deleting files
    data = json.loads(request.get_data(as_text=True))
    sns = [int(s) for s in data.get('sns', [])]
    for sn in sns:
        reset_db_status(sn)
    err_print(0, 'Dashboard', '透過 Web 重置了 ' + str(len(sns)) + ' 集的下載狀態', no_sn=True, status=2)
    return jsonify({'status': 200, 'count': len(sns)})


@app.route('/db_redownload', methods=['POST'])
def db_redownload():
    # Immediately re-download one episode or a whole series (multiple sn), writing the db when done. Runs in the background; progress is visible on the monitor page.
    data = json.loads(request.get_data(as_text=True))
    sns = [int(s) for s in data.get('sns', [])]

    def run_redownload():
        for sn in sns:
            try:
                redownload(sn)
            except Exception as e:
                err_print(sn, '重新下載', '任務異常, 跳過此集繼續: ' + str(e), status=1)

    threading.Thread(target=run_redownload, daemon=True).start()
    err_print(0, 'Dashboard', '透過 Web 立即下載 ' + str(len(sns)) + ' 集', no_sn=True, status=2)
    return jsonify({'status': 200, 'count': len(sns)})


@app.route('/batch/status', methods=['GET'])
def batch_status():
    # Report the batch-download pause state, plus whether the daemon is running (pause has no effect in app-only mode)
    return jsonify({'paused': state.batch_download_paused, 'daemon': state.daemon_running})


@app.route('/batch/pause', methods=['POST'])
def batch_pause():
    state.batch_download_paused = True
    err_print(0, 'Dashboard', '透過 Web 暫停了批次下載', no_sn=True, status=2)
    return jsonify({'status': 200, 'paused': True})


@app.route('/batch/resume', methods=['POST'])
def batch_resume():
    state.batch_download_paused = False
    err_print(0, 'Dashboard', '透過 Web 恢復了批次下載', no_sn=True, status=2)
    return jsonify({'status': 200, 'paused': False})


@app.route('/daemon/check_now', methods=['POST'])
def daemon_check_now():
    # Check now: make the daemon skip the remaining cooldown and scan sn_list once immediately
    state.force_check_now = True
    err_print(0, 'Dashboard', '透過 Web 觸發立即檢查', no_sn=True, status=2)
    return jsonify({'status': 200})


def _delayed_shutdown():
    # Let the HTTP response be sent first, then terminate the whole process.
    time.sleep(0.4)
    os.kill(os.getpid(), signal.SIGTERM)


@app.route('/shutdown', methods=['POST'])
def shutdown():
    # Stop the whole program (daemon + web).
    state.shutting_down = True
    err_print(0, 'Dashboard', '透過 Web 控制臺請求停止程式', no_sn=True, status=2)
    spawn(_delayed_shutdown)
    return jsonify({'status': 200})


def _ensure_self_signed_cert(crt_path, key_path):
    """Make sure a self-signed localhost cert/key pair exists at the given paths.

    The cert is deliberately NOT shipped in the repo (a private key has no place
    in a public tree), so it is generated on demand the first time SSL is turned
    on. Returns True if a usable pair is present afterwards, False if generation
    failed — the caller then falls back to plain HTTP.
    """
    if os.path.isfile(crt_path) and os.path.isfile(key_path):
        return True
    try:
        from datetime import datetime, timedelta
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        os.makedirs(os.path.dirname(crt_path), exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u'localhost')])
        now = datetime.utcnow()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(u'localhost')]),
                           critical=False)
            .sign(key, hashes.SHA256())
        )
        with open(key_path, 'wb') as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()))
        with open(crt_path, 'wb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        err_print(0, 'SSL 憑證', '已自動產生自簽憑證 -> %s' % os.path.dirname(crt_path),
                  no_sn=True, status=2)
        return True
    except Exception as e:
        err_print(0, 'SSL 憑證', '自動產生自簽憑證失敗, 改用 HTTP: %s' % e,
                  no_sn=True, status=1)
        return False


def run(force_basic_auth=False):
    settings = Config.read_settings()  # read config

    if settings['dashboard']['BasicAuth'] or force_basic_auth:
        # BasicAuth config. force_basic_auth lets the Docker shell force it on (even if config has it off).
        app.config['BASIC_AUTH_USERNAME'] = settings['dashboard']['username']  # BasicAuth user
        app.config['BASIC_AUTH_PASSWORD'] = settings['dashboard']['password']  # BasicAuth password
        app.config['BASIC_AUTH_FORCE'] = True  # site-wide auth
        BasicAuth(app)

    port = settings['dashboard']['port']
    host = settings['dashboard']['host']

    ssl_ok = False
    if settings['dashboard']['SSL']:
        # SSL config. The cert lives under the WRITABLE data dir (beside config/db),
        # NOT in web/ — web/ is read-only when frozen, and the key must never be
        # committed. Generate a self-signed localhost pair on demand if missing.
        ssl_path = os.path.join(Config.get_working_dir(), 'data', 'sslkey')
        ssl_crt = os.path.join(ssl_path, 'server.crt')
        ssl_key = os.path.join(ssl_path, 'server.key')
        ssl_ok = _ensure_self_signed_cert(ssl_crt, ssl_key)

    if ssl_ok:
        server = WSGIServer((host, port), app, certfile=ssl_crt, keyfile=ssl_key)

        wrap_socket = server.wrap_socket
        wrap_socket_and_handle = server.wrap_socket_and_handle

        # Handle errors when some browsers (e.g. Chrome) try to access via SSL v3
        def my_wrap_socket(sock, **_kwargs):
            try:
                return wrap_socket(sock, **_kwargs)
            except ssl.SSLError:
                pass

        # This method depends on the return value above, so it also errors when accessing via SSL v3
        def my_wrap_socket_and_handle(client_socket, address):
            try:
                return wrap_socket_and_handle(client_socket, address)
            except AttributeError:
                pass

        server.wrap_socket = my_wrap_socket
        server.wrap_socket_and_handle = my_wrap_socket_and_handle

    else:
        server = WSGIServer((host, port), app)

    server.serve_forever()


if __name__ == '__main__':
    run()
