#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless Docker entry point for the AniGamerDownloader daemon + web dashboard.

This is the Docker SHELL (one of the two shells built on ``core`` + ``app``).
It boots the SAME service layer the desktop shell uses, but headless:

* ``gevent.monkey.patch_all()`` FIRST — before importing :mod:`app.daemon` /
  :mod:`app.web.server`, exactly like the ``src/`` shims and the desktop shell.
  The engine's threading/socket/ssl calls rely on being patched under the daemon.
* Repo root on ``sys.path`` so ``import core`` / ``import app`` resolve when run
  as ``python -m shells.docker.entrypoint`` from ``/app``.
* Env overrides are applied to ``data/config.json`` BEFORE importing
  ``app.daemon`` (that module reads ``Config.read_settings()`` at import time).
* Web is forced onto host ``0.0.0.0`` with **BasicAuth ON** — the container is
  meant for LAN only, so we refuse to start with an empty/placeholder password.

Run:  ``python -m shells.docker.entrypoint``  (set as the image ENTRYPOINT).

Env vars (all optional except the password, which is REQUIRED):

==========================  ===========================================
``ANIGAMER_WEB_USER``       BasicAuth username        (default ``admin``)
``ANIGAMER_WEB_PASSWORD``   BasicAuth password        (REQUIRED, no default)
``ANIGAMER_WEB_PORT``       in-container web port     (default ``5000``)
``ANIGAMER_OUTPUT_DIR``     in-container output dir   (default mounted
                            ``/app/data/bangumi`` — the engine's silent
                            fallback, so leaving this unset + mounting the
                            volume there Just Works)
``ANIGAMER_RESOLUTION``     download resolution       (e.g. ``1080``)
``ANIGAMER_CHECK_FREQ``     sn_list check freq (min)  (e.g. ``5``)
==========================  ===========================================

config.json, cookie.txt, the sqlite db and logs all live under the mounted
``/app/data`` volume, so they survive container recreation. Paste the cookie via
the web GUI (設定 → Cookie 區塊) — no need to edit ``cookie.txt`` by hand.
"""

# --- 1. monkey-patch BEFORE any stdlib threading/socket/ssl import chain ------
from gevent import monkey
monkey.patch_all()

import os
import sys

# --- 2. put the repo root (parent of shells/) on sys.path ---------------------
# /app/shells/docker/entrypoint.py -> repo root is two levels up from this file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# UTF-8 console I/O (CJK log lines) regardless of the container locale.
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass


def _env(name, default=None):
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    return v if v else default


# Passwords we refuse to boot with: empty, or the schema placeholder 'admin'.
# A LAN-exposed dashboard with the default password is the one footgun worth
# hard-failing on (CLAUDE.md §8: real network exposure needs a real secret).
_REJECTED_PASSWORDS = {'', 'admin', 'password', 'changeme'}


def _seed_data_dir():
    """Seed the mounted /app/data volume on first boot.

    The /app/data volume shadows anything baked into the image there, so the
    danmu template (staged at /app/seed/DanmuTemplate.ass by the Dockerfile) must
    be copied in if absent. Also make sure data/ + data/logs exist so the very
    first ``read_settings()`` / log write does not fail on an empty named volume.
    """
    import shutil
    from core import config as Config

    data_dir = os.path.join(Config.get_working_dir(), 'data')
    os.makedirs(os.path.join(data_dir, 'logs'), exist_ok=True)

    template_dst = os.path.join(data_dir, 'DanmuTemplate.ass')
    template_seed = os.path.join(Config.get_working_dir(), 'seed', 'DanmuTemplate.ass')
    if not os.path.exists(template_dst) and os.path.exists(template_seed):
        try:
            shutil.copyfile(template_seed, template_dst)
        except OSError as e:
            sys.stderr.write('[entrypoint] WARNING: cannot seed DanmuTemplate.ass: '
                             + str(e) + '\n')


def _apply_env_overrides():
    """Read env → enrich + persist data/config.json BEFORE app.daemon imports it.

    Uses ``core.config`` only (NOT ``app.daemon``, which reads settings at import
    time). Returns nothing; the next import of ``app.daemon`` picks up the file.
    """
    from core import config as Config

    password = _env('ANIGAMER_WEB_PASSWORD')
    if password is None or password.strip().lower() in _REJECTED_PASSWORDS:
        sys.stderr.write(
            '\n[entrypoint] FATAL: ANIGAMER_WEB_PASSWORD is empty or a default '
            'placeholder.\n'
            '[entrypoint] The dashboard is forced onto 0.0.0.0 with BasicAuth; '
            'refusing to start without a real password.\n'
            '[entrypoint] Set a strong ANIGAMER_WEB_PASSWORD env var and retry.\n\n')
        sys.exit(78)  # EX_CONFIG

    username = _env('ANIGAMER_WEB_USER', 'admin')
    port = int(_env('ANIGAMER_WEB_PORT', '5000'))
    output_dir = _env('ANIGAMER_OUTPUT_DIR', os.path.join(Config.get_working_dir(), 'data', 'bangumi'))
    resolution = _env('ANIGAMER_RESOLUTION')
    check_freq = _env('ANIGAMER_CHECK_FREQ')

    # Make sure the output dir exists, otherwise read_settings()'s "path missing
    # -> silent fallback to data/bangumi" kicks in and the override is ignored.
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        sys.stderr.write('[entrypoint] WARNING: cannot create output dir '
                         + output_dir + ': ' + str(e) + '\n')

    # Start from the enriched current settings (auto-creates config.json on first
    # run), apply our overrides, write it back through the normal writer.
    settings = Config.read_settings()

    settings['dashboard']['host'] = '0.0.0.0'        # expose to LAN
    settings['dashboard']['port'] = port
    settings['dashboard']['BasicAuth'] = True        # force on; run() mounts BasicAuth accordingly
    settings['dashboard']['username'] = username
    settings['dashboard']['password'] = password
    settings['dashboard']['SSL'] = False             # LAN only; leave TLS to the reverse proxy
    settings['use_dashboard'] = True
    settings['use_proxy'] = False                    # no gost proxy by default inside the container

    settings['bangumi_dir'] = output_dir
    settings['save_logs'] = True

    if resolution in ('360', '480', '540', '720', '1080'):
        settings['download_resolution'] = resolution
    if check_freq and check_freq.isdigit() and int(check_freq) > 0:
        settings['check_frequency'] = int(check_freq)

    Config.write_settings(settings)


def main():
    _seed_data_dir()
    _apply_env_overrides()

    # Import AFTER config.json is written: app.daemon reads settings at import.
    from app import daemon

    # run_daemon() spawns the web dashboard (run_dashboard -> app.web.server.run),
    # then enters the auto-download loop. Same code path the desktop shell uses.
    daemon.run_daemon()


if __name__ == '__main__':
    main()
