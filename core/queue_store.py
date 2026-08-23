#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent manual download queue.

The batch downloader has always had two ways to start a download and neither
let you *stage* work: the Web "download now" path (:func:`app.daemon.redownload`)
and the manual-task path (``__cui``) both fire immediately. This module adds a
holding area between "I picked something" and "start downloading it": items are
appended here, shown on the dashboard, and only dispatched when the operator
presses 開始.

Design notes:

* The list lives in memory and is mirrored to ``data/download_queue.json`` on
  every mutation, so a queue survives restarting the program. Reads never touch
  the disk.
* Writes are atomic (temp file + ``os.replace``) and never raise: a queue is a
  convenience, and losing it must never take the downloader down with it.
* Identity is ``kind:sn`` -- queueing the same episode twice REPLACES the entry
  rather than duplicating it, so double-clicking "加入佇列" is harmless and
  re-queueing a manual task with different options does the obvious thing.
* The daemon and the Flask app run in ONE process (``run_daemon`` starts the web
  server), so a single module-level store is shared by both, like
  :class:`app.state.AppState`.
"""

import json
import os
import threading
from datetime import datetime

from core import config as Config
from core.logging import err_print

# 'db'     -- re-download a sn already in the database (app.daemon.redownload)
# 'manual' -- a one-off task with its own options (app.daemon.__cui)
KINDS = ('db', 'manual')

_QUEUE_FILENAME = 'download_queue.json'


def _queue_path():
    # resolved late: Config.data_dir is settled by the entry layer before first use
    return os.path.join(Config.data_dir, _QUEUE_FILENAME)


def _item_id(kind, sn):
    return '%s:%d' % (kind, int(sn))


class QueueStore:
    """In-memory list of staged downloads, mirrored to a JSON file."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []
        self._loaded = False

    # ---- persistence --------------------------------------------------------

    def _load_locked(self):
        """Read the backing file once, on first use. Never raises."""
        if self._loaded:
            return
        self._loaded = True
        path = _queue_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                self._items = [i for i in data if _valid(i)]
        except Exception as e:
            # a corrupt queue file must not stop the program from starting
            err_print(0, '下載佇列', '讀取佇列檔失敗, 以空佇列繼續: ' + str(e),
                      no_sn=True, status=1)
            self._items = []

    def _save_locked(self):
        """Mirror the in-memory list to disk atomically. Never raises."""
        path = _queue_path()
        tmp = path + '.tmp'
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except Exception as e:
            err_print(0, '下載佇列', '寫入佇列檔失敗: ' + str(e), no_sn=True, status=1)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # ---- public API ---------------------------------------------------------

    def list(self):
        """Every staged item, oldest first."""
        with self._lock:
            self._load_locked()
            return [dict(i) for i in self._items]

    def add(self, entries):
        """Stage ``entries``; returns (added, replaced).

        An entry is ``{kind, sn, title, options}``. Re-adding an existing
        ``kind:sn`` overwrites it instead of creating a duplicate.
        """
        added = replaced = 0
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        with self._lock:
            self._load_locked()
            for raw in entries or []:
                item = _normalise(raw, stamp)
                if item is None:
                    continue
                existing = next((i for i in self._items if i['id'] == item['id']), None)
                if existing is None:
                    self._items.append(item)
                    added += 1
                else:
                    existing.update(item)
                    replaced += 1
            if added or replaced:
                self._save_locked()
        return added, replaced

    def remove(self, ids):
        """Drop the given ids; returns how many were removed."""
        wanted = set(ids or [])
        if not wanted:
            return 0
        with self._lock:
            self._load_locked()
            before = len(self._items)
            self._items = [i for i in self._items if i['id'] not in wanted]
            removed = before - len(self._items)
            if removed:
                self._save_locked()
        return removed

    def take(self, ids=None):
        """Remove and RETURN items, for dispatching.

        ``ids`` of None (or empty) takes the whole queue. Removing before the
        download starts is deliberate: a staged item that is now running should
        not still look staged, and a failed download reappears in the log rather
        than silently re-queueing.
        """
        with self._lock:
            self._load_locked()
            if ids:
                wanted = set(ids)
                taken = [i for i in self._items if i['id'] in wanted]
                self._items = [i for i in self._items if i['id'] not in wanted]
            else:
                taken = list(self._items)
                self._items = []
            if taken:
                self._save_locked()
        return taken

    def clear(self):
        """Drop everything; returns how many were removed."""
        return len(self.take())


def _valid(item):
    return (isinstance(item, dict)
            and item.get('kind') in KINDS
            and isinstance(item.get('id'), str)
            and str(item.get('sn', '')).lstrip('-').isdigit())


def _normalise(raw, stamp):
    """Coerce one caller-supplied entry into a stored item, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get('kind')
    if kind not in KINDS:
        return None
    try:
        sn = int(raw.get('sn'))
    except (TypeError, ValueError):
        return None
    if sn <= 0:
        return None
    title = str(raw.get('title') or ('sn ' + str(sn)))[:200]
    options = raw.get('options')
    return {
        'id': _item_id(kind, sn),
        'kind': kind,
        'sn': sn,
        'title': title,
        'added': stamp,
        # Where this item will land. ``path_exact`` False means only the
        # destination FOLDER is known -- the filename is decided at parse time,
        # so the UI must not present it as a real file path.
        'path': str(raw.get('path') or '')[:500],
        'path_exact': bool(raw.get('path_exact')),
        'options': options if isinstance(options, dict) else {},
    }


# ONE store shared by the daemon and the Flask app (same process).
store = QueueStore()
