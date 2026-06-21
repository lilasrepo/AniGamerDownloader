#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cookie store: read / test / invalidate / renew / write the Bahamut cookie.

Moved from ``src/Config.py`` (read_cookie / test_cookie / invalid_cookie /
renew_cookies / get_cookie_time, lines 685-786). The old module-global
``cookie`` becomes per-instance state on :class:`CookieStore`; ``cookie_path``
is injected via the constructor instead of a module global. ``__color_print``
calls become the injected ``self._log`` (default = the console+file logger).

NEW: :meth:`CookieStore.write` writes ``cookie.txt`` from a raw single-line
``Cookie:`` header string (GUI cookie editing), mirroring
``Config.write_sn_list``.
"""

import os
import re
import random
import time
from urllib.parse import quote

from core.logging import err_print as _default_err_print


def _time_stamp_to_time(timestamp):
    # convert a timestamp to a time: 1479264792 to 2016-11-16 10:53:12
    timeStruct = time.localtime(timestamp)
    return time.strftime('%Y-%m-%d %H:%M:%S', timeStruct)


class CookieStore:
    """Holds the cookie file path + the parsed in-memory cookie dict.

    A single instance is shared wherever the cookie is needed (engine, danmu,
    web). ``read()`` caches into ``self.cookie`` (None = not yet read); the
    other methods reset that cache exactly as the old module globals did.
    """

    def __init__(self, cookie_path, logger=None):
        self.cookie_path = cookie_path
        self.cookie = None  # None = not yet read; {} = read but no valid cookie
        self._log = logger or _default_err_print

    def test(self):
        # test whether cookie.txt exists and can be read normally, and log the result
        self.read(log=True)

    def read(self, log=False):
        # if the cookie is already in memory, return directly
        if self.cookie is not None:
            return self.cookie
        cookie_path = self.cookie_path
        # support the old cookie filename
        old_cookie_path = cookie_path.replace('cookie.txt', 'cookies.txt')
        if os.path.exists(old_cookie_path):
            os.rename(old_cookie_path, cookie_path)
        # sanity guard https://github.com/miyouzi/aniGamerPlus/issues/5
        error_cookie_path = cookie_path.replace('cookie.txt', 'cookie.txt.txt')
        if os.path.exists(error_cookie_path):
            os.rename(error_cookie_path, cookie_path)
        # the user can store the cookie in the program's directory, saved as cookies.txt, UTF-8 encoded
        if os.path.exists(cookie_path):
            # prevent an error when the cookie file is empty
            if os.path.getsize(cookie_path) == 0:
                return None
            # remove BOM / transcode non-UTF-8 (corresponds to the original Config.read_cookie, sanity guard issue #5)
            from core.config import check_encoding
            check_encoding(cookie_path)
            if log:
                self._log(0, '讀取cookie', detail='發現cookie檔案', no_sn=True, display=False)
            with open(cookie_path, 'r', encoding='utf-8') as f:
                for line in f.readlines():
                    if not line.isspace():  # skip blank lines
                        cookies = line.replace('\n', '')  # delete the newline character
                        cookies = dict([list(map(lambda x: quote(x, safe='') if re.match(r'[一-龥]', x) else x,  l.split("=", 1))) for l in cookies.split("; ")])
                        cookies.pop('ckBH_lastBoard', 404)
                        self.cookie = cookies
                        if log:
                            self._log(0, '讀取cookie', detail='已讀取cookie', no_sn=True, display=False)
                        return self.cookie  # the cookie is a single line, return as soon as it is read
        else:
            self._log(0, '讀取cookie', detail='未發現cookie檔案', no_sn=True, display=False)
            self.cookie = {}
            return self.cookie
        # if nothing was read at all (empty file)
        self._log(0, '讀取cookie', detail='cookie檔案為空', no_sn=True, status=1)
        self.invalidate()
        self.cookie = {}
        return self.cookie

    def invalidate(self):
        # when the cookie is invalid, rename it to avoid repeatedly trying the invalid cookie
        cookie_path = self.cookie_path
        if os.path.exists(cookie_path):
            invalid_cookie_path = cookie_path.replace('cookie.txt', 'invalid_cookie.txt')
            try:
                self.cookie = None  # reset the already-read cookie
                if os.path.exists(invalid_cookie_path):
                    os.remove(invalid_cookie_path)
                os.rename(cookie_path, invalid_cookie_path)
            except BaseException as e:
                self._log(0, 'cookie狀態', '嘗試標記失效cookie時遇到未知錯誤: ' + str(e), no_sn=True, status=1)
            else:
                self._log(0, 'cookie狀態', '已成功標記失效cookie', no_sn=True, display=False)

    def get_time(self):
        # get the cookie modification time
        cookie_time = os.path.getmtime(self.cookie_path)
        return _time_stamp_to_time(cookie_time)

    def renew(self, new_cookie, log=True):
        self.cookie = None  # reset the cookie
        new_cookie_str = ''
        for key, value in new_cookie.items():
            new_cookie_str = new_cookie_str + key + '=' + value + '; '
        new_cookie_str = new_cookie_str[0:-2]
        try_counter = 0
        while True:
            try:
                with open(self.cookie_path, 'w', encoding='utf-8') as f:
                    f.write(new_cookie_str)
            except BaseException as e:
                if try_counter > 3:
                    self._log(0, '新cookie儲存失敗! 發生異常: ' + str(e), status=1, no_sn=True)
                    break
                random_wait_time = random.uniform(2, 5)
                time.sleep(random_wait_time)
                try_counter = try_counter + 1
            else:
                if log:
                    self._log(0, '新cookie儲存成功', no_sn=True, display=False)
                break

    def write(self, content):
        # NEW: write cookie.txt from a Cookie: header string pasted in the GUI (mirrors write_sn_list).
        # reset the in-memory cache after writing so the next read() re-parses. Stored on a single line, with surrounding whitespace and newlines stripped.
        self.cookie = None
        with open(self.cookie_path, 'w', encoding='utf-8') as f:
            f.write(content.strip())
