#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Thin CLI one-shot entry: ``python cli.py -s <sn> [-m all] [-r 1080] ...``.
#
# The no-arg auto-download daemon lives in the shells (desktop / docker); this is
# purely the one-shot downloader, delegating to the daemon's argparse CLI
# (app.daemon.cli_main). It does NOT write the db (same as the historical CLI
# one-shot path). gevent is monkey-patched first, exactly like the shells.

from gevent import monkey
monkey.patch_all()

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from app.daemon import cli_main

if __name__ == '__main__':
    cli_main()
