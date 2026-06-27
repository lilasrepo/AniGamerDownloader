#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Author  : Miyouzi (original aniGamerPlus.py); refactored into app by local fork
"""The download daemon + CLI body, built on :mod:`core` + :class:`app.state.AppState`.

Moved from ``src/aniGamerPlus.py`` (build_anime / read/insert/update db wrappers /
redownload / worker / check_tasks / __download_only / __get_info_only /
__get_danmu_only / __cui / download_cd_counter / proxy+gost helpers /
acquire_single_instance / run_daemon / version-check / signal handlers).

Inverted seams vs the original module:

* The six runtime globals (``tasks_progress_rate`` / ``pending_tasks`` /
  ``batch_download_paused`` / ``daemon_running`` / ``shutting_down`` /
  ``force_check_now``) and the queue/limiter globals now live on ONE shared
  :class:`AppState` instance (``state``). The daemon and the web layer read/write
  the SAME ``state`` object.
* Engine instances are built via :func:`build_anime` -> ``core.AnimeDownloader``
  with DI (settings / paths / ffmpeg / cookies / on_progress / logger). The
  ``on_progress`` callback is ``state.on_progress`` (writes
  ``state.tasks_progress_rate``), replacing the engine's old direct writes.
* DB CRUD delegates to :mod:`core.db` (db_path injected).
* Cookie read/test delegates to a shared :class:`core.cookies.CookieStore`.

``gevent.monkey.patch_all()`` is NOT done here — it stays at the shell/entry
layer (``src/aniGamerPlus.py`` shim, ``shells/desktop.py``,
``shells/docker/entrypoint.py``) BEFORE this module is imported, so the engine's
threading/socket calls run patched under the daemon and unpatched under the CLI.
"""

import os
import sys
import time
import re
import random
import traceback
import argparse
import signal
import threading
import subprocess
import platform
import socket

import pip_system_certs.wrapt_requests  # noqa: F401
import requests

from core import config as Config
from core.engine import AnimeDownloader, TryTooManyTimeError
from core.cookies import CookieStore
from core.danmu import Danmu
from core.types import Paths
from core.logging import err_print
from core import db as _db
from app.state import AppState

# ---------------------------------------------------------------------------
# Shared module state. ONE AppState is shared between this daemon and the web
# app (app.web.server imports `state` from here). Built from the live settings.
# ---------------------------------------------------------------------------
settings = Config.read_settings()
working_dir = settings['working_dir']
db_path = os.path.join(working_dir, 'data', 'aniGamer.db')
cookie_path = Config.cookie_path
cookies = CookieStore(cookie_path, logger=err_print)

state = AppState(multi_thread=settings['multi-thread'], multi_upload=settings['multi_upload'])

sn_dict = Config.read_sn_list()
danmu = settings['danmu']

SINGLE_INSTANCE_PORT = 47763   # fixed loopback port used by the single-instance lock
_single_instance_socket = None  # held after binding until process exit (avoids GC)
thread_tasks = []
gost_subprocess = None  # holds gost's subprocess.Popen object, used to kill gost on exit
_ffmpeg_path = None     # resolved ffmpeg executable path (lazy, injected into the engine)


def port_is_available(port):
    # Check whether a port is available (not in use); returns True if available
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    return result != 0


def _random_gost_port():
    random_port = random.randint(40000, 60000)
    while not port_is_available(random_port):
        random_port = random.randint(40000, 60000)
    return random_port


# A concrete port value (the original reassigned the gost_port() function result
# onto the same name at module load: aniGamerPlus.py:932 `gost_port = gost_port()`).
gost_port = _random_gost_port()


def locate_ffmpeg():
    # ffmpeg location (originally in Anime.download, moved to the app/shell layer after the refactor).
    # PATH takes priority, otherwise look for a sibling exe next to working_dir. If neither exists, raise FileNotFoundError.
    # The result is cached at module level in _ffmpeg_path and injected into each AnimeDownloader instance.
    global _ffmpeg_path
    if _ffmpeg_path:
        return _ffmpeg_path
    check_ffmpeg = subprocess.Popen('ffmpeg -h', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=(0x08000000 if os.name == 'nt' else 0))
    if check_ffmpeg.stdout.readlines():  # check whether ffmpeg is on the system path
        _ffmpeg_path = 'ffmpeg'
    else:
        if 'Windows' in platform.system():
            _ffmpeg_path = os.path.join(working_dir, 'ffmpeg.exe')
        else:
            _ffmpeg_path = os.path.join(working_dir, 'ffmpeg')
        if not os.path.exists(_ffmpeg_path):
            err_print(0, '本項目依賴於ffmpeg, 但ffmpeg未找到', status=1, no_sn=True)
            raise FileNotFoundError  # raise an exception if ffmpeg is not found in the local directory either
    return _ffmpeg_path


def _build_paths():
    return Paths(
        working_dir=settings['working_dir'],
        bangumi_dir=settings['bangumi_dir'],
        temp_dir=settings['temp_dir'],
        danmu_template=Config.get_danmu_template(),
    )


def build_anime(sn):
    anime = {'anime': None, 'failed': True}
    try:
        ffmpeg = locate_ffmpeg()
        if settings['use_gost']:
            # if using gost, pick a random gost listen port
            a = AnimeDownloader(sn, settings=settings, paths=_build_paths(), ffmpeg=ffmpeg,
                                cookies=cookies, on_progress=state.on_progress, logger=err_print,
                                settings_writer=Config.write_settings, gost_port=gost_port)
        else:
            a = AnimeDownloader(sn, settings=settings, paths=_build_paths(), ffmpeg=ffmpeg,
                                cookies=cookies, on_progress=state.on_progress, logger=err_print,
                                settings_writer=Config.write_settings)
        anime['anime'] = a
        anime['failed'] = False

        if danmu:
            anime['anime'].enable_danmu()

    except TryTooManyTimeError:
        err_print(sn, '抓取失敗', '影片資訊抓取失敗!', status=1)
    except BaseException as e:
        err_print(sn, '抓取失敗', '抓取影片資訊時發生未知錯誤: '+str(e), status=1)
        err_print(sn, '抓取異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)

    # sn parse cooldown
    if settings['parse_sn_cd'] > 0:
        err_print("更新資訊", "SN 解析冷卻 " + str(settings['parse_sn_cd']) + " 秒", no_sn=True)
        time.sleep(settings['parse_sn_cd'])

    return anime


# ---------------------------------------------------------------------------
# DB wrappers: thin adapters over core.db that bind the injected db_path so the
# call sites below (and Server.py) keep the original zero/one-arg signatures.
# ---------------------------------------------------------------------------
def read_db_all():
    return _db.read_db_all(db_path)


def read_db(sn):
    return _db.read_db(db_path, sn)


def insert_db(anime):
    return _db.insert_db(db_path, anime, logger=err_print)


def update_db(anime):
    return _db.update_db(db_path, anime)


def reset_db_status(sn):
    return _db.reset_db_status(db_path, sn)


def init_db():
    _db.init_db(db_path)


def redownload(sn):
    # Web dashboard "download now": immediately re-download a given sn, updating the database when done.
    sn = int(sn)

    # in-flight dedup
    state.redownloading_locker.acquire()
    if sn in state.redownloading:
        state.redownloading_locker.release()
        err_print(sn, '重新下載', '該集正在下載中, 跳過重複請求', status=1)
        return False
    state.redownloading.add(sn)
    state.redownloading_locker.release()

    state.thread_limiter.acquire()
    try:
        anime = build_anime(sn)
        if anime['failed']:
            state.thread_limiter.release()
            err_print(sn, '重新下載', '影片資訊抓取失敗, 取消', status=1)
            return False
        anime = anime['anime']

        try:
            anime.download(settings['download_resolution'], classify=settings['classify_bangumi'])
        except BaseException as e:
            err_print(sn, '重新下載異常', '發生未知錯誤: ' + str(e), status=1)
            err_print(sn, '重新下載異常', '異常詳情:\n' + traceback.format_exc(), status=1, display=False)
            anime.video_size = 0

        if anime.video_size < 5:
            state.thread_limiter.release()
            if sn in state.tasks_progress_rate.keys():
                del state.tasks_progress_rate[sn]
            err_print(sn, '重新下載失敗', 'title=\"' + anime.get_title() + '\"', status=1)
            return False

        try:
            try:
                read_db(sn)
                update_db(anime)
            except IndexError:
                insert_db(anime)
                update_db(anime)
        except Exception as e:
            state.thread_limiter.release()
            err_print(sn, '重新下載', 'db 更新失敗: ' + str(e), status=1)
            err_print(sn, '重新下載', '異常詳情:\n' + traceback.format_exc(), status=1, display=False)
            return False

        download_cd = threading.Thread(target=download_cd_counter)
        download_cd.start()
        err_print(sn, '重新下載完成', status=2)
        return True
    finally:
        state.redownloading.discard(sn)


def worker(sn, sn_info, realtime_show_file_size=False):
    bangumi_tag = sn_info['tag']
    rename = sn_info['rename']

    def upload_quit():
        state.queue.pop(sn)
        state.processing_queue.remove(sn)
        state.upload_limiter.release()
        sys.exit(0)

    anime_in_db = read_db(sn)
    if settings['upload_to_server'] and anime_in_db['status'] == 1 and anime_in_db['remote_status'] == 0:
        state.upload_limiter.acquire()
        state.pending_tasks.pop(int(sn), None)
        anime = build_anime(sn)
        if anime['failed']:
            err_print(sn, '任務失敗', '從任務列隊中移除, 等待下次更新重試.', status=1)
            upload_quit()

        anime = anime['anime']
        if not os.path.exists(anime_in_db['local_file_path']):
            update_db(anime)
            err_msg_detail = 'title=\"' + anime.get_title() + '\" 本地檔案遺失, 從任務列隊中移除, 等待下次更新重試.'
            err_print(sn, '上傳失敗', err_msg_detail, status=1)
            upload_quit()

        anime.local_video_path = anime_in_db['local_file_path']
        anime.video_size = anime_in_db['file_size']
        anime.video_resolution = anime_in_db['resolution']

        try:
            if not anime.upload(bangumi_tag):
                err_msg_detail = 'title=\"' + anime.get_title() + '\" 從任務列隊中移除, 等待下次更新重試.'
                err_print(sn, '上傳失敗', err_msg_detail, 1)
            else:
                update_db(anime)
                err_print(sn, '任務完成', status=2)
        except BaseException as e:
            err_msg_detail = 'title=\"' + anime.get_title() + '\" 發生未知錯誤, 等待下次更新重試: ' + str(e)
            err_print(sn, '上傳失敗', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)
            err_print(sn, '上傳失敗', err_msg_detail, 1)

        upload_quit()

    # =====download module =====
    state.thread_limiter.acquire()
    state.pending_tasks.pop(int(sn), None)
    anime = build_anime(sn)

    if anime['failed']:
        state.queue.pop(sn)
        state.processing_queue.remove(sn)
        state.thread_limiter.release()
        err_print(sn, '任務失敗', '從任務列隊中移除, 等待下次更新重試.', status=1)
        sys.exit(1)

    anime = anime['anime']

    try:
        anime.download(settings['download_resolution'], bangumi_tag=bangumi_tag, rename=rename,
                       realtime_show_file_size=realtime_show_file_size, classify=settings['classify_bangumi'])
    except BaseException as e:
        err_print(sn, '下載異常', '發生未知錯誤: '+str(e), status=1)
        err_print(sn, '下載異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)
        anime.video_size = 0

    if anime.video_size < 5:
        state.queue.pop(sn)
        state.processing_queue.remove(sn)
        state.thread_limiter.release()
        err_msg_detail = 'title=\"' + anime.get_title() + '\" 從任務列隊中移除, 等待下次更新重試.'
        err_print(sn, '任務失敗', err_msg_detail, status=1)
        if int(sn) in state.tasks_progress_rate.keys():
            del state.tasks_progress_rate[int(sn)]
        sys.exit(1)

    update_db(anime)
    download_cd = threading.Thread(target=download_cd_counter)
    download_cd.start()
    # =====download module end =====

    # =====upload module=====
    if settings['upload_to_server']:
        state.upload_limiter.acquire()

        try:
            anime.upload(bangumi_tag)
        except BaseException as e:
            err_print(sn, '上傳異常', '發生未知錯誤, 從任務列隊中移除, 等待下次更新重試: ' + str(e), status=1)
            err_print(sn, '上傳異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)
            upload_quit()

        update_db(anime)
        state.upload_limiter.release()
    # =====upload module end=====

    download_cd.join()
    state.queue.pop(sn)
    state.processing_queue.remove(sn)
    err_print(sn, '任務完成', status=2)


def download_cd_counter():
    seconds = settings['download_cd']
    while seconds > 0:
        err_print('', '下載冷卻:', '下載冷卻時間剩餘 ' + str(seconds) + ' 秒', status=0, no_sn=True)
        wait_time = min(30, seconds)
        time.sleep(wait_time)
        seconds -= wait_time
    state.thread_limiter.release()


def check_tasks():
    for sn in sn_dict.keys():
        anime = build_anime(sn)
        if anime['failed']:
            err_print(sn, '更新狀態', '檢查更新失敗, 跳過等待下次檢查', status=1)
            continue
        anime = anime['anime']
        err_print(sn, '更新資訊', '正在檢查《' + anime.get_bangumi_name() + '》')
        episode_list = list(anime.get_episode_list().values())

        if sn_dict[sn]['mode'] == 'all':
            for ep in episode_list:
                try:
                    db = read_db(ep)
                    if (db['status'] == 0 or (db['remote_status'] == 0 and settings['upload_to_server'])) and ep not in state.queue.keys():
                        state.queue[ep] = sn_dict[sn]
                except IndexError:
                    if anime.get_sn() == ep:
                        new_anime = anime
                    else:
                        new_anime = build_anime(ep)
                        if new_anime['failed']:
                            err_print(ep, '更新狀態', '更新數據失敗, 跳過等待下次檢查', status=1)
                            continue
                        new_anime = new_anime['anime']
                    insert_db(new_anime)
                    state.queue[ep] = sn_dict[sn]
        else:
            if sn_dict[sn]['mode'] == 'largest-sn':
                episode_list.sort()
                latest_sn = episode_list[-1]
            elif sn_dict[sn]['mode'] == 'single':
                latest_sn = sn
            else:
                latest_sn = episode_list[-1]
            try:
                db = read_db(latest_sn)
                if (db['status'] == 0 or (db['remote_status'] == 0 and settings['upload_to_server'])) and latest_sn not in state.queue.keys():
                    state.queue[latest_sn] = sn_dict[sn]
            except IndexError:
                if anime.get_sn() == latest_sn:
                    new_anime = anime
                else:
                    new_anime = build_anime(latest_sn)
                    if new_anime['failed']:
                        err_print(latest_sn, '更新狀態', '更新數據失敗, 跳過等待下次檢查', status=1)
                        continue
                    new_anime = new_anime['anime']
                insert_db(new_anime)
                state.queue[latest_sn] = sn_dict[sn]


def __download_only(sn, dl_resolution='', dl_save_dir='', realtime_show_file_size=False, classify=True):
    # download only, do not touch the database
    state.thread_limiter.acquire()
    err_counter = 0

    anime = build_anime(sn)
    if anime['failed']:
        sys.exit(1)
    anime = anime['anime']

    try:
        if dl_resolution:
            anime.download(dl_resolution, dl_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)
        else:
            anime.download(settings['download_resolution'], dl_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)
    except BaseException as e:
        err_print(sn, '下載異常', '發生未知異常: ' + str(e), status=1)
        err_print(sn, '下載異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)
        anime.video_size = 0

    while anime.video_size < 5:
        if err_counter >= 3:
            err_print(sn, '終止任務', 'title=' + anime.get_title()+' 任務失敗達三次! 終止任務!', status=1)
            state.thread_limiter.release()
            if int(sn) in state.tasks_progress_rate.keys():
                del state.tasks_progress_rate[int(sn)]
            return
        else:
            err_print(sn, '任務失敗', 'title=' + anime.get_title() + ' 10s後自動重啟,最多重試三次', status=1)
            err_counter = err_counter + 1
            if int(sn) in state.tasks_progress_rate.keys():
                state.tasks_progress_rate[int(sn)]['status'] = '失敗! 重啟中'
            time.sleep(10)
            anime.renew()

            try:
                if dl_resolution:
                    anime.download(dl_resolution, dl_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)
                else:
                    anime.download(settings['download_resolution'], dl_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)
            except BaseException as e:
                err_print(sn, '下載異常', '發生未知異常: ' + str(e), status=1)
                err_print(sn, '下載異常', '異常詳情:\n'+traceback.format_exc(), status=1, display=False)
                anime.video_size = 0

    download_cd = threading.Thread(target=download_cd_counter)
    download_cd.start()


def __get_info_only(sn):
    state.thread_limiter.acquire()

    anime = build_anime(sn)
    if anime['failed']:
        sys.exit(1)
    anime = anime['anime']
    anime.set_resolution(resolution)
    anime.get_info()
    download_dir = settings['bangumi_dir']
    if classify:
        download_dir = os.path.join(download_dir, Config.legalize_filename(anime.get_bangumi_name()))

    if danmu:
        if os.path.exists(download_dir):
            full_filename = os.path.join(download_dir, anime.get_filename()).replace('.' + settings['video_filename_extension'], '.ass')
            d = Danmu(sn, full_filename, cookies.read(),
                      danmu_template=Config.get_danmu_template(), logger=err_print)
            d.download(settings['danmu_ban_words'])
        else:
            err_print(sn, '彈幕下載異常', '番劇資料夾不存在: ' + download_dir, status=1)

    state.thread_limiter.release()


def __get_danmu_only(sn, bangumi_name, video_path):
    state.thread_limiter.acquire()

    download_dir = settings['bangumi_dir']
    if classify:
        download_dir = os.path.join(download_dir, Config.legalize_filename(bangumi_name))

    if os.path.exists(download_dir):
        d = Danmu(sn, video_path.replace('.' + settings['video_filename_extension'], '.ass'), cookies.read(),
                  danmu_template=Config.get_danmu_template(), logger=err_print)
        d.download(settings['danmu_ban_words'])
    else:
        err_print(sn, '彈幕下載異常', '番劇資料夾不存在: ' + download_dir, status=1)

    state.thread_limiter.release()


def __cui(sn, cui_resolution, cui_download_mode, cui_thread_limit, ep_range,
          cui_save_dir='', classify=True, get_info=False, user_cmd=False, realtime_show=True, cui_danmu=False):
    state.thread_limiter = threading.Semaphore(cui_thread_limit)

    global danmu
    danmu = cui_danmu

    if realtime_show:
        if cui_thread_limit == 1 or cui_download_mode in ('single', 'latest', 'largest-sn'):
            realtime_show_file_size = True
        else:
            realtime_show_file_size = False
    else:
        realtime_show_file_size = False

    if cui_download_mode == 'single':
        if get_info:
            print('當前模式: 查詢本集資訊\n')
        else:
            print('當前下載模式: 僅下載本集\n')

        if get_info:
            __get_info_only(sn)
        else:
            __download_only(sn, cui_resolution, cui_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)

    elif cui_download_mode == 'latest' or cui_download_mode == 'largest-sn':
        if cui_download_mode == 'latest':
            if get_info:
                print('當前模式: 查詢本番劇最後一集資訊\n')
            else:
                print('當前下載模式: 下載本番劇最後一集\n')
        else:
            if get_info:
                print('當前模式: 查詢本番劇最近上傳一集資訊\n')
            else:
                print('當前下載模式: 下載本番劇最近上傳的一集\n')

        anime = build_anime(sn)
        if anime['failed']:
            sys.exit(1)
        anime = anime['anime']

        bangumi_list = list(anime.get_episode_list().values())

        if cui_download_mode == 'largest-sn':
            bangumi_list.sort()

        if get_info:
            __get_info_only(bangumi_list[-1])
        else:
            __download_only(bangumi_list[-1], cui_resolution, cui_save_dir, realtime_show_file_size=realtime_show_file_size, classify=classify)

    elif cui_download_mode == 'all':
        if get_info:
            print('當前模式: 查詢本番劇所有劇集資訊\n')
        else:
            print('當前下載模式: 下載本番劇所有劇集\n')

        anime = build_anime(sn)
        if anime['failed']:
            sys.exit(1)
        anime = anime['anime']

        bangumi_list = list(anime.get_episode_list().values())
        bangumi_list.sort()
        tasks_counter = 0
        for anime_sn in bangumi_list:
            if get_info:
                task = threading.Thread(target=__get_info_only, args=(anime_sn,))
            else:
                task = threading.Thread(target=__download_only, args=(anime_sn, cui_resolution, cui_save_dir, realtime_show_file_size, classify))
            task.daemon = True
            thread_tasks.append(task)
            task.start()
            tasks_counter = tasks_counter + 1
            print('添加任務列隊: sn=' + str(anime_sn))
        if get_info:
            print('所有查詢任務已添加至列隊, 共 '+str(tasks_counter)+' 個任務\n')
        else:
            print('所有下載任務已添加至列隊, 共 '+str(tasks_counter)+' 個任務, '+'執行緒數: ' + str(cui_thread_limit) + '\n')

    elif cui_download_mode == 'range':
        if get_info:
            print('當前模式: 查詢本番劇指定劇集資訊\n')
        else:
            print('當前下載模式: 下載本番劇指定劇集\n')

        anime = build_anime(sn)
        if anime['failed']:
            sys.exit(1)
        anime = anime['anime']

        episode_dict = anime.get_episode_list()
        bangumi_ep_list = list(episode_dict.keys())
        tasks_counter = 0
        for ep in ep_range:
            if ep in bangumi_ep_list:
                if get_info:
                    a = threading.Thread(target=__get_info_only, args=(episode_dict[ep],))
                else:
                    a = threading.Thread(target=__download_only, args=(episode_dict[ep], cui_resolution, cui_save_dir, realtime_show_file_size))
                a.daemon = True
                thread_tasks.append(a)
                a.start()
                tasks_counter = tasks_counter + 1
                if get_info:
                    print('添加查詢列隊: sn=' + str(episode_dict[ep]) + ' 《' + anime.get_bangumi_name() + '》 第 ' + ep + ' 集')
                else:
                    print('添加任務列隊: sn='+str(episode_dict[ep])+' 《'+anime.get_bangumi_name()+'》 第 '+ep+' 集')
            else:
                err_print(0, '《'+anime.get_bangumi_name()+'》 第 '+ep+' 集不存在!', status=1, no_sn=True)
        print('所有任務已添加至列隊, 共 '+str(tasks_counter)+' 個任務, '+'執行緒數: ' + str(cui_thread_limit) + '\n')

    elif cui_download_mode == 'sn-range':
        if get_info:
            print('當前模式: 查詢本番劇指定sn範圍資訊\n')
        else:
            print('當前下載模式: 下載本番劇指定sn範圍劇集\n')

        anime = build_anime(sn)
        if anime['failed']:
            sys.exit(1)
        anime = anime['anime']

        episode_dict = {value: key for key, value in anime.get_episode_list().items()}
        ep_sn_list = list(episode_dict.keys())
        tasks_counter = 0
        ep_range = list(map(lambda x: int(x), ep_range))
        for sn in ep_sn_list:
            if sn in ep_range:
                if get_info:
                    a = threading.Thread(target=__get_info_only, args=(sn,))
                else:
                    a = threading.Thread(target=__download_only, args=(sn, cui_resolution, cui_save_dir, realtime_show_file_size))
                a.daemon = True
                thread_tasks.append(a)
                a.start()
                tasks_counter = tasks_counter + 1
                if get_info:
                    print('添加查詢列隊: sn=' + str(sn) + ' 《' + anime.get_bangumi_name() + '》 第 ' + episode_dict[sn] + ' 集')
                else:
                    print('添加任務列隊: sn='+str(sn)+' 《'+anime.get_bangumi_name()+'》 第 ' + episode_dict[sn] + ' 集')
        print('所有任務已添加至列隊, 共 ' + str(tasks_counter) + ' 個任務, ' + '執行緒數: ' + str(cui_thread_limit) + '\n')

    elif cui_download_mode == 'multi':
        if get_info:
            print('當前模式: 查詢指定sn資訊\n')
        else:
            print('當前下載模式: 下載指定sn劇集\n')

        tasks_counter = 0
        for sn in ep_range:
            if get_info:
                a = threading.Thread(target=__get_info_only, args=(sn,))
            else:
                a = threading.Thread(target=__download_only, args=(sn, cui_resolution, cui_save_dir, realtime_show_file_size))
            a.daemon = True
            thread_tasks.append(a)
            a.start()
            tasks_counter = tasks_counter + 1

        print('所有任務已添加至列隊, 共 ' + str(tasks_counter) + ' 個任務, ' + '執行緒數: ' + str(cui_thread_limit) + '\n')

    elif cui_download_mode in ('list', 'sn-list'):
        if get_info:
            print('當前模式: 查詢sn_list.txt中指定sn的資訊\n')
            ep_range = Config.read_sn_list().keys()
            for sn in ep_range:
                anime = build_anime(sn)
                if anime['failed']:
                    sys.exit(1)
                anime = anime['anime']
                anime.get_info()
        else:
            if cui_download_mode == 'sn-list':
                print('當前下載模式: 下載sn_list.txt中指定的sn劇集\n')
                for i in sn_dict:
                    sn_dict[i]['mode'] = 'single'
            else:
                print('當前下載模式: 單次下載sn_list.txt中的番劇\n')

            check_tasks()
            for sn in state.queue.keys():
                state.processing_queue.append(sn)
                task = threading.Thread(target=worker, args=(sn, state.queue[sn], realtime_show_file_size))
                task.daemon = True
                thread_tasks.append(task)
                task.start()
                err_print(sn, '加入任務列隊')
            msg = '共 ' + str(len(state.queue)) + ' 個任務'
            err_print(0, '任務資訊', msg, no_sn=True)
            print()

    elif cui_download_mode == 'danmu':
        tasks_counter = 0
        for anime_db in ep_range:
            if anime_db["status"] == 1:
                if anime_db["anime_name"] is not None \
                and anime_db["local_file_path"] is not None:
                    a = threading.Thread(target=__get_danmu_only, args=(anime_db["sn"], anime_db["anime_name"], anime_db["local_file_path"]))
                    a.daemon = True
                    thread_tasks.append(a)
                    a.start()
                    tasks_counter = tasks_counter + 1
                else:
                    err_print(anime_db["sn"], '彈幕更新失敗', "資料庫不存在番劇名稱或影片路徑", status=1)

        print('所有任務已添加至列隊, 共 ' + str(tasks_counter) + ' 個任務, ' + '執行緒數: ' + str(cui_thread_limit) + '\n')

    __kill_thread_when_ctrl_c()
    kill_gost()

    if user_cmd:
        print()
        os.popen(settings['user_command'])
        err_print(0, '任務完成', '已執行用戶命令', no_sn=True, status=2)

    sys.exit(0)


def __kill_thread_when_ctrl_c():
    for t in thread_tasks:
        while True:
            if t.is_alive():
                time.sleep(1)
            else:
                break


def kill_gost():
    if gost_subprocess is not None:
        gost_subprocess.kill()


def user_exit(signum, frame):
    err_print(0, '你終止了程序!', '\n', status=1, no_sn=True, prefix='\n\n')
    kill_gost()
    sys.exit(255)


def _version_tuple(v):
    # 'v0.1.1' -> (0, 1, 1); compares correctly across multi-dot semver,
    # unlike float() which chokes on a second '.'. Non-numeric parts are dropped.
    return tuple(int(p) for p in str(v).lstrip('vV').split('.') if p.isdigit())


def check_new_version():
    remote_version = Config.read_latest_version_on_github()
    if _version_tuple(settings['AniGamerDownloader_version']) < _version_tuple(remote_version['tag_name']):
        msg = '發現GitHub上有新版本: '+remote_version['tag_name']+'\n更新內容:\n'+remote_version['body']+'\n'
        err_print(0, msg, status=1, no_sn=True)


def __init_proxy():
    if settings['use_gost']:
        print('使用代理連接動畫瘋, 使用擴展的代理協議')
        check_gost = subprocess.Popen('gost -h', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=(0x08000000 if os.name == 'nt' else 0))
        if check_gost.stderr.readlines():
            gost_path = 'gost'
        else:
            if 'Windows' in platform.system():
                gost_path = os.path.join(working_dir, 'gost.exe')
            else:
                gost_path = os.path.join(working_dir, 'gost')
            if not os.path.exists(gost_path):
                err_print(0, '當前代理使用擴展協議, 需要使用gost, 但是gost未找到', status=1, no_sn=True)
                raise FileNotFoundError
        gost_cmd = [gost_path, '-L=:'+str(gost_port), '-F=' + settings['proxy']]

        def run_gost():
            global gost_subprocess
            gost_subprocess = subprocess.Popen(gost_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=(0x08000000 if os.name == 'nt' else 0))
            gost_subprocess.communicate()

        run_gost_threader = threading.Thread(target=run_gost)
        run_gost_threader.daemon = True
        run_gost_threader.start()
        time.sleep(3)

    else:
        print('使用代理連接動畫瘋, 使用http/https/socks5協議')


def do_request(url, headers, cookies, params=None):
    return requests.get(url, headers=headers, cookies=cookies, params=params)


def parse_anime(soup, animes, headers, cookies):
    if soup.text.find("目前沒有訂閱內容") != -1:
        return False
    for animeInfo in soup.select_one(".theme-list-block").select("a"):
        response = do_request(f"https://ani.gamer.com.tw/{animeInfo['href']}", headers, cookies)
        sn = response.url.split("=")[-1]
        name = animeInfo.select_one(".theme-name").text
        animes.append({"sn": sn, "name": name})
    return True


def export_my_anime():
    from bs4 import BeautifulSoup

    url = "https://ani.gamer.com.tw/mygather.php"
    header = {
        'accept':
        'application/json',
        'origin':
        'https://ani.gamer.com.tw',
        'authority':
        'ani.gamer.com.tw',
        'user-agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.83 Safari/537.36',
    }

    my_cookies = cookies.read()
    if not my_cookies:
        err_print(0, "請先設定cookie後再執行此指令", status=1, no_sn=True)
        return

    page = 1
    animes = []
    while True:
        params = {'page': page, 'sort': 0}
        bahamygatherPage = do_request(url, headers=header, cookies=my_cookies, params=params)
        if bahamygatherPage.status_code == requests.codes.ok:
            soup = BeautifulSoup(bahamygatherPage.text, 'html.parser')
            if not parse_anime(soup, animes, header, my_cookies):
                break
        else:
            err_print(0, f"匯入我的動畫失敗，狀態碼{bahamygatherPage.status_code}", status=1, no_sn=True)
        page += 1

    with open("my_anime.txt", "w", encoding="utf-8") as f:
        for anime in animes:
            f.write(f"{anime['sn']} all <{anime['name']}>\n")


def run_dashboard():
    if not port_is_available(settings['dashboard']['port']):
        err_print(0, 'Web控制面板啟動失敗', 'Port已被佔用! 請到設定檔更換', status=1, no_sn=True)
        return

    from app.web.server import run as dashboard
    server = threading.Thread(target=dashboard)
    server.daemon = True
    server.start()
    if settings['dashboard']['SSL']:
        dashboard_address = 'https://'
    else:
        dashboard_address = 'http://'
    if settings['dashboard']['host'] == '0.0.0.0':
        host = Config.get_local_ip()
        dashboard_address = '【開放外部訪問】訪問地址: ' + dashboard_address
    else:
        host = settings['dashboard']['host']
        dashboard_address = '訪問地址: ' + dashboard_address

    dashboard_address = dashboard_address + host + ':' + str(settings['dashboard']['port'])
    err_print(0, 'Web控制面板已啟動', dashboard_address, no_sn=True, status=2)


def acquire_single_instance(port=SINGLE_INSTANCE_PORT):
    # Single-instance lock: bind a fixed loopback port and hold it until process exit. Cannot bind = an instance is already running.
    global _single_instance_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', port))
        s.listen(1)
    except OSError:
        s.close()
        return False
    _single_instance_socket = s  # hold the reference; released automatically by the OS on process exit
    return True


def run_daemon():
    # Auto-download daemon + (optional) web dashboard.
    global settings, sn_dict, danmu
    if not acquire_single_instance():
        err_print(0, '啟動中止', '偵測到已有一個 AniGamerDownloader 正在執行, 請勿重複開啟', no_sn=True, status=1)
        return False
    init_db()
    version_msg = '當前AniGamerDownloader版本: ' + settings['AniGamerDownloader_version']
    err_print(0, '自動模式啟動AniGamerDownloader ' + version_msg, no_sn=True, display=False)
    err_print(0, '工作目錄: ' + working_dir, no_sn=True, display=False)

    if settings['use_proxy']:
        __init_proxy()

    if settings['use_dashboard']:
        run_dashboard()

    state.daemon_running = True
    while True:
        if state.shutting_down:
            err_print(0, '程式結束', '收到停止指令, daemon 迴圈退出', no_sn=True, status=2)
            break
        print()
        if state.batch_download_paused:
            err_print(0, '批次下載', '已暫停 (透過 Web 控制臺), 跳過本次檢查; 進行中的任務繼續', no_sn=True, status=1)
        else:
            err_print(0, '開始更新', no_sn=True)
            cookies.test()  # test cookie
            if settings['read_sn_list_when_checking_update']:
                sn_dict = Config.read_sn_list()
            if settings['read_config_when_checking_update']:
                settings = Config.read_settings()
            danmu = settings['danmu']
            check_tasks()
            new_tasks_counter = 0
            if state.queue:
                for task_sn in state.queue.keys():
                    if task_sn not in state.processing_queue:
                        try:
                            rec = read_db(task_sn)
                            state.pending_tasks[int(task_sn)] = {
                                'name': rec['anime_name'], 'episode': rec['episode']}
                        except Exception:
                            state.pending_tasks[int(task_sn)] = {'name': '', 'episode': str(task_sn)}
                        task = threading.Thread(target=worker, args=(task_sn, state.queue[task_sn]))
                        task.daemon = True
                        task.start()
                        state.processing_queue.append(task_sn)
                        new_tasks_counter = new_tasks_counter + 1
                        err_print(task_sn, '加入任務列隊')
            info = '本次更新添加了 ' + str(new_tasks_counter) + ' 個新任務, 目前列隊中共有 ' + str(len(state.processing_queue)) + ' 個任務'
            err_print(0, '更新資訊', info, no_sn=True)
            err_print(0, '更新終了', no_sn=True)
        print()
        for i in range(settings['check_frequency'] * 60):
            if state.shutting_down or state.force_check_now:
                state.force_check_now = False
                break
            time.sleep(1)
    return True


# Module-level vars referenced by __cui/__get_info_only when set from CLI.
resolution = settings['download_resolution']
classify = True


def cli_main(argv=None):
    # The argparse one-shot CLI body (was aniGamerPlus.py __main__ 1035-1163).
    # Kept here so cli.py + the src shim both delegate to a single implementation.
    global resolution, classify, danmu
    if settings['check_latest_version']:
        check_new_version()
    version_msg = '當前AniGamerDownloader版本: ' + settings['AniGamerDownloader_version']
    print(version_msg)

    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 0:
        init_db()
        parser = argparse.ArgumentParser()
        parser.add_argument('--sn', '-s', type=int, help='影片sn碼(數字)')
        parser.add_argument('--resolution', '-r', type=int, help='指定下載畫質(數字)', choices=[360, 480, 540, 576, 720, 1080])
        parser.add_argument('--download_mode', '-m', type=str, help='下載模式', default='single',
                            choices=['single', 'latest', 'largest-sn', 'multi', 'all', 'range', 'list', 'sn-list', 'sn-range', 'db'])
        parser.add_argument('--thread_limit', '-t', type=int, help='最高並發下載數(數字)')
        parser.add_argument('--current_path', '-c', action='store_true', help='下載到當前工作目錄')
        parser.add_argument('--episodes', '-e', type=str, help='僅下載指定劇集')
        parser.add_argument('--no_classify', '-n', action='store_true', help='不建立番劇資料夾')
        parser.add_argument('--user_command', '-u', action='store_true', help='所有下載完成後執行用戶命令')
        parser.add_argument('--information_only', '-i', action='store_true', help='僅查詢資訊，可搭配 -d 更新彈幕')
        parser.add_argument('--danmu', '-d', action='store_true', help='以 .ass 下載彈幕')
        parser.add_argument('--my_anime', action='store_true', help='匯出「我的動畫」至my_anime.txt')
        arg = parser.parse_args(argv)

        if arg.my_anime:
            export_my_anime()
            sys.exit(0)

        if (arg.download_mode not in ('list', 'multi', 'sn-list', 'db')) and arg.sn is None:
            err_print(0, '參數錯誤', '非 list/multi 模式需要提供 sn ', no_sn=True, status=1)
            sys.exit(1)

        save_dir = ''
        download_mode = arg.download_mode
        if arg.current_path:
            save_dir = os.getcwd()
            info = '使用命令列模式, 指定下載到當前目錄: '
            print(info + '\n    ' + save_dir)
            err_print(0, info + save_dir, no_sn=True, display=False)
        else:
            info = '使用命令列模式, 檔案將儲存在設定檔中指定的目錄下: '
            print(info + '\n    ' + settings['bangumi_dir'])
            err_print(0, info + settings['bangumi_dir'], no_sn=True, display=False)

        classify = True
        if arg.no_classify:
            classify = False
            print('將不會建立番劇資料夾')

        if not arg.episodes and arg.download_mode == 'range':
            err_print(0, 'ERROR: 當前為指定範圍模式, 但範圍未指定!', status=1, no_sn=True)
            sys.exit(1)

        download_episodes = []
        if arg.episodes or arg.download_mode == 'sn-list':
            if arg.download_mode == 'multi':
                for i in arg.episodes.split(','):
                    if re.match(r'^\d+$', i):
                        download_episodes.append(int(i))
                if arg.sn:
                    download_episodes.append(arg.sn)

            elif arg.download_mode == 'sn-list':
                download_episodes = list(Config.read_sn_list().keys())

            else:
                for i in arg.episodes.split(','):
                    if re.match(r'^\d+-\d+$', i):
                        episodes_range_start = int(i.split('-')[0])
                        episodes_range_end = int(i.split('-')[1])
                        if episodes_range_start > episodes_range_end:
                            episodes_range_start, episodes_range_end = episodes_range_end, episodes_range_start
                        download_episodes.extend(list(range(episodes_range_start, episodes_range_end + 1)))
                    if re.match(r'^\d+$', i):
                        download_episodes.append(int(i))
                if arg.download_mode != 'sn-range':
                    download_mode = 'range'

            download_episodes = list(set(download_episodes))
            download_episodes.sort()
            download_episodes = list(map(lambda x: str(x), download_episodes))

        if not arg.resolution:
            resolution = settings['download_resolution']
            print('未設定下載解析度, 將使用設定檔指定的畫質: ' + resolution + 'P')
        else:
            if arg.download_mode in ('sn-list', 'list'):
                err_print(0, '無效參數:', 'list 及 sn-list 模式無法通過命令列指定畫質', 1, no_sn=True, display_time=False)
                resolution = settings['download_resolution']
                print('將使用設定檔指定的畫質: ' + resolution + 'P')
            else:
                resolution = str(arg.resolution)
                print('指定下載解析度: ' + resolution + 'P')

        if arg.download_mode == "db":
            download_mode = "danmu"
            download_episodes = read_db_all()

        if arg.information_only:
            thread_limit = 1
            state.thread_limiter = threading.Semaphore(thread_limit)
        else:
            if arg.thread_limit:
                if arg.thread_limit > Config.get_max_multi_thread():
                    thread_limit = Config.get_max_multi_thread()
                else:
                    thread_limit = arg.thread_limit
            else:
                thread_limit = settings['multi-thread']

        if settings['use_proxy']:
            __init_proxy()

        if arg.user_command:
            user_command = True
        else:
            user_command = False

        if arg.danmu:
            danmu = True

        cookies.test()
        __cui(arg.sn, resolution, download_mode, thread_limit, download_episodes, save_dir, classify,
              get_info=arg.information_only, user_cmd=user_command, cui_danmu=danmu)
    else:
        run_daemon()


# Install the SIGINT/SIGTERM handlers at import time, matching the original
# module (aniGamerPlus.py:916-917) so Ctrl+C / SIGTERM behaviour is preserved
# wherever the daemon/CLI runs.
signal.signal(signal.SIGINT, user_exit)
signal.signal(signal.SIGTERM, user_exit)
