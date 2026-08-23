#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AppState: the runtime mutable flags + progress/pending registries.

Moved from the ``Config`` module globals (``src/Config.py:32-44``):

* ``tasks_progress_rate`` -- {sn: {'rate', 'filename', 'status'}}; tasks that
  currently have a progress bar (active). The engine emits progress events via
  ``on_progress``; the daemon's callback (:meth:`AppState.on_progress`) mutates
  this dict.
* ``pending_tasks`` -- {sn: {'name', 'episode'}}; scheduled tasks still waiting
  for a concurrency slot. The daemon adds an entry when it spawns a worker; the
  worker pops it the instant it acquires the limiter (active ∩ pending = ∅).
* ``batch_download_paused`` / ``daemon_running`` / ``shutting_down`` /
  ``force_check_now`` -- the four Web-dashboard runtime flags.

Plus the queue/limiter registries that were module globals in
``src/aniGamerPlus.py`` (queue / processing_queue / thread_limiter /
upload_limiter / redownloading), so the daemon and web read ONE shared object.

ONE :class:`AppState` instance is shared between the daemon loop and the Flask
app (replaces the "Config is the one module both import" module-global trick).
The keys of ``tasks_progress_rate`` / ``pending_tasks`` are ``int`` sn, matching
the engine's ``int(self._sn)`` emit keys and the original daemon code.
"""

import threading


class AppState:
    def __init__(self, *, multi_thread=1, multi_upload=3):
        # ----- progress / pending registries (were Config globals) -----
        # {sn(int): {'rate': float, 'filename': str, 'status': str}}
        self.tasks_progress_rate = {}
        # {sn(int): {'name': str, 'episode': str}}
        self.pending_tasks = {}

        # ----- runtime flags (were Config globals) -----
        self.batch_download_paused = False  # Web batch-download pause toggle (in-memory flag, reset on restart)
        self.daemon_running = False         # whether the daemon main loop is running (distinguishes app-only mode)
        self.shutting_down = False          # Web "stop program" request: the daemon loop exits on this
        self.force_check_now = False        # Web "check now" request: skip the remaining cooldown and scan immediately

        # ----- pause bookkeeping (why we are paused, and the failure guard) -----
        # 'startup' -- came up idle on purpose (settings['start_paused']), no request made yet
        # 'user'    -- the operator pressed 暫停
        # 'failures'-- the auto-pause guard tripped after N consecutive failures
        self.pause_reason = ''
        self.consecutive_failures = 0
        self.last_failure = ''              # short human-readable reason for the most recent failure
        self.failure_locker = threading.Lock()

        # ----- task queues / limiters (were aniGamerPlus module globals) -----
        self.queue = {}                     # {sn: sn_info}; pending tasks
        self.processing_queue = []          # in-progress sn
        self.thread_limiter = threading.Semaphore(multi_thread)   # download concurrency limiter
        self.upload_limiter = threading.Semaphore(multi_upload)   # upload concurrency limiter
        self.redownloading = set()          # sn currently being re-fetched by Web "download now" (in-flight dedup)
        self.redownloading_locker = threading.Semaphore(1)

    def record_failure(self, limit, detail=''):
        """Count one failed task; auto-pause the batch once ``limit`` is reached.

        Called from worker threads, so the counter is mutated under a lock.
        Returns True on the single call that trips the guard (so the caller logs
        it once), False otherwise. ``limit`` <= 0 disables the guard entirely --
        the counter still runs so the dashboard can show it.
        """
        with self.failure_locker:
            self.consecutive_failures += 1
            if detail:
                self.last_failure = detail
            if not limit or limit < 1:
                return False
            if self.consecutive_failures >= limit and not self.batch_download_paused:
                self.batch_download_paused = True
                self.pause_reason = 'failures'
                return True
        return False

    def record_success(self):
        # One completed download clears the streak -- the guard only fires on a
        # run of failures with nothing succeeding in between.
        with self.failure_locker:
            self.consecutive_failures = 0
            self.last_failure = ''

    def clear_pause(self):
        # Resume: drop the pause AND the streak, so the guard gets a full N
        # attempts again instead of re-tripping on the next single failure.
        with self.failure_locker:
            self.batch_download_paused = False
            self.pause_reason = ''
            self.consecutive_failures = 0

    def on_progress(self, sn, event):
        # The daemon's on_progress callback passed into AnimeDownloader. Routes a
        # ProgressEvent into tasks_progress_rate, reproducing the original direct
        # writes to Config.tasks_progress_rate (register / rate / status /
        # filename / delete). Keys are int sn.
        reg = self.tasks_progress_rate
        if event.done:
            reg.pop(sn, None)
            return
        if sn not in reg:
            # Initial register (download() start). Mirror the original literal shape.
            reg[sn] = {'rate': event.rate if event.rate is not None else 0,
                       'filename': event.filename if event.filename is not None else '',
                       'status': event.status if event.status is not None else ''}
            return
        if event.rate is not None:
            reg[sn]['rate'] = event.rate
        if event.status is not None:
            reg[sn]['status'] = event.status
        if event.filename is not None:
            reg[sn]['filename'] = event.filename
