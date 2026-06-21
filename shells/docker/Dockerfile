# AniGamerDownloader — Docker shell (headless daemon + web dashboard).
#
# Multi-arch by design (linux/amd64 + linux/arm64): no arch-specific binaries are
# baked in — Python wheels + the distro ffmpeg package cover both. Build with:
#   docker buildx build --platform linux/amd64,linux/arm64 -f shells/docker/Dockerfile -t <tag> --push .
# (build context = repo ROOT, so core/ app/ web/ data/ are all visible.)
#
# Layout invariant: core/config.py resolves working_dir as the PARENT of core/.
# We copy core/ app/ web/ as siblings under /app, so working_dir == /app and the
# app finds /app/web (templates/static) and /app/data (config/cookie/db/logs).
FROM python:3.13-slim

# ffmpeg = hard runtime dep (decrypt + merge m3u8 segments); ffprobe (same pkg)
# powers the dashboard's /db_verify deep check. tini = proper PID-1 signal
# forwarding so the web "Stop" button / docker stop deliver SIGTERM to the daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps (cache layer) -----------------------------------------------
# Install THIRD-PARTY deps in their own layer so it's cached and only rebuilds
# when this list changes (not on every source edit). The app is run from the
# /app source trees via sys.path (the entrypoint puts /app first), NOT from a
# site-packages install — so pip only needs to provide the runtime libraries,
# not the core/app packages themselves.
#
# This list = core/pyproject.toml's declared deps (the download-engine subset)
# PLUS the web/app server deps. Mirrors requirements-venv.txt minus the
# Windows/desktop-only bits (pystray/Pillow/pywebview). Keep in sync with
# core/pyproject.toml. greenlet is pulled transitively by gevent.
#
# pip-system-certs IS required (app/daemon.py does `import
# pip_system_certs.wrapt_requests` AFTER the entrypoint's gevent
# monkey.patch_all() — the activation order the app intends). BUT the package
# also ships an auto-bootstrap `.pth` that runs truststore's ssl patch at
# interpreter startup, BEFORE gevent. That early patch collides with gevent's
# own ssl patch and the system CA store then fails to load under gevent ->
# SSLCertVerificationError on every ani.gamer.com.tw request. So we DELETE the
# .pth right after install: the package stays importable (daemon.py works) and
# its explicit post-gevent import becomes the single, correctly-ordered
# activation. The package has no env switch, so removing the .pth is the fix.
RUN pip install --no-cache-dir \
        requests \
        beautifulsoup4 \
        lxml \
        pyhttpx \
        chardet \
        termcolor \
        pysocks \
        pip-system-certs \
        "flask>=3.0" \
        "flask_basicauth==0.2.0" \
        "gevent>=24.2.1" \
    && rm -f /usr/local/lib/python*/site-packages/pip_system_certs.pth

# --- application source ------------------------------------------------------
# Sibling packages under /app so working_dir (parent of core/) == /app.
COPY core/ ./core/
COPY app/ ./app/
COPY web/ ./web/
COPY shells/__init__.py ./shells/__init__.py
COPY shells/docker/ ./shells/docker/

# Danmu template seed. The engine reads /app/data/DanmuTemplate.ass, but /app/data
# is a MOUNTED volume that shadows anything baked there — so we stage the template
# OUTSIDE the volume and the entrypoint copies it into /app/data on first boot.
COPY data/DanmuTemplate.ass /app/seed/DanmuTemplate.ass

# data/ is a MOUNTED volume (config.json / cookie.txt / aniGamer.db / logs /
# bangumi output). Declare it so an un-mounted run still persists to an anon vol;
# the entrypoint creates the dir tree (and the output dir) on first boot.
VOLUME ["/app/data"]

# In-container web port. Override the published port on the host side, and
# ANIGAMER_WEB_PORT if you change the in-container port too.
EXPOSE 5000

# Headless boot: tini as PID 1 -> python -m shells.docker.entrypoint.
# -u = unbuffered so logs stream to `docker logs` immediately.
ENTRYPOINT ["/usr/bin/tini", "--", "python", "-u", "-m", "shells.docker.entrypoint"]
