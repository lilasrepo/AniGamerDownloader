#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author  : Miyouzi (original Anime.py); refactored into core by local fork
"""The download engine: ``AnimeDownloader`` (was ``Anime``), with DI.

Moved from ``src/Anime.py`` verbatim except for the inverted seams listed below.
The whole m3u8 / segment / ffmpeg / merge / danmu / notify pipeline and the
filename logic (including the dynamic zero-fill ``__episode_num_width`` +
``__get_filename``) are preserved byte-for-byte.

Inverted seams (dependency injection):

* ``settings``  -- the enriched static config dict (was ``Config.read_settings()``).
* ``cookies``   -- a ``core.cookies.CookieStore`` (was the ``Config.read_cookie`` /
  ``renew_cookies`` / ``invalid_cookie`` / ``get_cookie_time`` module functions).
* ``paths``     -- a ``core.types.Paths`` carrying working_dir / bangumi_dir /
  temp_dir / danmu_template (was settings['working_dir'] + the hard-coded
  DanmuTemplate path inside Danmu).
* ``ffmpeg``    -- the resolved ffmpeg executable path (the shell does the
  PATH-probe + sibling-exe fallback; the engine just uses it).
* ``on_progress(sn, ProgressEvent)`` -- replaces the 6 direct writes to
  ``Config.tasks_progress_rate`` (register / rate / status / filename / delete).
* ``logger``    -- replaces ``from ColorPrint import err_print``; every call site
  now uses ``self._log`` (default = the module-level ``core.logging.err_print``).
* ``settings_writer(settings)`` -- replaces ``Config.write_settings`` for the
  VIP ad-time learning side effect (preserved).

``download()`` additionally returns a ``DownloadResult`` for the core API /
unit-test contract while still mutating ``self.video_size`` /
``self.local_video_path`` / ``self.video_resolution`` for backward callers.
"""

import ftplib
import shutil
import traceback
import pyhttpx
from bs4 import BeautifulSoup
import re, time, os, platform, subprocess, requests, random, sys
from ftplib import FTP, FTP_TLS
import socket
import threading
from urllib.parse import quote

from core.danmu import Danmu
from core.logging import err_print as _default_err_print
from core.types import DownloadResult, ProgressEvent
from core import config as _config


class TryTooManyTimeError(BaseException):
    pass


def _noop_progress(sn, event):
    # Default on_progress for CLI / unit-test runs: the engine still works
    # standalone without a shared progress registry.
    pass


class AnimeDownloader:
    def __init__(self, sn, *, settings, paths, ffmpeg, cookies,
                 on_progress=None, logger=None, settings_writer=None,
                 debug_mode=False, gost_port=34173):
        self._settings = settings
        self._cookie_store = cookies
        self._cookies = cookies.read()
        self._paths = paths
        self._working_dir = paths.working_dir
        self._bangumi_dir = paths.bangumi_dir
        self._temp_dir = paths.temp_dir
        self._ffmpeg_path = ffmpeg
        self._on_progress = on_progress or _noop_progress
        self._log = logger or _default_err_print
        self._settings_writer = settings_writer or _config.write_settings
        self._gost_port = str(gost_port)

        self._session = requests.session()
        if 'firefox' in self._settings['ua'].lower():
            self._pyhttpx_session = pyhttpx.HttpSession(browser_type='firefox')
        else:
            self._pyhttpx_session = pyhttpx.HttpSession(browser_type='chrome')
        self._title = ''
        self._sn = sn
        self._bangumi_name = ''
        self._bangumi_name_orig = ''
        self._episode = ''
        self._episode_list = {}
        self._device_id = ''
        self._playlist = {}
        self._m3u8_dict = {}
        self.local_video_path = ''
        self._video_filename = ''
        self.video_resolution = 0
        self.video_size = 0
        self.realtime_show_file_size = False
        self.upload_succeed_flag = False
        self._danmu = False
        self._proxies = {}

        self.season_title_filter = re.compile('第[零一二三四五六七八九十]{1,3}季$')
        self.extra_title_filter = re.compile(r'\[(特別篇|中文配音)\]$')

        if self._settings['use_mobile_api']:
            self._log(sn, '解析模式', 'APP解析', display=False)
        else:
            self._log(sn, '解析模式', 'Web解析', display=False)

        if debug_mode:
            print('當前為debug模式')
        else:
            if self._settings['use_proxy']:  # use proxy
                self.__init_proxy()
            self.__init_header()  # http header
            self.__get_src()  # fetch web page, produce self._src (BeautifulSoup)
            self.__get_title()  # extract page title
            self.__get_bangumi_name()  # extract this anime's name
            self.__get_episode()  # extract episode code, str
            # extract episode list, structure {'episode': sn}, stored to self._episode_list, sn is int; key is str to account for movies, sp, etc.
            self.__get_episode_list()

    def __init_proxy(self):
        if self._settings['use_gost']:
            # case where gost is needed, proxy to gost
            os.environ['HTTP_PROXY'] = 'http://127.0.0.1:' + self._gost_port
            os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:' + self._gost_port
            self._proxies = {'https': 'http://127.0.0.1:' + self._gost_port,
                             'http': 'http://127.0.0.1:' + self._gost_port}
        else:
            # case where gost is not needed
            os.environ['HTTP_PROXY'] = self._settings['proxy']
            os.environ['HTTPS_PROXY'] = self._settings['proxy']
            self._proxies = {'https': self._settings['proxy'],
                             'http': self._settings['proxy']}

        if self._settings['no_proxy_akamai']:
            os.environ['NO_PROXY'] = "127.0.0.1,localhost,bahamut.akamaized.net"
        else:
            os.environ['NO_PROXY'] = "127.0.0.1,localhost"

    def renew(self):
        self.__get_src()
        self.__get_title()
        self.__get_bangumi_name()
        self.__get_episode()
        self.__get_episode_list()

    def get_sn(self):
        return self._sn

    def get_bangumi_name(self):
        if self._bangumi_name == '':
            self.__get_bangumi_name()
        return self._bangumi_name

    def get_episode(self):
        if self._episode == '':
            self.__get_episode()
        return self._episode

    def get_episode_list(self):
        if self._episode_list == {}:
            self.__get_episode_list()
        return self._episode_list

    def get_title(self):
        return self._title

    def get_filename(self):
        if self.video_resolution == 0:
            return self.__get_filename(self._settings['download_resolution'])
        else:
            return self.__get_filename(str(self.video_resolution))

    def get_resolutions(self):
        # core API convenience: the available resolution keys (e.g. ['1080','720',...]).
        return list(self.get_m3u8_dict().keys())

    def __get_src(self):
        if self._settings['use_mobile_api']:
            self._src = self.__request_json(f'https://api.gamer.com.tw/mobile_app/anime/v4/video.php?sn={self._sn}', no_cookies=True)
        else:
            req = f'https://ani.gamer.com.tw/animeVideo.php?sn={self._sn}'
            f = self.__request(req, no_cookies=True, use_pyhttpx=True)
            self._src = BeautifulSoup(f.content, "lxml")

    def __get_title(self):
        if self._settings['use_mobile_api']:
            try:
                self._title = self._src['data']['anime']['title']
            except KeyError:
                self._log(self._sn, 'ERROR: 該 sn 下真的有動畫？', status=1)
                self._episode_list = {}
                sys.exit(1)
        else:
            soup = self._src
            try:
                self._title = soup.find('div', 'anime_name').h1.string  # extract title (contains episode number)
            except (TypeError, AttributeError):
                # no anime under this sn
                self._log(self._sn, 'ERROR: 該 sn 下真的有動畫？', status=1)
                self._episode_list = {}
                sys.exit(1)

    def __get_bangumi_name(self):
        self._bangumi_name = self._title.replace('[' + self.get_episode() + ']', '').strip()  # extract anime name (strip episode suffix)
        self._bangumi_name = re.sub(r'\s+', ' ', self._bangumi_name)  # remove duplicate spaces

    def __get_episode(self):  # extract episode number

        def get_ep():
            # 20210719 Bahamut's version-tag position is jumping around again
            # https://github.com/miyouzi/aniGamerPlus/issues/109
            # first look for a number, if none look for brackets, if neither just give up and set the episode to 1
            self._episode = re.findall(r'\[\d*\.?\d* *\.?[A-Z,a-z]*(?:電影)?\]', self._title)
            if len(self._episode) > 0:
                self._episode = str(self._episode[0][1:-1])
            elif len(re.findall(r'\[.+?\]', self._title)) > 0:
                self._episode = re.findall(r'\[.+?\]', self._title)
                self._episode = str(self._episode[0][1:-1])
            else:
                self._episode = "1"

        # 20200320 found that trailing multi-version tags broke the original episode-extraction method
        # https://github.com/miyouzi/aniGamerPlus/issues/36
        # self._episode = re.findall(r'\[.+?\]', self._title)  # non-greedy match
        # self._episode = str(self._episode[-1][1:-1])  # stored as str to account for .5 episodes and sp, ova, etc.
        if self._settings['use_mobile_api']:
            get_ep()
        else:
            soup = self._src
            try:
                #  applies when an episode list exists
                self._episode = str(soup.find('li', 'playing').a.string)
            except AttributeError:
                # case where this sn has only one episode and no episode list exists
                # https://github.com/miyouzi/aniGamerPlus/issues/36#issuecomment-605065988
                # self._episode = re.findall(r'\[.+?\]', self._title)  # non-greedy match
                # self._episode = str(self._episode[0][1:-1])  # stored as str to account for .5 episodes and sp, ova, etc.
                get_ep()

    def __get_episode_list(self):
        if self._settings['use_mobile_api']:
            for _type in self._src['data']['anime']['episodes']:
                for _sn in self._src['data']['anime']['episodes'][_type]:
                    if _type == '0': # main episodes
                        self._episode_list[str(_sn['episode'])] = int(_sn["videoSn"])
                    elif _type == '1': # movie
                        self._episode_list['電影'] = int(_sn["videoSn"])
                    elif _type == '2': # special
                        self._episode_list[f'特別篇{_sn["episode"]}'] = int(_sn["videoSn"])
                    elif _type == '3': # Chinese dub
                        self._episode_list[f'中文配音{_sn["episode"]}'] = int(_sn["videoSn"])
                    else: # Chinese-dubbed movie
                        self._episode_list['中文電影'] = int(_sn["videoSn"])
        else:
            try:
                a = self._src.find('section', 'season').find_all('a')
                p = self._src.find('section', 'season').find_all('p')
                # https://github.com/miyouzi/aniGamerPlus/issues/9
                # sample https://ani.gamer.com.tw/animeVideo.php?sn=10210
                # 20190413 Bahamut split out the specials
                index_counter = {}  # tracks how many times an episode number repeats, used as an index into the list type ('main', 'special')
                if len(p) > 0:
                    p = list(map(lambda x: x.contents[0], p))
                for i in a:
                    sn = int(i['href'].replace('?sn=', ''))
                    ep = str(i.string)
                    if ep not in index_counter.keys():
                        index_counter[ep] = 0
                    if ep in self._episode_list.keys():
                        index_counter[ep] = index_counter[ep] + 1
                        ep = p[index_counter[ep]] + ep
                    self._episode_list[ep] = sn
            except AttributeError:
                # when there is only one episode, no episode list exists; self._episode_list holds only itself
                self._episode_list[self._episode] = self._sn

    def __init_header(self):
        # disguise as a browser
        host = 'ani.gamer.com.tw'
        origin = 'https://' + host
        ua = self._settings['ua']  # cookie auto-refresh requires a consistent UA
        ref = 'https://' + host + '/animeVideo.php?sn=' + str(self._sn)
        lang = 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.6'
        accept = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
        accept_encoding = 'gzip, deflate'
        cache_control = 'max-age=0'
        self._mobile_header = {
            "User-Agent": "Animad/1.16.16 (tw.com.gamer.android.animad; build:328; Android 9) okHttp/4.4.0",
            "X-Bahamut-App-Android": "tw.com.gamer.android.animad",
            "X-Bahamut-App-Version": "328",
            "Accept-Encoding": "gzip",
            "Connection": "Keep-Alive"
        }
        self._web_header = {
                "User-Agent": ua,
                "referer": ref,
                "Accept-Language": lang,
                "Accept": accept,
                "Accept-Encoding": accept_encoding,
                "Cache-Control": cache_control,
                "Origin": origin
            }
        if self._settings['use_mobile_api']:
            self._req_header = self._mobile_header
        else:
            self._req_header = self._web_header

    def __request(self, req, no_cookies=False, show_fail=True, max_retry=3, addition_header=None, use_pyhttpx = False):
        # set header
        current_header = self._req_header
        if addition_header is None:
            addition_header = {}
        if len(addition_header) > 0:
            for key in addition_header.keys():
                current_header[key] = addition_header[key]

        # fetch page
        error_cnt = 0
        if self._cookies and not no_cookies:
            cookies = self._cookies
        else:
            cookies = {}
        while True:
            try:
                if use_pyhttpx:
                    # https://github.com/miyouzi/aniGamerPlus/issues/249 the pyhttpx author made a change
                    # https://github.com/zero3301/pyhttpx/commit/4735190df741f4c00287ec948f0734fd2c21bfee
                    # moving proxy auth into the proxies URL
                    f = self._pyhttpx_session.get(req, headers=current_header, cookies=cookies, timeout=10,
                                                  proxies=self._proxies)
                else:
                    f = self._session.get(req, headers=current_header, cookies=cookies, timeout=10)
            except requests.exceptions.RequestException as e:
                if error_cnt >= max_retry >= 0:
                    raise TryTooManyTimeError('任務狀態: sn=' + str(self._sn) + ' 請求失敗次數過多！請求鏈接：\n%s' % req)
                err_detail = 'ERROR: 請求失敗！except：\n' + str(e) + '\n3s後重試(最多重試' + str(max_retry) + '次)'
                if show_fail:
                    self._log(self._sn, '任務狀態', err_detail)
                time.sleep(3)
                error_cnt += 1
            else:
                break
        # handle cookie
        if not self._cookies:
            # when the instance has no cookie yet, read it
            self._cookies = self._session.cookies
        elif 'nologinuser' not in self._cookies.keys() and 'BAHAID' not in self._cookies.keys():
            # handle guest cookie
            if 'nologinuser' in self._session.cookies.keys():
                # self._cookies['nologinuser'] = self._session.cookies['nologinuser']
                self._cookies = self._session.cookies
        else:  # if the user provided a cookie, handle cookie refresh
            if 'set-cookie' in f.headers.keys():  # detected that the server responded with set-cookie
                if 'deleted' in f.headers.get('set-cookie'):
                    # set-cookie cookie refresh gets only one chance; if another thread received it first, this returns deleted
                    # wait for another thread to refresh the cookie, then re-read the cookie

                    if self._settings['use_mobile_api'] and 'X-Bahamut-App-Android' in self._req_header:
                        # the mobile API cannot perform cookie refresh, switch back to the header to refresh the cookie
                        self._log(self._sn, '嘗試切換回 Web Header 刷新 Cookie', display=False)
                        self._req_header = self._web_header
                        self.__request('https://ani.gamer.com.tw/')  # try fetching the new cookie again
                    else:
                        self._log(self._sn, '收到cookie重置響應', display=False)
                        time.sleep(2)
                        try_counter = 0
                        succeed_flag = False
                        while try_counter < 3:  # try reading three times, give up if it fails
                            old_BAHARUNE = self._cookies['BAHARUNE']
                            self._cookie_store.cookie = None  # force a re-read
                            self._cookies = self._cookie_store.read()
                            self._log(self._sn, '讀取cookie',
                                      'cookie.txt最後修改時間: ' + self._cookie_store.get_time() + ' 第' + str(try_counter) + '次嘗試',
                                      display=False)
                            if old_BAHARUNE != self._cookies['BAHARUNE']:
                                # new cookie read successfully (because another thread may have received the new cookie)
                                succeed_flag = True
                                self._log(self._sn, '讀取cookie', '新cookie讀取成功', display=False)
                                break
                            else:
                                self._log(self._sn, '讀取cookie', '新cookie讀取失敗', display=False)
                                random_wait_time = random.uniform(2, 5)
                                time.sleep(random_wait_time)
                                try_counter = try_counter + 1
                        if not succeed_flag:
                            self._cookies = {}
                            self._log(0, '用戶cookie更新失敗! 使用遊客身份訪問', status=1, no_sn=True)
                            self._cookie_store.invalidate()  # rename the invalid cookie

                        if self._settings['use_mobile_api'] and 'X-Bahamut-App-Android' not in self._req_header:
                            # if the cookie still cannot be refreshed even after switching the header, restore the header; at least the ad is only 3s
                            self._req_header = self._mobile_header

                else:
                    # this thread received the new cookie
                    # 20220115 simplified cookie refresh logic
                    self._log(self._sn, '收到新cookie', display=False)

                    self._cookies.update(self._session.cookies)
                    self._cookie_store.renew(self._cookies, log=False)

                    key_list_str = ', '.join(self._session.cookies.keys())
                    self._log(self._sn, f'用戶cookie刷新 {key_list_str} ', display=False)

                    self.__request('https://ani.gamer.com.tw/')
                    # 20210724 Bahamut refreshes the cookie in one step
                    if 'BAHARUNE' in f.headers.get('set-cookie'):
                        self._log(0, '用戶cookie已更新', status=2, no_sn=True)
                        if self._settings['use_mobile_api']:
                            # when temporarily switching from the APP API to the Web API to update the cookie, switch back to the App Header once the cookie update succeeds
                            self._req_header = self._mobile_header
                            self._log(self._sn, '切換回 App Header 進行影片解析', display=False)

        return f

    def __request_json(self, req, no_cookies=False, show_fail=True, max_retry=3, addition_header=None, use_pyhttpx = False):
        if use_pyhttpx:
            return self.__request(req, no_cookies, show_fail, max_retry, addition_header, use_pyhttpx).json
        else:
            return self.__request(req, no_cookies, show_fail, max_retry, addition_header, use_pyhttpx).json()

    def __get_m3u8_dict(self):
        # m3u8 fetch module adapted from https://github.com/c0re100/BahamutAnimeDownloader
        def get_device_id():
            req = 'https://ani.gamer.com.tw/ajax/getdeviceid.php'
            self._device_id = self.__request_json(req)['deviceid']
            return self._device_id

        def get_playlist():
            if self._settings['use_mobile_api']:
                req = f'https://api.gamer.com.tw/mobile_app/anime/v3/m3u8.php?videoSn={str(self._sn)}&device={self._device_id}'
            else:
                req = 'https://ani.gamer.com.tw/ajax/m3u8.php?sn=' + str(self._sn) + '&device=' + self._device_id
            self._playlist = self.__request_json(req)

        def random_string(num):
            chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
            random.seed(int(round(time.time() * 1000)))
            result = []
            for i in range(num):
                result.append(chars[random.randint(0, len(chars) - 1)])
            return ''.join(result)

        def gain_access():
            if self._settings['use_mobile_api']:
                req = f'https://ani.gamer.com.tw/ajax/token.php?adID=0&sn={str(self._sn)}&device={self._device_id}'
            else:
                req = 'https://ani.gamer.com.tw/ajax/token.php?adID=0&sn=' + str(
                    self._sn) + "&device=" + self._device_id + "&hash=" + random_string(12)
            # returns basic info, used to determine whether the account is VIP
            return self.__request_json(req)

        def unlock():
            req = 'https://ani.gamer.com.tw/ajax/unlock.php?sn=' + str(self._sn) + "&ttl=0"
            f = self.__request(req)  # no response body

        def check_lock():
            req = 'https://ani.gamer.com.tw/ajax/checklock.php?device=' + self._device_id + '&sn=' + str(self._sn)
            f = self.__request(req)

        def start_ad():
            if self._settings['use_mobile_api']:
                req = f"https://api.gamer.com.tw/mobile_app/anime/v1/stat_ad.php?schedule=-1&sn={str(self._sn)}"
            else:
                req = "https://ani.gamer.com.tw/ajax/videoCastcishu.php?sn=" + str(self._sn) + "&s=194699"
            f = self.__request(req)  # no response body

        def skip_ad():
            if self._settings['use_mobile_api']:
                req = f"https://api.gamer.com.tw/mobile_app/anime/v1/stat_ad.php?schedule=-1&ad=end&sn={str(self._sn)}"
            else:
                req = "https://ani.gamer.com.tw/ajax/videoCastcishu.php?sn=" + str(self._sn) + "&s=194699&ad=end"
            f = self.__request(req)  # no response body

        def video_start():
            req = "https://ani.gamer.com.tw/ajax/videoStart.php?sn=" + str(self._sn)
            f = self.__request(req)

        def check_no_ad(error_count=10):
            if error_count == 0:
                self._log(self._sn, '廣告去除失敗! 請向開發者提交 issue!', status=1)
                sys.exit(1)

            req = "https://ani.gamer.com.tw/ajax/token.php?sn=" + str(
                self._sn) + "&device=" + self._device_id + "&hash=" + random_string(12)
            resp = self.__request_json(req)
            if 'time' in resp.keys():
                if not resp['time'] == 1:
                    self._log(self._sn, '廣告似乎還沒去除, 追加等待2秒, 剩餘重試次數 ' + str(error_count), status=1)
                    time.sleep(2)
                    skip_ad()
                    video_start()
                    check_no_ad(error_count=error_count - 1)
                else:
                    # passed the ad check
                    if error_count != 10:
                        ads_time = (10-error_count)*2 + ad_time + 2
                        self._log(self._sn, '通過廣告時間' + str(ads_time) + '秒, 記錄到配置檔案', status=2)
                        if self._settings['use_mobile_api']:
                            self._settings['mobile_ads_time'] = ads_time
                        else:
                            self._settings['ads_time'] = ads_time
                        self._settings_writer(self._settings)  # save to the config file
            else:
                self._log(self._sn, '遭到動畫瘋地區限制, 你的IP可能不被動畫瘋認可!', status=1)
                sys.exit(1)

        def parse_playlist():
            playlist_url = ""
            if self._settings['use_mobile_api']:
                playlist_url = self._playlist['data']['src']
            else:
                playlist_url = self._playlist['src']
            f = self.__request(playlist_url, no_cookies=True, addition_header={'origin': 'https://ani.gamer.com.tw'})
            url_prefix = re.sub(r'playlist.+', '', playlist_url)  # m3u8 URL prefix
            m3u8_list = re.findall(r'=\d+x\d+\n.+', f.content.decode())  # extract lines containing the resolution and m3u8 file
            m3u8_dict = {}
            for i in m3u8_list:
                key = re.findall(r'=\d+x\d+', i)[0]  # extract resolution
                key = re.findall(r'x\d+', key)[0][1:]  # extract the vertical pixel count, used as the key
                value = re.findall(r'.*chunklist.+', i)[0]  # extract the m3u8 file
                value = url_prefix + value  # assemble the full m3u8 URL
                m3u8_dict[key] = value
            self._m3u8_dict = m3u8_dict

        get_device_id()
        user_info = gain_access()
        if not self._settings['use_mobile_api']:
            unlock()
            check_lock()
            unlock()
            unlock()

        # received an error response
        # possibly a restricted-rating anime requiring login
        if 'error' in user_info.keys():
            msg = '《' + self._title + '》 '
            msg = msg + 'code=' + str(user_info['error']['code']) + ' message: ' + user_info['error']['message']
            self._log(self._sn, '收到錯誤', msg, status=1)
            sys.exit(1)

        if not user_info['vip']:
            # if the user is not VIP, then wait for the ad (20s)
            # 20200513 site update: minimum ad refresh time increased from 8s to 20s https://github.com/miyouzi/aniGamerPlus/issues/41
            # 20200806 site update: minimum ad refresh time increased from 20s to 25s https://github.com/miyouzi/aniGamerPlus/issues/55

            if self._settings['only_use_vip']:
                 self._log(self._sn, '非VIP','因為已設定只使用VIP下載，故強制停止', status=1, no_sn=True)
                 sys.exit(1)

            if self._settings['use_mobile_api']:
                ad_time = self._settings['mobile_ads_time']  # APP parse mode has a different ad time
            else:
                ad_time = self._settings['ads_time']

            self._log(self._sn, '正在等待', '《' + self.get_title() + '》 由於不是VIP賬戶, 正在等待'+str(ad_time)+'s廣告時間')
            start_ad()
            time.sleep(ad_time)
            skip_ad()
        else:
            self._log(self._sn, '開始下載', '《' + self.get_title() + '》 識別到VIP賬戶, 立即下載')

        if not self._settings['use_mobile_api']:
            video_start()
            check_no_ad()
        get_playlist()
        parse_playlist()

    def get_m3u8_dict(self):
        if not self._m3u8_dict:
            self.__get_m3u8_dict()
        return self._m3u8_dict

    def get_season_num(self, zh_num):
        zh2digit_table = {'零': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

        # digit position increments, taken from the highest digit first
        digit_num = 0
        # result
        result = 0
        # temporary storage variable
        tmp = 0

        while digit_num < len(zh_num):
            tmp_zh = zh_num[digit_num]
            tmp_num = zh2digit_table.get(tmp_zh, None)
            if tmp_num >= 10:
                if tmp == 0:
                    tmp = 1
                result = result + tmp_num * tmp
                tmp = 0
            elif tmp_num is not None:
                tmp = tmp * 10 + tmp_num
            digit_num += 1

        result = result + tmp
        return result

    def __episode_num_width(self):
        # digit count of this season's largest episode, used by __get_filename for dynamic zero-fill. takes the leading integer part of each episode
        # (including A/B split episodes like "1A"/"1B" -> take 1, "12.5" -> take 12); pure-text episodes
        # ("電影"/"特別篇"/"中文配音") have no leading number and are skipped. returns 1 when there is no numeric episode.
        max_ep = 0
        for ep in self._episode_list.keys():
            m = re.match(r'^(\d+)', str(ep))
            if m:
                max_ep = max(max_ep, int(m.group(1)))
        return len(str(max_ep)) if max_ep > 0 else 1

    def __get_filename(self, resolution, without_suffix=False):
        # handle episode-name zero-fill
        # treat zerofill as the "minimum width": dynamically widen to the digit count of this season's largest episode so the whole season pads to the same width,
        # past 100 it automatically becomes three digits, keeping file sorting correct (avoid "100" sorting before "99" lexicographically)
        zerofill = self._settings['zerofill']
        if zerofill > 1:
            zerofill = max(zerofill, self.__episode_num_width())
        # zero-fill treats zerofill as the minimum width. supports three "starts with an integer" episode forms:
        #   plain integer "12" -> "12";  float "1.5" -> "01.5";  A/B split "1A" -> "01A".
        # episodes not starting with a digit (movie/special/Chinese dub/OVA...) are left as-is without padding.
        m = re.match(r'^([+-]?)(\d+)(\.\d+)?([A-Za-z].*)?$', self._episode) if zerofill > 1 else None
        if m:
            sign, num, frac, suffix = m.group(1), m.group(2), m.group(3) or '', m.group(4) or ''
            episode = sign + num.zfill(zerofill) + frac + suffix
        else:
            episode = self._episode

        if self._settings['plex_naming']:
            # adapt to PLEX naming rules
            season = re.findall(self.season_title_filter, self._bangumi_name_orig)
            extra = re.findall(self.extra_title_filter, self._bangumi_name_orig)
            if season:
                season_num_string = ''.join(season).replace('第','').replace('季','')
                season_num = self.get_season_num(season_num_string)
                episode = '[S' + str(season_num).zfill(self._settings['zerofill']) + 'E' + episode + ']'
            elif extra:
                episode = '[E' + episode + ']' # do not classify season if the bangumi type is "特別篇" or "中文配音"
            elif episode == "電影":
                episode = '[' + episode + ']' # there is no episode num for "電影"
            else:
                episode = '[S01E' + episode + ']' # as season 1 if there is no matching above types
        else:
            episode = '[' + episode + ']'

        if self._settings['add_bangumi_name_to_video_filename']:
            # if the user wants the anime name
            bangumi_name = self._settings['customized_video_filename_prefix'] \
                           + self._bangumi_name \
                           + self._settings['customized_bangumi_name_suffix']

            filename = bangumi_name + episode  # filename with the anime name
        else:
            # if the user does not want the anime name added to the filename
            filename = self._settings['customized_video_filename_prefix'] + episode

        # add resolution suffix
        if self._settings['add_resolution_to_video_filename']:
            filename = filename + '[' + resolution + 'P]'

        if without_suffix:
            return filename  # filename up to the resolution, used by __get_temp_filename()

        # add user suffix and extension
        filename = filename + self._settings['customized_video_filename_suffix'] \
                   + '.' + self._settings['video_filename_extension']
        legal_filename = _config.legalize_filename(filename)  # remove illegal characters
        filename = legal_filename
        return filename

    def __get_temp_filename(self, resolution, temp_suffix):
        filename = self.__get_filename(resolution, without_suffix=True)
        # temp_filename is the temporary filename, renamed to the official filename after download completes
        temp_filename = filename + self._settings['customized_video_filename_suffix'] + '.' + temp_suffix \
                        + '.' + self._settings['video_filename_extension']
        temp_filename = _config.legalize_filename(temp_filename)
        return temp_filename

    def __segment_download_mode(self, resolution=''):
        # set the file storage path
        filename = self.__get_filename(resolution)
        merging_filename = self.__get_temp_filename(resolution, temp_suffix='MERGING')

        output_file = os.path.join(self._bangumi_dir, filename)  # full output path
        merging_file = os.path.join(self._temp_dir, merging_filename)

        url_path = os.path.split(self._m3u8_dict[resolution])[0]  # used to construct the full chunk URL
        temp_dir = os.path.join(self._temp_dir, str(self._sn) + '-downloading-by-AniGamerDownloader')  # temp dir named by sn
        if not os.path.exists(temp_dir):  # create temp dir
            os.makedirs(temp_dir)
        m3u8_path = os.path.join(temp_dir, str(self._sn) + '.m3u8')  # m3u8 storage location
        m3u8_text = self.__request(self._m3u8_dict[resolution], no_cookies=True).text  # request the m3u8 file
        with open(m3u8_path, 'w', encoding='utf-8') as f:  # save the m3u8 file locally
            f.write(m3u8_text)
            pass
        key_uri = re.search(r'(?<=AES-128,URI=")(.*)(?=")', m3u8_text).group()  # extract the key URL
        original_key_uri = key_uri

        if not re.match(r'http.+', key_uri):
            # https://github.com/miyouzi/aniGamerPlus/issues/46
            # if it is not a full URI
            key_uri = url_path + '/' + key_uri  # assemble the complete URI

        m3u8_key_path = os.path.join(temp_dir, 'key.m3u8key')  # key storage location
        with open(m3u8_key_path, 'wb') as f:  # save the key
            f.write(self.__request(key_uri, no_cookies=True).content)

        chunk_list = re.findall(r'media_b.+ts.*', m3u8_text)  # chunk

        limiter = threading.Semaphore(self._settings['multi_downloading_segment'])  # chunk concurrent download limiter
        total_chunk_num = len(chunk_list)
        finished_chunk_counter = 0
        failed_flag = False

        def download_chunk(uri):
            chunk_name = re.findall(r'media_b.+ts', uri)[0]  # chunk filename
            chunk_local_path = os.path.join(temp_dir, chunk_name)  # chunk path
            nonlocal failed_flag

            try:
                with open(chunk_local_path, 'wb') as f:
                    f.write(self.__request(uri, no_cookies=True,
                                           show_fail=False,
                                           max_retry=self._settings['segment_max_retry']).content)
            except TryTooManyTimeError:
                failed_flag = True
                self._log(self._sn, '下載狀態', 'Bad segment=' + chunk_name, status=1)
                limiter.release()
                sys.exit(1)
            except BaseException as e:
                failed_flag = True
                self._log(self._sn, '下載狀態', 'Bad segment=' + chunk_name + ' 發生未知錯誤: ' + str(e), status=1)
                limiter.release()
                sys.exit(1)

            # display completion percentage
            nonlocal finished_chunk_counter
            finished_chunk_counter = finished_chunk_counter + 1
            progress_rate = float(finished_chunk_counter / total_chunk_num * 100)
            progress_rate = round(progress_rate, 2)
            self._on_progress(int(self._sn), ProgressEvent(rate=progress_rate))

            if self.realtime_show_file_size:
                sys.stdout.write('\r正在下載: sn=' + str(self._sn) + ' ' + filename + ' ' + str(progress_rate) + '%  ')
                sys.stdout.flush()
            limiter.release()

        if self.realtime_show_file_size:
            # whether to show file size in real time; designed to apply only when cui downloads a single file or thread count = 1
            sys.stdout.write('正在下載: sn=' + str(self._sn) + ' ' + filename)
            sys.stdout.flush()
        else:
            self._log(self._sn, '正在下載', filename + ' title=' + self._title)

        chunk_tasks_list = []
        for chunk in chunk_list:
            chunk_uri = url_path + '/' + chunk
            task = threading.Thread(target=download_chunk, args=(chunk_uri,))
            chunk_tasks_list.append(task)
            task.daemon = True
            limiter.acquire()
            task.start()

        for task in chunk_tasks_list:  # wait for all tasks to finish
            while True:
                if failed_flag:
                    self._log(self._sn, '下載失敗', filename, status=1)
                    self.video_size = 0
                    return
                if task.is_alive():
                    time.sleep(1)
                else:
                    break

        # localize the m3u8
        # replace('\\', '\\\\') escapes the win path
        m3u8_text_local_version = m3u8_text.replace(original_key_uri, os.path.join(temp_dir, 'key.m3u8key')).replace('\\', '\\\\')
        for chunk in chunk_list:
            chunk_filename = re.findall(r'media_b.+ts', chunk)[0]  # chunk filename
            chunk_path = os.path.join(temp_dir, chunk_filename).replace('\\', '\\\\')  # chunk local path
            m3u8_text_local_version = m3u8_text_local_version.replace(chunk, chunk_path)
        with open(m3u8_path, 'w', encoding='utf-8') as f:  # save the localized m3u8
            f.write(m3u8_text_local_version)

        if self.realtime_show_file_size:
            sys.stdout.write('\n')
            sys.stdout.flush()
        self._log(self._sn, '下載狀態', filename + ' 下載完成, 正在解密合並……')
        self._on_progress(int(self._sn), ProgressEvent(status='下載完成'))

        # build the ffmpeg command
        ffmpeg_cmd = [self._ffmpeg_path,
                      '-allowed_extensions', 'ALL',
                      '-i', m3u8_path,
                      '-c', 'copy', merging_file,
                      '-y']

        if self._settings['faststart_movflags']:
            # move metadata to the head of the video file
            # this feature allows faster online playback of the video
            ffmpeg_cmd[7:7] = iter(['-movflags', 'faststart'])

        if self._settings['audio_language']:
            if self._title.find('中文') == -1:
                ffmpeg_cmd[7:7] = iter(['-metadata:s:a:0', 'language=jpn'])
            else:
                ffmpeg_cmd[7:7] = iter(['-metadata:s:a:0', 'language=chi'])

        # run ffmpeg
        run_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=(0x08000000 if os.name == 'nt' else 0))
        run_ffmpeg.communicate()
        # record file size in MB
        self.video_size = int(os.path.getsize(merging_file) / float(1024 * 1024))
        # rename
        self._log(self._sn, '下載狀態', filename + ' 解密合並完成, 本集 ' + str(self.video_size) + 'MB, 正在移至番劇目錄……')
        if os.path.exists(output_file):
            os.remove(output_file)

        if self._settings['use_copyfile_method']:
            shutil.copyfile(merging_file, output_file)  # adapt to rclone mounted drive
            os.remove(merging_file)  # delete the temporary merged file
        else:
            shutil.move(merging_file, output_file)  # this method errors out on an rclone mounted drive

        # delete the temp dir
        shutil.rmtree(temp_dir, ignore_errors=True)

        self.local_video_path = output_file  # record the save path, used for FTP upload
        self._video_filename = filename  # record the filename, used for FTP upload

        self._log(self._sn, '下載完成', filename, status=2)

    def __ffmpeg_download_mode(self, resolution=''):
        # set the file storage path
        filename = self.__get_filename(resolution)
        downloading_filename = self.__get_temp_filename(resolution, temp_suffix='DOWNLOADING')

        output_file = os.path.join(self._bangumi_dir, filename)  # full output path
        downloading_file = os.path.join(self._temp_dir, downloading_filename)

        # build the ffmpeg command
        ffmpeg_cmd = [self._ffmpeg_path,
                      '-user_agent',
                      self._settings['ua'],
                      '-headers', "Origin: https://ani.gamer.com.tw",
                      '-i', self._m3u8_dict[resolution],
                      '-c', 'copy', downloading_file,
                      '-y']

        if os.path.exists(downloading_file):
            os.remove(downloading_file)  # clean up the corpse of a failed task

        # subprocess.call(ffmpeg_cmd, creationflags=0x08000000)  # windows only
        run_ffmpeg = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, bufsize=204800, stderr=subprocess.PIPE, creationflags=(0x08000000 if os.name == 'nt' else 0))

        def check_ffmpeg_alive():
            # handle ffmpeg hangs, resource throttling, etc.; if the file size has not grown by more than 3M within 1min, judge it hung
            if self.realtime_show_file_size:  # whether to show file size in real time; designed to apply only when cui downloads a single file or thread count = 1
                sys.stdout.write('正在下載: sn=' + str(self._sn) + ' ' + filename)
                sys.stdout.flush()
            else:
                self._log(self._sn, '正在下載', filename + ' title=' + self._title)

            time.sleep(2)
            time_counter = 1
            pre_temp_file_size = 0
            while run_ffmpeg.poll() is None:

                if self.realtime_show_file_size:
                    # show file size in real time
                    if os.path.exists(downloading_file):
                        size = os.path.getsize(downloading_file)
                        size = size / float(1024 * 1024)
                        size = round(size, 2)
                        sys.stdout.write(
                            '\r正在下載: sn=' + str(self._sn) + ' ' + filename + '    ' + str(size) + 'MB      ')
                        sys.stdout.flush()
                    else:
                        sys.stdout.write('\r正在下載: sn=' + str(self._sn) + ' ' + filename + '    檔案尚未生成  ')
                        sys.stdout.flush()

                if time_counter % 60 == 0 and os.path.exists(downloading_file):
                    temp_file_size = os.path.getsize(downloading_file)
                    a = temp_file_size - pre_temp_file_size
                    if a < (3 * 1024 * 1024):
                        err_msg_detail = downloading_filename + ' 在一分鐘內僅增加' + str(
                            int(a / float(1024))) + 'KB 判定為卡死, 任務失敗!'
                        self._log(self._sn, '下載失敗', err_msg_detail, status=1)
                        run_ffmpeg.kill()
                        return
                    pre_temp_file_size = temp_file_size
                time.sleep(1)
                time_counter = time_counter + 1

        ffmpeg_checker = threading.Thread(target=check_ffmpeg_alive)  # checker thread
        ffmpeg_checker.daemon = True  # if the Anime thread is killed, the checker process should also end
        ffmpeg_checker.start()
        run = run_ffmpeg.communicate()
        return_str = str(run[1])

        if self.realtime_show_file_size:
            sys.stdout.write('\n')
            sys.stdout.flush()

        if run_ffmpeg.returncode == 0 and (return_str.find('Failed to open segment') < 0):
            # execution succeeded (ffmpeg ended normally, every segment downloaded successfully)
            if os.path.exists(output_file):
                os.remove(output_file)
            # record file size in MB
            self.video_size = int(os.path.getsize(downloading_file) / float(1024 * 1024))
            self._log(self._sn, '下載狀態', filename + '本集 ' + str(self.video_size) + 'MB, 正在移至番劇目錄……')

            if self._settings['use_copyfile_method']:
                shutil.copyfile(downloading_file, output_file)  # adapt to rclone mounted drive
                os.remove(downloading_file)  # delete the temporary merged file
            else:
                shutil.move(downloading_file, output_file)  # this method errors out on an rclone mounted drive

            self.local_video_path = output_file  # record the save path, used for FTP upload
            self._video_filename = filename  # record the filename, used for FTP upload
            self._log(self._sn, '下載完成', filename, status=2)
        else:
            err_msg_detail = filename + ' ffmpeg_return_code=' + str(
                run_ffmpeg.returncode) + ' Bad segment=' + str(return_str.find('Failed to open segment'))
            self._log(self._sn, '下載失敗', err_msg_detail, status=1)

    def download(self, resolution='', save_dir='', bangumi_tag='', realtime_show_file_size=False, rename='', classify=True):
        self.realtime_show_file_size = realtime_show_file_size
        if not resolution:
            resolution = self._settings['download_resolution']

        if save_dir:
            self._bangumi_dir = save_dir  # used when a cui user specifies downloading to the current directory

        # preserve the original title in advance
        self._bangumi_name_orig = self._title.replace('[' + self.get_episode() + ']', '').strip()  # extract anime name (strip episode suffix)
        self._bangumi_name_orig = re.sub(r'\s+', ' ', self._bangumi_name_orig)  # remove duplicate spaces

        if rename:
            bangumi_name = self._bangumi_name
            # adapt to multi-version anime
            version = re.findall(r'\[.+?\]', self._bangumi_name)  # check the anime name for a multi-version marker
            if version:  # if this anime is multi-version
                version = str(version[-1])  # extract the anime version name
                bangumi_name = bangumi_name.replace(version, '').strip()  # bangumi_name without the version name, with no leading/trailing spaces
            # if settings rename the anime
            # replace the anime name within it with the user-set one, without affecting the version suffix (if any)
            self._title = self._title.replace(bangumi_name, rename)
            self._bangumi_name = self._bangumi_name.replace(bangumi_name, rename)

        # download task starts
        self._on_progress(int(self._sn), ProgressEvent(rate=0, filename='《'+self.get_title()+'》', status='正在解析'))

        try:
            self.__get_m3u8_dict()  # fetch the m3u8 list
        except TryTooManyTimeError:
            # if something unexpected happens while fetching the m3u8, cancel this download
            self._log(self._sn, '下載狀態', '獲取 m3u8 失敗!', status=1)
            self.video_size = 0
            return DownloadResult(path='', size=0, resolution=0, ok=False, error='獲取 m3u8 失敗')

        # ffmpeg is injected by the shell/app (PATH probe + sibling-exe fallback moved to the shell).
        # if the injected path is invalid, ffmpeg will raise FileNotFoundError during the download.

        # create the directory for storing the anime, removing illegal characters
        if bangumi_tag:  # if an anime category is specified
            self._bangumi_dir = os.path.join(self._bangumi_dir, _config.legalize_filename(bangumi_tag))
        if classify:  # controls whether to create the anime folder
            if self._settings['classify_season']:  # controls whether to create the anime season subfolder
                season = re.findall(self.season_title_filter, self._bangumi_name_orig)
                extra = re.findall(self.extra_title_filter, self._bangumi_name_orig)
                if season:
                    season_num_string = ''.join(season).replace('第','').replace('季','')
                    season_num = self.get_season_num(season_num_string)
                    root_bangumi_dir = self._bangumi_name_orig.replace(str(season[0]),'') # remove season name for bangumi root dir
                    root_bangumi_dir = "".join(root_bangumi_dir.rstrip()) # remove tail space if exists
                    sub_dir = "Season "+str(season_num) # add season sub folder
                elif extra:
                    root_bangumi_dir = self._bangumi_name_orig.replace("["+str(extra[0])+"]",'') # remove extra name for bangumi root dir
                    root_bangumi_dir = "".join(root_bangumi_dir.rstrip()) # remove tail space if exists
                    sub_dir = "Specials" # add special sub folder if the bangumi type is "特別篇" or "中文配音"
                elif self.get_episode() == "電影":
                    root_bangumi_dir = self._bangumi_name_orig.replace("[電影]",'') # remove the "[電影]" tag for bangumi root dir
                    sub_dir = "Movie" # add movie sub folder if the bangumi type is "電影"
                else:
                    root_bangumi_dir = self._bangumi_name_orig # for bangumi root dir
                    root_bangumi_dir = "".join(root_bangumi_dir.rstrip()) # remove tail space if exists
                    sub_dir = "Season 1" # as season 1 if there is no matching season
                # if settings rename the anime
                # replace the anime root directory name within it with the user-set one
                if rename:
                    root_bangumi_dir = self._bangumi_name
                self._bangumi_dir = os.path.join(self._bangumi_dir, _config.legalize_filename(root_bangumi_dir), sub_dir)
            else:
                self._bangumi_dir = os.path.join(self._bangumi_dir, _config.legalize_filename(self._bangumi_name))

        if not os.path.exists(self._bangumi_dir):
            try:
                os.makedirs(self._bangumi_dir)  # create folders categorized by anime
            except FileExistsError as e:
                self._log(self._sn, '下載狀態', '欲建立的番劇資料夾已存在 ' + str(e), display=False)

        if not os.path.exists(self._temp_dir):  # create the temp folder
            try:
                os.makedirs(self._temp_dir)
            except FileExistsError as e:
                self._log(self._sn, '下載狀態', '欲建立的臨時資料夾已存在 ' + str(e), display=False)

        # if the specified resolution does not exist, pick the closest available resolution
        if resolution not in self._m3u8_dict.keys():
            if self._settings['lock_resolution']:
                # if the user has set resolution lock, cancel the download
                err_msg_detail = '指定畫質不存在, 因當前鎖定了畫質, 下載取消. 可用的畫質: ' + 'P '.join(self._m3u8_dict.keys()) + 'P'
                self._log(self._sn, '任務狀態', err_msg_detail, status=1)
                return DownloadResult(path='', size=0, resolution=0, ok=False, error='指定畫質不存在且鎖定畫質')

            resolution_list = map(lambda x: int(x), self._m3u8_dict.keys())
            resolution_list = list(resolution_list)
            flag = 9999
            closest_resolution = 0
            for i in resolution_list:
                a = abs(int(resolution) - i)
                if a < flag:
                    flag = a
                    closest_resolution = i
            # resolution_list.sort()
            # resolution = str(resolution_list[-1])  # pick the highest available resolution
            resolution = str(closest_resolution)
            err_msg_detail = '指定畫質不存在, 選取最近可用畫質: ' + resolution + 'P'
            self._log(self._sn, '任務狀態', err_msg_detail, status=1)
        self.video_resolution = int(resolution)

        # parsing complete, start downloading
        self._on_progress(int(self._sn), ProgressEvent(status='正在下載'))
        self._on_progress(int(self._sn), ProgressEvent(filename=self.get_filename()))

        if self._settings['segment_download_mode']:
            self.__segment_download_mode(resolution)
        else:
            self.__ffmpeg_download_mode(resolution)

        # task complete, remove from the task progress table
        self._on_progress(int(self._sn), ProgressEvent(done=True))

        # download danmaku
        if self._danmu:
            try:
                full_filename = os.path.join(self._bangumi_dir, self.__get_filename(resolution)).replace('.' + self._settings['video_filename_extension'], '.ass')
                d = Danmu(self._sn, full_filename, self._cookie_store.read(),
                          danmu_template=self._paths.danmu_template, logger=self._log)
                d.download(self._settings['danmu_ban_words'])
            except BaseException as e:
                self._log(self._sn, '彈幕異常', '下載彈幕時發生未知錯誤: '+str(e), status=1)
                self._log(self._sn, '彈幕異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)

        # push CQ notification
        if self._settings['coolq_notify']:
            try:
                msg = '【AniGamerDownloader消息】\n《' + self._video_filename + '》下載完成, 本集 ' + str(self.video_size) + ' MB'
                if self._settings['coolq_settings']['message_suffix']:
                    # append user info
                    msg = msg + '\n\n' + self._settings['coolq_settings']['message_suffix']

                for query in self._settings['coolq_settings']['query']:
                    if '?' not in query:
                        query = query + '?'
                    else:
                        query = query + '&'
                    req = query + self._settings['coolq_settings']['msg_argument_name'] + '=' + quote(msg)
                    self.__request(req, no_cookies=True)
            except BaseException as e:
                self._log(self._sn, 'CQ NOTIFY ERROR', 'Exception: ' + str(e), status=1)

        # push TG notification
        if self._settings['telebot_notify']:
            try:
                msg = '【AniGamerDownloader消息】\n《' + self._video_filename + '》下載完成, 本集 ' + str(self.video_size) + ' MB'
                vApiTokenTelegram = self._settings['telebot_token']
                try:
                    if self._settings['telebot_use_chat_id'] and self._settings['telebot_chat_id']:  # manually specify the send target
                        chat_id = self._settings['telebot_chat_id']
                    else:
                        apiMethod = "getUpdates"
                        api_url = "https://api.telegram.org/bot" + vApiTokenTelegram + "/" + apiMethod # Telegram bot api url
                        response = self.__request_json(api_url)
                        chat_id = response["result"][0]["message"]["chat"]["id"] # Get chat id
                    try:
                        api_method = "sendMessage"
                        req = "https://api.telegram.org/bot" \
                                + vApiTokenTelegram \
                                + "/" \
                                + api_method \
                                + "?chat_id=" \
                                + str(chat_id) \
                                + "&text=" \
                                + str(msg)
                        self.__request(req, no_cookies=True) # Send msg to telegram bot
                    except:
                        self._log(self._sn, 'TG NOTIFY ERROR', "Exception: Send msg error\nReq: " + req, status=1) # Send mag error
                except:
                    self._log(self._sn, 'TG NOTIFY ERROR', "Exception: Invalid access token\nToken: " + vApiTokenTelegram, status=1) # Cannot find chat id
            except BaseException as e:
                self._log(self._sn, 'TG NOTIFY ERROR', 'Exception: ' + str(e), status=1)

        # push notification to Discord
        if self._settings['discord_notify']:
            try:
                msg = '【AniGamerDownloader消息】\n《' + self._video_filename + '》下載完成，本集 ' + str(self.video_size) + ' MB'
                url = self._settings['discord_token']
                data = {
                    'content': None,
                    'embeds': [{
                        'title': '下載完成',
                        'description': msg,
                        'color': '5814783',
                        'author': {
                            'name': '🔔 動畫瘋'
                        }}]}
                r = requests.post(url, json=data)
                if r.status_code != 204:
                    self._log(self._sn, 'discord NOTIFY ERROR', "Exception: Send msg error\nReq: " + r.text, status=1)
            except:
                self._log(self._sn, 'Discord NOTIFY UNKNOWN ERROR', 'Exception: ' + str(e), status=1)

        # plex auto-refresh media library
        if self._settings['plex_refresh']:
            try:
                url = 'https://{plex_url}/library/sections/{plex_section}/refresh?X-Plex-Token={plex_token}'.format(
                    plex_url=self._settings['plex_url'],
                    plex_section=self._settings['plex_section'],
                    plex_token=self._settings['plex_token']
                )
                r = requests.get(url)
                if r.status_code != 200:
                    self._log(self._sn, 'Plex auto Refresh ERROR', status=1)
            except:
                self._log(self._sn, 'Plex auto Refresh UNKNOWN ERROR', 'Exception: ' + str(e), status=1)

        return DownloadResult(path=self.local_video_path, size=self.video_size,
                              resolution=self.video_resolution,
                              ok=bool(self.local_video_path), error=None)

    def upload(self, bangumi_tag='', debug_file=''):
        first_connect = True  # marks whether this is the first connection; the first connection deletes the temporary cache dir
        tmp_dir = str(self._sn) + '-uploading-by-AniGamerDownloader'

        if debug_file:
            self.local_video_path = debug_file

        if not os.path.exists(self.local_video_path):  # if the file does not exist, return failure directly
            return self.upload_succeed_flag

        if not self._video_filename:  # used for upload-only, extract the filename
            self._video_filename = os.path.split(self.local_video_path)[-1]

        socket.setdefaulttimeout(20)  # timeout of 20s

        if self._settings['ftp']['tls']:
            ftp = FTP_TLS()  # FTP over TLS
        else:
            ftp = FTP()

        def connect_ftp(show_err=True):
            ftp.encoding = 'utf-8'  # fix Chinese garbled text
            err_counter = 0
            connect_flag = False
            while err_counter <= 3:
                try:
                    ftp.connect(self._settings['ftp']['server'], self._settings['ftp']['port'])  # connect FTP
                    ftp.login(self._settings['ftp']['user'], self._settings['ftp']['pwd'])  # log in
                    connect_flag = True
                    break
                except ftplib.error_temp as e:
                    if show_err:
                        if 'Too many connections' in str(e):
                            detail = self._video_filename + ' 當前FTP連接數過多, 5分鐘後重試, 最多重試三次: ' + str(e)
                            self._log(self._sn, 'FTP狀態', detail, status=1)
                        else:
                            detail = self._video_filename + ' 連接FTP時發生錯誤, 5分鐘後重試, 最多重試三次: ' + str(e)
                            self._log(self._sn, 'FTP狀態', detail, status=1)
                    err_counter = err_counter + 1
                    for i in range(5 * 60):
                        time.sleep(1)
                except BaseException as e:
                    if show_err:
                        detail = self._video_filename + ' 在連接FTP時發生無法處理的異常:' + str(e)
                        self._log(self._sn, 'FTP狀態', detail, status=1)
                    break

            if not connect_flag:
                self._log(self._sn, '上傳失敗', self._video_filename, status=1)
                return connect_flag  # if the connection fails, give up directly

            ftp.voidcmd('TYPE I')  # binary mode

            if self._settings['ftp']['cwd']:
                try:
                    ftp.cwd(self._settings['ftp']['cwd'])  # enter the user-specified directory
                except ftplib.error_perm as e:
                    if show_err:
                        self._log(self._sn, 'FTP狀態', '進入指定FTP目錄時出錯: ' + str(e), status=1)

            if bangumi_tag:  # anime category
                try:
                    ftp.cwd(bangumi_tag)
                except ftplib.error_perm:
                    try:
                        ftp.mkd(bangumi_tag)
                        ftp.cwd(bangumi_tag)
                    except ftplib.error_perm as e:
                        if show_err:
                            self._log(self._sn, 'FTP狀態', '建立目錄番劇目錄時發生異常, 你可能沒有權限建立目錄: ' + str(e), status=1)

            # categorize the anime
            ftp_bangumi_dir = _config.legalize_filename(self._bangumi_name)  # ensure it is legal
            try:
                ftp.cwd(ftp_bangumi_dir)
            except ftplib.error_perm:
                try:
                    ftp.mkd(ftp_bangumi_dir)
                    ftp.cwd(ftp_bangumi_dir)
                except ftplib.error_perm as e:
                    if show_err:
                        detail = '你可能沒有權限建立目錄(用於分類番劇), 影片檔案將會直接上傳, 收到異常: ' + str(e)
                        self._log(self._sn, 'FTP狀態', detail, status=1)

            # delete the old temporary folder
            nonlocal first_connect
            if first_connect:  # first connection
                remove_dir(tmp_dir)
                first_connect = False  # mark the first connection as complete

            # create a new temporary folder
            # create a temporary folder because pure-ftpd changes the filename to an unpredictable name during resume
            # normally interrupting the transfer changes the name back, but an unexpected disconnect does not; to handle this case
            # we need to fetch pure-ftpd's resume cache file with an unknown filename, so a temporary folder is created to avoid mixing it up with other videos' cache files
            try:
                ftp.cwd(tmp_dir)
            except ftplib.error_perm:
                ftp.mkd(tmp_dir)
                ftp.cwd(tmp_dir)

            return connect_flag

        def exit_ftp(show_err=True):
            try:
                ftp.quit()
            except BaseException as e:
                if show_err and self._settings['ftp']['show_error_detail']:
                    self._log(self._sn, 'FTP狀態', '將強制關閉FTP連接, 因為在退出時收到異常: ' + str(e))
                ftp.close()

        def remove_dir(dir_name):
            try:
                ftp.rmd(dir_name)
            except ftplib.error_perm as e:
                if 'Directory not empty' in str(e):
                    # if the directory is not empty, delete the files inside
                    ftp.cwd(dir_name)
                    del_all_files()
                    ftp.cwd('..')
                    ftp.rmd(dir_name)  # after deleting the inner files, delete the folder
                elif 'No such file or directory' in str(e):
                    pass
                else:
                    # other non-empty directory error
                    raise e

        def del_all_files():
            try:
                for file_need_del in ftp.nlst():
                    if not re.match(r'^(\.|\.\.)$', file_need_del):
                        ftp.delete(file_need_del)
                        # print('deleted file: ' + file_need_del)
            except ftplib.error_perm as resp:
                if not str(resp) == "550 No files found":
                    raise

        if not connect_ftp():  # connect FTP
            return self.upload_succeed_flag  # if the connection fails

        self._log(self._sn, '正在上傳', self._video_filename + ' title=' + self._title + '……')
        try_counter = 0
        video_filename = self._video_filename  # video_filename may store the pure-ftpd cache filename
        max_try_num = self._settings['ftp']['max_retry_num']
        local_size = os.path.getsize(self.local_video_path)  # local file size
        while try_counter <= max_try_num:
            try:
                if try_counter > 0:
                    # handling after the transfer is interrupted
                    detail = self._video_filename + ' 發生異常, 重連FTP, 續傳檔案, 將重試最多' + str(max_try_num) + '次……'
                    self._log(self._sn, '上傳狀態', detail, status=1)
                    if not connect_ftp():  # reconnect
                        return self.upload_succeed_flag

                    # work around the damn Pure-Ftpd issue where resuming once renames the file and prevents further resume.
                    # normally on a clean transfer close Pure-Ftpd changes the name back, but on an unexpected network interruption it does not, leaving the temporary filename
                    # this block handles that case
                    try:
                        for i in ftp.nlst():
                            if 'pureftpd-upload' in i:
                                # found the pure-ftpd cache, grab the cache directly to resume
                                video_filename = i
                    except ftplib.error_perm as resp:
                        if not str(resp) == "550 No files found":  # not a file-not-found error, raise the exception
                            raise
                # resume from breakpoint
                try:
                    # requires the FTP server to support resume
                    ftp_binary_size = ftp.size(video_filename)  # remote file byte count
                except ftplib.error_perm:
                    # if the file does not exist
                    ftp_binary_size = 0
                except OSError:
                    try_counter = try_counter + 1
                    continue

                ftp.voidcmd('TYPE I')  # binary mode
                conn = ftp.transfercmd('STOR ' + video_filename, ftp_binary_size)  # ftp server filename and offset address
                with open(self.local_video_path, 'rb') as f:
                    f.seek(ftp_binary_size)  # start reading from the breakpoint
                    while True:
                        block = f.read(1048576)  # read 1M
                        conn.sendall(block)  # send the block
                        if not block:
                            time.sleep(3)  # wait a moment to let sendall() finish
                            break

                conn.close()

                self._log(self._sn, '上傳狀態', '檢查遠端檔案大小是否與本地一致……')
                exit_ftp(False)
                connect_ftp(False)
                # without reconnecting, the remote file size query below would return None, baffling...
                # if sendall() did not complete it returns 500 Unknown command
                err_counter = 0
                remote_size = 0
                while err_counter < 3:
                    try:
                        remote_size = ftp.size(video_filename)  # remote file size
                        break
                    except ftplib.error_perm as e1:
                        self._log(self._sn, 'FTP狀態', 'ftplib.error_perm: ' + str(e1))
                        remote_size = 0
                        break
                    except OSError as e2:
                        self._log(self._sn, 'FTP狀態', 'OSError: ' + str(e2))
                        remote_size = 0
                        connect_ftp(False)  # reconnect after disconnect
                        err_counter = err_counter + 1

                if remote_size is None:
                    self._log(self._sn, 'FTP狀態', 'remote_size is None')
                    remote_size = 0
                # remote file size fetch failed, possibly the file does not exist or is glitching
                # in that case fetching the remote byte count above would be 0, causing a re-download, so the files in the cache dir should be cleared now
                # to avoid resuming with the wrong file later
                if remote_size == 0:
                    del_all_files()

                if remote_size != local_size:
                    # if the remote file size differs from local
                    # print('remote_size='+str(remote_size))
                    # print('local_size ='+str(local_size))
                    detail = self._video_filename + ' 在遠端為' + str(
                        round(remote_size / float(1024 * 1024), 2)) + 'MB' + ' 與本地' + str(
                        round(local_size / float(1024 * 1024), 2)) + 'MB 不一致! 將重試最多' + str(max_try_num) + '次'
                    self._log(self._sn, '上傳狀態', detail, status=1)
                    try_counter = try_counter + 1
                    continue  # resume

                # after a successful upload
                ftp.cwd('..')  # return to the parent directory, i.e. exit the temp dir
                try:
                    # if a file with the same name exists, delete it
                    ftp.size(self._video_filename)
                    ftp.delete(self._video_filename)
                except ftplib.error_perm:
                    pass
                ftp.rename(tmp_dir + '/' + video_filename, self._video_filename)  # move the video out of the temp file, renaming it along the way
                remove_dir(tmp_dir)  # delete the temp dir
                self.upload_succeed_flag = True  # mark the upload as successful
                break

            except ConnectionResetError as e:
                if self._settings['ftp']['show_error_detail']:
                    detail = self._video_filename + ' 在上傳過程中網路被重置, 將重試最多' + str(max_try_num) + '次' + ', 收到異常: ' + str(e)
                    self._log(self._sn, '上傳狀態', detail, status=1)
                try_counter = try_counter + 1
            except TimeoutError as e:
                if self._settings['ftp']['show_error_detail']:
                    detail = self._video_filename + ' 在上傳過程中超時, 將重試最多' + str(max_try_num) + '次, 收到異常: ' + str(e)
                    self._log(self._sn, '上傳狀態', detail, status=1)
                try_counter = try_counter + 1
            except socket.timeout as e:
                if self._settings['ftp']['show_error_detail']:
                    detail = self._video_filename + ' 在上傳過程socket超時, 將重試最多' + str(max_try_num) + '次, 收到異常: ' + str(e)
                    self._log(self._sn, '上傳狀態', detail, status=1)
                try_counter = try_counter + 1

        if not self.upload_succeed_flag:
            self._log(self._sn, '上傳失敗', self._video_filename + ' 放棄上傳!', status=1)
            exit_ftp()
            return self.upload_succeed_flag

        self._log(self._sn, '上傳完成', self._video_filename, status=2)
        exit_ftp()  # log out of FTP
        return self.upload_succeed_flag

    def get_info(self):
        self._log(self._sn, '顯示資訊')
        indent = '                    '
        self._log(0, indent+'影片標題:', '\"' + self.get_title() + '\"', no_sn=True, display_time=False)
        self._log(0, indent+'番劇名稱:', '\"' + self.get_bangumi_name() + '\"', no_sn=True, display_time=False)
        self._log(0, indent+'劇集標題:', '\"' + self.get_episode() + '\"', no_sn=True, display_time=False)
        self._log(0, indent+'參考檔名:', '\"' + self.get_filename() + '\"', no_sn=True, display_time=False)
        self._log(0, indent+'可用解析度', 'P '.join(self.get_m3u8_dict().keys()) + 'P\n', no_sn=True, display_time=False)

    def enable_danmu(self):
        self._danmu = True

    def set_resolution(self, resolution):
        self.video_resolution = int(resolution)


if __name__ == '__main__':
    pass
