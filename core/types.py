#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure data types shared across the core engine and the app/shell layers.

Zero imports beyond the stdlib dataclasses/typing so this module can be pulled
in anywhere (engine, daemon, web) without dragging in heavier dependencies.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProgressEvent:
    """A single progress update emitted by the download engine.

    The engine emits these via the injected ``on_progress(sn, ProgressEvent)``
    callback instead of writing the shared ``tasks_progress_rate`` registry
    directly. Each field is optional so callers can send partial updates
    (e.g. only ``rate`` during a download tick, or only ``status`` on a state
    change). ``done=True`` signals the entry should be removed from the
    registry.
    """

    rate: Optional[float] = None
    status: Optional[str] = None
    filename: Optional[str] = None
    done: bool = False


@dataclass
class DownloadResult:
    """Outcome of an :meth:`AnimeDownloader.download` call.

    Returned for the core-API/unit-test contract while the engine still mutates
    ``self.video_size`` / ``self.local_video_path`` / ``self.video_resolution``
    for backward-compatible callers.
    """

    path: str
    size: int
    resolution: int
    ok: bool
    error: Optional[str] = None


@dataclass
class Paths:
    """Resolved filesystem locations injected into the engine/danmu.

    Derived FROM the enriched settings dict (not a replacement for it):
    ``bangumi_dir`` / ``temp_dir`` already carry the fallback-to-``data/``
    normalization done in :func:`core.config.read_settings`.
    """

    working_dir: str
    bangumi_dir: str
    temp_dir: str
    danmu_template: str


# A Settings is just the enriched config dict produced by read_settings().
Settings = dict
