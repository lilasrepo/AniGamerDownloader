#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AniGamerDownloader core: the pure download engine + support layers.

Re-exports the public API so callers can do ``from core import ...`` without
knowing the internal module split, including the download engine
(``AnimeDownloader``) and ``Danmu``. Importing this package therefore requires
the full download-engine dependency set (requests/bs4/lxml/pyhttpx/...).
"""

from core.types import DownloadResult, ProgressEvent, Paths, Settings
from core.logging import ColorPrintLogger, Color, err_print
from core.cookies import CookieStore
from core import config
from core import db
from core.engine import AnimeDownloader, TryTooManyTimeError
from core.danmu import Danmu

__all__ = [
    'DownloadResult', 'ProgressEvent', 'Paths', 'Settings',
    'ColorPrintLogger', 'Color', 'err_print',
    'CookieStore',
    'config', 'db',
    'AnimeDownloader', 'TryTooManyTimeError', 'Danmu',
]
