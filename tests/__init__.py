#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for the pure / local parts of ``core``.

Run from the project root with the project venv::

    .venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v

Importing this package installs two process-wide guards BEFORE any ``core``
module is imported by a test module:

* **No network.** ``socket.getaddrinfo`` / ``create_connection`` /
  ``socket.connect`` raise :class:`NetworkBlocked`. Nothing under test should
  open a socket; if something does, the test fails loudly instead of talking to
  the site (account-ban risk) or GitHub.
* **No writes to the real ``data/logs``.** ``core.logging._default_logger`` (what
  every ``err_print`` call goes through) is replaced by :data:`LOG`, an
  in-memory recorder tests can inspect.

Per-test files live under ``<repo>/tmp/unit-tests/`` (git-ignored), never the
real ``data/`` directory.
"""

import os
import socket
import tempfile


class NetworkBlocked(RuntimeError):
    pass


def _blocked(*args, **kwargs):
    raise NetworkBlocked('network access is blocked in unit tests: %r' % (args,))


socket.getaddrinfo = _blocked
socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked


class LogRecorder:
    """Drop-in for ``ColorPrintLogger``: same call signature, records instead of printing."""

    def __init__(self):
        self.calls = []

    def __call__(self, sn, err_msg, detail='', status=0, no_sn=False, prefix='',
                 display=True, display_time=True):
        self.calls.append({'sn': sn, 'err_msg': err_msg, 'detail': detail, 'status': status})

    def clear(self):
        self.calls.clear()

    def with_status(self, status):
        return [c for c in self.calls if c['status'] == status]


LOG = LogRecorder()

import core.logging  # noqa: E402  (must come after the socket guard)

core.logging._default_logger = LOG

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH_ROOT = os.path.join(REPO_ROOT, 'tmp', 'unit-tests')


def make_tmpdir(testcase):
    """A fresh directory under ``tmp/unit-tests/``, removed when the test ends."""
    os.makedirs(SCRATCH_ROOT, exist_ok=True)
    d = tempfile.TemporaryDirectory(dir=SCRATCH_ROOT, ignore_cleanup_errors=True)
    testcase.addCleanup(d.cleanup)
    return d.name
