#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author  : Miyouzi (original ColorPrint.py); refactored into core by local fork
"""Logger interface + the default console+file implementation.

This is the home of the old ``ColorPrint.py`` ``err_print`` and ``Color``.

Key change vs the old module: there is NO ``import Config`` and NO module-level
``read_log_settings()`` at import time. Log behaviour (save_logs / quantity /
logs_dir) is INJECTED into a :class:`ColorPrintLogger` instance that the
app/shell constructs from the resolved settings. A module-level default
``err_print`` is still provided so legacy ``from core.logging import err_print``
(and the ``src/ColorPrint.py`` shim) keep working; it resolves its log dir
lazily and degrades safely (save to a best-effort ``data/logs`` next to the
working dir) without reading config at import time.
"""

import ctypes
import os
import sys
import platform
from datetime import datetime

from termcolor import cprint


# --- Win native console colour helper (unchanged from ColorPrint.Color) -------
# Used for colored output on Windows, code from https://blog.csdn.net/five3/article/details/7630295
class Color:
    ''' See http://msdn.microsoft.com/library/default.asp?url=/library/en-us/winprog/winprog/windows_api_reference.asp
    for information on Windows APIs.'''

    def __init__(self):
        self.FOREGROUND_RED = 0x04
        self.FOREGROUND_GREEN = 0x02
        self.FOREGROUND_BLUE = 0x01
        self.FOREGROUND_INTENSITY = 0x08
        STD_OUTPUT_HANDLE = -11
        self.handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)

    def set_cmd_color(self, color):
        """(color) -> bit
        Example: set_cmd_color(FOREGROUND_RED | FOREGROUND_GREEN | FOREGROUND_BLUE | FOREGROUND_INTENSITY)
        """
        bool = ctypes.windll.kernel32.SetConsoleTextAttribute(self.handle, color)
        return bool

    def reset_color(self):
        self.set_cmd_color(self.FOREGROUND_RED | self.FOREGROUND_GREEN | self.FOREGROUND_BLUE)

    def print_red_text(self, print_text):
        self.set_cmd_color(self.FOREGROUND_RED | self.FOREGROUND_INTENSITY)
        print(print_text)
        self.reset_color()

    def print_green_text(self, print_text):
        self.set_cmd_color(self.FOREGROUND_GREEN | self.FOREGROUND_INTENSITY)
        print(print_text)
        self.reset_color()


def _emit(msg, save_logs, logs_dir,
          sn, err_msg, detail='', status=0, no_sn=False, prefix='',
          display=True, display_time=True):
    # status has three values: 0 for normal output, 1 for error output, 2 for success output
    # err_msg is the message type/summary, ideally a four-character Chinese phrase
    # detail is the detailed message description
    # no_sn controls whether to print the sn, printed by default
    # display whether to show in the console (False outputs only to the log file)
    # display_time whether to show the time
    # format example:
    # 2019-01-30 17:22:30 status update: sn=12345 update check failed, skip and wait for the next check
    green = False

    def succeed_or_failed_print():
        # Windows native console -> custom ANSI Color(); otherwise termcolor.
        if 'Windows' in platform.system():
            clr = Color()
            if green:
                clr.print_green_text(msg)
            else:
                clr.print_red_text(msg)
        else:
            if green:
                cprint(msg, 'green', attrs=['bold'])
            else:
                cprint(msg, 'red', attrs=['bold'])

    if display_time:
        msg = prefix + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ' '
    else:
        msg = prefix

    if no_sn:
        msg = msg + err_msg + ' ' + detail
    else:
        msg = msg + err_msg + ': sn=' + str(sn) + '\t' + detail

    if display:
        if status == 0:
            print(msg)
        elif status == 1:
            # 1 is error output
            green = False
            succeed_or_failed_print()
        else:
            # 2 is success output
            green = True
            succeed_or_failed_print()

    if save_logs:
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
        log_path = os.path.join(logs_dir, datetime.now().strftime("%Y-%m-%d") + '.log')
        with open(log_path, 'a+', encoding='utf-8') as log:
            log.write(msg + '\n')


class ColorPrintLogger:
    """Default logger: console colour output + daily log files.

    Constructed by the app/shell from resolved settings so the engine, danmu
    and db layers never read config themselves. Instances are callable with the
    same signature as the legacy ``err_print`` so they drop into every call site
    unchanged.
    """

    def __init__(self, save_logs=True, logs_dir=None):
        self.save_logs = bool(save_logs)
        # logs_dir is required when save_logs is True; tolerate None for
        # display-only loggers (e.g. unit tests passing save_logs=False).
        self.logs_dir = logs_dir

    def __call__(self, sn, err_msg, detail='', status=0, no_sn=False, prefix='',
                 display=True, display_time=True):
        _emit(None, self.save_logs, self.logs_dir,
              sn, err_msg, detail=detail, status=status, no_sn=no_sn,
              prefix=prefix, display=display, display_time=display_time)

    # Convenience aliases matching a generic Logger protocol. err_print is the
    # canonical surface; these wrap it so callers that prefer info/warn/error
    # can use this object too.
    def info(self, err_msg, detail='', no_sn=True):
        self(0, err_msg, detail=detail, status=0, no_sn=no_sn)

    def warn(self, err_msg, detail='', no_sn=True):
        self(0, err_msg, detail=detail, status=1, no_sn=no_sn)

    def error(self, err_msg, detail='', no_sn=True):
        self(0, err_msg, detail=detail, status=1, no_sn=no_sn)


def _default_logs_dir():
    # Best-effort logs dir WITHOUT importing config (avoids import-time coupling).
    # Frozen: data/logs beside the exe — __file__ points INTO the read-only
    # _internal/ bundle, so it must NOT be used (would write to a read-only area).
    # Source: data/logs under the project root (parent of core/).
    if getattr(sys, 'frozen', False):
        root = os.path.dirname(sys.executable)
    else:
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    return os.path.join(root, 'data', 'logs')


# Module-level default logger so `from core.logging import err_print` keeps
# resolving for the src/ColorPrint.py shim and any not-yet-converted call site.
# It always saves logs (matching the old default-on-error behaviour) to a
# best-effort data/logs dir, computed lazily so no config read happens at import.
_default_logger = ColorPrintLogger(save_logs=True, logs_dir=_default_logs_dir())


def err_print(sn, err_msg, detail='', status=0, no_sn=False, prefix='',
              display=True, display_time=True):
    _default_logger(sn, err_msg, detail=detail, status=status, no_sn=no_sn,
                    prefix=prefix, display=display, display_time=display_time)
