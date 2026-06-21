#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AniGamerDownloader app layer: the daemon + web service built on :mod:`core`.

This package is the shared service consumed by both shells (desktop / docker).
It owns the runtime mutable state (:class:`app.state.AppState`), the daemon loop
(:mod:`app.daemon`) and the Flask web app (:mod:`app.web.server`). ``core`` stays
free of Flask/gevent/pystray; those concerns live here and in ``shells/``.
"""
