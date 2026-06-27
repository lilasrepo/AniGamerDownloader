#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2019/1/5 20:23
# @Author  : Miyouzi
# @File    : Config.py (moved to core/config.py by the local fork refactor)
"""Static config layer: schema defaults + load/save/upgrade/normalize.

Moved verbatim from ``src/Config.py`` (the static half). Runtime mutable flags
(``tasks_progress_rate`` / ``pending_tasks`` / ``batch_download_paused`` /
``daemon_running`` / ``shutting_down`` / ``force_check_now``) deliberately do
NOT live here — they belong to ``app.state.AppState`` in a later phase, and the
``src/Config.py`` shim keeps them for current importers.

The ``__color_print`` indirection is replaced by the default logger from
``core.logging`` (no behaviour change). ``__update_database`` lives in
``core.db`` (DB concern) and is imported back here for ``read_settings``.
"""

import os, json, re, sys, requests, time, random, codecs, chardet
import socket
from urllib.parse import quote
from urllib.parse import urlencode

from core.logging import err_print as __color_print
from core.db import update_database as __update_database

# Guess whether I am a .exe or a .py file
if getattr(sys, 'frozen', False):
    working_dir = os.path.dirname(sys.executable)        # writable data (data/) sits next to the exe
    # Read-only assets (web/, DanmuTemplate.ass, webview2/) are stored in PyInstaller's _internal/
    # (= sys._MEIPASS), keeping the portable folder's top level to just exe + data/, not flooded with deps.
    bundle_dir = getattr(sys, '_MEIPASS', working_dir)
else:
    # core/ is one level below the project root; resolve data/web/etc. from the root
    working_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    bundle_dir = working_dir  # in source mode the two are the same, behaviour unchanged

data_dir = os.path.join(working_dir, 'data')
web_dir = os.path.join(bundle_dir, 'web')
config_path = os.path.join(data_dir, 'config.json')
sn_list_path = os.path.join(data_dir, 'sn_list.txt')
cookie_path = os.path.join(data_dir, 'cookie.txt')
logs_dir = os.path.join(data_dir, 'logs')
AniGamerDownloader_version = 'v0.1.1'
latest_config_version = 17.2
latest_database_version = 2.0
max_multi_thread = 5
max_multi_downloading_segment = 5


def get_max_multi_thread():
    return max_multi_thread


def legalize_filename(filename):
    # Make the filename legal
    legal_filename = re.sub(r'\|+', '｜', filename)  # handle | , convert to fullwidth ｜
    legal_filename = re.sub(r'\?+', '？', legal_filename)  # handle ? , convert to Chinese ？
    legal_filename = re.sub(r'\*+', '＊', legal_filename)  # handle * , convert to fullwidth ＊
    legal_filename = re.sub(r'<+', '＜', legal_filename)  # handle < , convert to fullwidth ＜
    legal_filename = re.sub(r'>+', '＞', legal_filename)  # handle < , convert to fullwidth ＞
    legal_filename = re.sub(r'\"+', '＂', legal_filename)  # handle " , convert to fullwidth ＂
    legal_filename = re.sub(r':+', '：', legal_filename)  # handle : , convert to Chinese ：
    legal_filename = re.sub(r'\\', '＼', legal_filename)  # handle \ , convert to fullwidth ＼
    legal_filename = re.sub(r'/', '／', legal_filename)  # handle / , convert to fullwidth ／
    return legal_filename


def get_working_dir():
    return working_dir


def get_bundle_dir():
    # Root of read-only assets (frozen = _internal/ ; source = project root, same as working_dir)
    return bundle_dir


def get_web_dir():
    return web_dir


def get_danmu_template():
    # The user can place a custom template in the data/ next to the exe to override; otherwise use the bundled default template.
    user = os.path.join(working_dir, 'data', 'DanmuTemplate.ass')
    if os.path.exists(user):
        return user
    return os.path.join(bundle_dir, 'data', 'DanmuTemplate.ass')


def get_config_path():
    return config_path


def get_sn_list_content():
    # Return all sn_list content, including comments, for the web dashboard
    if not os.path.exists(sn_list_path):
        return ""
    with open(sn_list_path, 'r', encoding='utf-8') as f:
        return f.read()


def __init_settings():
    if os.path.exists(config_path):
        os.remove(config_path)
    settings = {'bangumi_dir': '',
                'temp_dir': '',
                'classify_bangumi': True,  # whether to create per-anime directories
                'classify_season': False,  # whether to create per-season subdirectories
                'check_frequency': 5,  # check cooldown time, in minutes
                'download_cd': 60,  # download cooldown time (seconds)
                'parse_sn_cd': 5,  # sn page (i.e. playback page) parse cooldown time
                'download_resolution': '1080',  # download resolution
                'lock_resolution': False,  # lock resolution; if the resolution does not exist, declare the download failed
                'only_use_vip': False,  # lock to VIP-account downloads
                'default_download_mode': 'latest',  # download only the latest episode; the other mode is 'all', download everything plus future updates
                'use_copyfile_method': False,  # whether to use the copy method when moving videos to the anime directory; set True for rclone mount compatibility
                'multi-thread': 1,  # max concurrent downloads
                'multi_upload': 3,  # max concurrent uploads
                'segment_download_mode': True,  # AniGamerDownloader downloads segments; False means ffmpeg downloads
                'multi_downloading_segment': 2,  # effective when the above is True, concurrent segment downloads per video
                'segment_max_retry': 8,  # effective in segment download mode, max retries per segment, -1 means infinite retries
                'add_bangumi_name_to_video_filename': True,
                'add_resolution_to_video_filename': True,  # whether to add the resolution to the filename
                'customized_video_filename_prefix': '【動畫瘋】',  # user-defined prefix
                'customized_bangumi_name_suffix': '',  # user-defined anime name suffix (before the episode name)
                'customized_video_filename_suffix': '',  # user-defined suffix
                'video_filename_extension': 'mp4',  # video extension / container format
                'zerofill': 2,  # min digits for episode-name zero padding, <=1 no padding; dynamically widens to the season's max episode-number digit count (auto 3 digits past 100)
                # cookie auto-refresh checks the UA
                'ua': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
                'use_proxy': False,
                'proxy': 'http://user:passwd@example.com:1000',  # proxy feature, config_version v13.0 removed chained proxies
                "no_proxy_akamai": False,  # do not proxy the akamai CDN
                'upload_to_server': False,
                'ftp': {  # upload files to a remote server
                    'server': '',
                    'port': '',
                    'user': '',
                    'pwd': '',
                    'tls': True,
                    'cwd': '',  # file storage directory, the directory entered first after login
                    'show_error_detail': False,
                    'max_retry_num': 15
                },
                'user_command': 'shutdown -s -t 60',
                'coolq_notify': False,
                'coolq_settings': {
                    'msg_argument_name': 'message',
                    'message_suffix': '追加的資訊',
                    'query': [
                        'http://127.0.0.1:5700/send_group_msg?access_token=abc&group_id=12345678',
                        'http://127.0.0.1:5700/send_group_msg?access_token=abc&group_id=87654321'
                    ]
                },
                'telebot_notify': False,
                'telebot_token': "",
                'telebot_use_chat_id': False,
                'telebot_chat_id': "",
                'discord_notify': False,
                'discord_token': '',
                'plex_refresh': False,
                'plex_url': '',
                'plex_token': '',
                'plex_section': '',
                'plex_naming': False, # adapt to PLEX naming rules
                'faststart_movflags': False,
                'audio_language': False,
                'use_mobile_api': False,
                'danmu': False,
                'danmu_ban_words': [],
                'check_latest_version': True,  # whether to check for new versions
                'read_sn_list_when_checking_update': True,
                'read_config_when_checking_update': True,
                'ads_time': 25,
                'mobile_ads_time': 25,
                'use_dashboard': True,
                'dashboard': {
                    'host': '127.0.0.1',
                    'port': 5000,
                    'SSL': False,
                    'BasicAuth': False,
                    'username': 'admin',
                    'password': 'admin'
                },
                'save_logs': True,
                'quantity_of_logs': 7,
                'config_version': latest_config_version,
                'database_version': latest_database_version
                }
    os.makedirs(data_dir, exist_ok=True)  # a fresh portable copy may not have a data/ directory yet
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=4)


def __update_settings(old_settings):  # upgrade the config file
    new_settings = old_settings.copy()
    if 'check_latest_version' not in new_settings.keys():  # v2.0 added the check-for-update switch
        new_settings['check_latest_version'] = True

    if 'tls' not in new_settings['ftp'].keys():  # v2.0 added the FTP over TLS switch
        new_settings['ftp']['tls'] = True

    if 'upload_to_server' not in new_settings.keys():  # v2.0 added the upload switch
        new_settings['upload_to_server'] = False

    if 'use_proxy' not in new_settings.keys():  # v2.0 added the proxy switch
        new_settings['use_proxy'] = False

    if 'show_error_detail' not in new_settings['ftp'].keys():  # v2.0 added the show-FTP-transfer-error switch
        new_settings['ftp']['show_error_detail'] = False

    if 'max_retry_num' not in new_settings['ftp'].keys():  # v2.0 added the FTP retransmission attempt count
        new_settings['ftp']['max_retry_num'] = 10

    if 'read_sn_list_when_checking_update' not in new_settings.keys():  # v2.0 added switch: read sn_list on every update check
        new_settings['read_sn_list_when_checking_update'] = True

    if 'multi_upload' not in new_settings.keys():  # v2.0 added the max concurrent upload tasks
        new_settings['multi_upload'] = 3

    if 'read_config_when_checking_update' not in new_settings.keys():  # v2.0 added switch: read config.json on every update check
        new_settings['read_config_when_checking_update'] = True

    if 'add_bangumi_name_to_video_filename' not in new_settings.keys():  # v3.0 added switch, filename can use just the episode name
        new_settings['add_bangumi_name_to_video_filename'] = True

    if 'segment_download_mode' not in new_settings.keys():  # v3.1 added the segment download mode switch
        new_settings['segment_download_mode'] = True

    if 'multi_downloading_segment' not in new_settings.keys():  # v3.1 added concurrent segment downloads per video in segment download mode
        new_settings['multi_downloading_segment'] = 2

    new_settings['database_version'] = latest_database_version  # v3.2 added the database version number

    if 'save_logs' not in new_settings.keys():  # v4.0 added the log switch
        new_settings['save_logs'] = True

    if 'quantity_of_logs' not in new_settings.keys():  # v4.0 added the log quantity config (one log per day)
        new_settings['quantity_of_logs'] = 7

    if 'temp_dir' not in new_settings.keys():  # v4.0 added the temp directory option
        new_settings['temp_dir'] = ''

    if 'lock_resolution' not in new_settings.keys():
        new_settings['lock_resolution'] = False  # v4.1 added the resolution lock switch

    if 'ua' not in new_settings.keys():  # v4.2 added the UA config
        new_settings['ua'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.96 Safari/537.36"

    if 'classify_bangumi' not in new_settings.keys():
        new_settings['classify_bangumi'] = True  # v5.0 added the create-anime-directory switch

    if 'classify_season' not in new_settings.keys():
        new_settings['classify_season'] = False  # added the create-anime-season-subdirectory switch

    if 'plex_naming' not in new_settings.keys():
        new_settings['plex_naming'] = False

    if 'use_copyfile_method' not in new_settings.keys():
        # v6.0 added the video transfer method switch, set True for rclone mount compatibility
        new_settings['use_copyfile_method'] = False

    if 'zerofill' not in new_settings.keys():
        # v6.0 added episode-name zero padding; this number is the min padding digit count, <=1 means no padding;
        # dynamically widens to the season's max episode-number digit count (auto 3 digits past 100), default changed to 2
        new_settings['zerofill'] = 2

    if 'customized_bangumi_name_suffix' not in new_settings.keys():
        # v7.0 added the custom anime name suffix
        new_settings['customized_bangumi_name_suffix'] = ''

    if 'user_command' not in new_settings.keys():
        # v8.0 added running a custom command after CLI mode finishes
        # default command is shut down after one minute
        new_settings['user_command'] = 'shutdown -s -t 60'

    if 'segment_download_max_retry' not in new_settings.keys():
        # v9.0 added the per-segment retry count in segment mode
        new_settings['segment_max_retry'] = 8

    if 'coolq_notify' not in new_settings.keys():
        # v9.0 added the push notification to CQ feature
        new_settings['coolq_notify'] = False
        new_settings['coolq_settings'] = {
            'host': '127.0.0.1',
            'port': '5700',
            'SSL': False,
            'api': 'send_group_msg',
            'query': {
                'group_id': '123456789',
            },
            "user_message": ""
        }

    if 'telebot_notify' not in new_settings.keys():
        # added the push notification to TG feature
        new_settings['telebot_notify'] = False
        new_settings['telebot_token'] = ""
        new_settings['telebot_use_chat_id'] = False
        new_settings['telebot_chat_id'] = ""

    if 'discord_notify' not in new_settings.keys():
        # added the push notification to TG feature
        new_settings['discord_notify'] = False
        new_settings['discord_token'] = ''

    if 'plex_refresh' not in new_settings.keys():
        # added plex auto-refresh
        new_settings['plex_refresh'] = False
        new_settings['plex_url'] = ''
        new_settings['plex_token'] = ''
        new_settings['plex_section'] = ''
        new_settings['plex_naming'] = False

    if 'faststart_movflags' not in new_settings.keys():
        # v9.0 added feature: move metadata to the head of the video file
        # this feature allows faster online playback of the video
        new_settings['faststart_movflags'] = False

    if 'video_filename_extension' not in new_settings.keys():
        # v17 added the user-defined video extension
        new_settings['video_filename_extension'] = 'mp4'

    if 'audio_language' not in new_settings.keys():
        # v19 added the Japanese audio track tag  #37
        new_settings['audio_language'] = False

    if 'audio_language_jpn' in new_settings.keys():
        del new_settings['audio_language_jpn']

    if 'proxy' not in new_settings.keys() or 'proxies' in new_settings.keys():
        # v20 removed the chained proxy feature
        if new_settings['proxies']["1"]:
            # migrate the user's existing config
            new_settings['proxy'] = new_settings['proxies']["1"]
        else:
            new_settings['proxy'] = 'http://user:passwd@example.com:1000'
        del new_settings['proxies']

    if 'use_dashboard' not in new_settings.keys():
        # v20 launched the web dashboard
        new_settings['use_dashboard'] = True

    if 'dashboard' not in new_settings.keys():
        new_settings['dashboard'] = {
            'host': '127.0.0.1',
            'port': 5000,
            'SSL': False,
            'BasicAuth': False,
            'username': 'admin',
            'password': 'admin'
        }

    if 'ads_time' not in new_settings.keys():
        new_settings['ads_time'] = 25

    if 'danmu' not in new_settings.keys():
        # support downloading danmaku
        # https://github.com/miyouzi/aniGamerPlus/pull/66
        new_settings['danmu'] = False

    if 'danmu_ban_words' not in new_settings.keys():
        new_settings['danmu_ban_words'] = []

    if 'use_mobile_api' not in new_settings.keys():
        # v21.0 added the use APP API option #69
        new_settings['use_mobile_api'] = False

    if 'mobile_ads_time' not in new_settings.keys():
        new_settings['mobile_ads_time'] = 25  # with the APP API the non-member ad wait time can be as low as 3s

    if 'message_suffix' not in new_settings['coolq_settings'].keys():
        # v21.1 added
        new_settings['coolq_settings']['message_suffix'] = "追加的資訊"

    if 'user_message' in new_settings['coolq_settings'].keys():
        # the QQ bot push notification can append notification content via config
        new_settings['coolq_settings']['message_suffix'] = new_settings['coolq_settings']['user_message']
        del new_settings['coolq_settings']['user_message']

    if 'msg_argument_name' not in new_settings['coolq_settings'].keys():
        # v21.1 let the user build the QQ bot URL themselves
        new_settings['coolq_settings']['msg_argument_name'] = "message"

    if 'SSL' in new_settings['coolq_settings'].keys():
        # inherit the user's config
        if new_settings['coolq_settings']['SSL']:
            req = 'https://'
        else:
            req = 'http://'
        req = req + new_settings['coolq_settings']['host'] + ':' + new_settings['coolq_settings']['port'] + '/' \
              + new_settings['coolq_settings']['api'] + '?' + urlencode(new_settings['coolq_settings']['query'])

        new_settings['coolq_settings']['query'] = [req]
        del new_settings['coolq_settings']['host']
        del new_settings['coolq_settings']['port']
        del new_settings['coolq_settings']['api']
        del new_settings['coolq_settings']['SSL']

    if 'only_use_vip' not in new_settings.keys():
        new_settings['only_use_vip'] = False

    if 'no_proxy_akamai' not in new_settings.keys():
        # v24.3 added whether to proxy the akamai CDN (video stream)
        new_settings['no_proxy_akamai'] = False

    if 'download_cd' not in new_settings.keys():
        # v24.4 download cooldown time (seconds)
        new_settings['download_cd'] = 60

    if 'parse_sn_cd' not in new_settings.keys():
        # v24.4 sn parse cooldown time (seconds)
        new_settings['parse_sn_cd'] = 5

    new_settings['config_version'] = latest_config_version
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(new_settings, f, ensure_ascii=False, indent=4)
    msg = '設定檔從 v' + str(old_settings['config_version']) + ' 升級到 v' + str(latest_config_version) + ' 你的有效配置不會遺失!'
    __color_print(0, msg, status=2, no_sn=True)


def __read_settings_file():
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            # escape win paths
            return json.loads(re.sub(r'\\', '\\\\\\\\', f.read()))
    except json.JSONDecodeError:
        # if it has a BOM header, strip it
        try:
            # del_bom(config_path)
            check_encoding(config_path)
            # re-read
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.loads(re.sub(r'\\', '\\\\\\\\', f.read()))
        except BaseException as e:
            __color_print(0, '讀取配置發生異常, 將重置配置! ' + str(e), status=1, no_sn=True)
            __init_settings()
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except BaseException as e:
        __color_print(0, '讀取配置發生異常, 將重置配置! ' + str(e), status=1, no_sn=True)
        __init_settings()
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)


def del_bom(path, display=True):
    # handle UTF-8-BOM
    have_bom = False
    with open(path, 'rb') as f:
        content = f.read()
        if content.startswith(codecs.BOM_UTF8):
            content = content[len(codecs.BOM_UTF8):]
            have_bom = True
    if have_bom:
        filename = os.path.split(path)[1]
        if display:
            __color_print(0, '發現 ' + filename + ' 帶有BOM頭, 將移除後儲存', no_sn=True)
        try_counter = 0
        while True:
            try:
                with open(path, 'wb') as f:
                    f.write(content)
            except BaseException as e:
                if try_counter > 3:
                    if display:
                        __color_print(0, '無BOM ' + filename + ' 儲存失敗! 發生異常: ' + str(e), status=1, no_sn=True)
                    raise e
                random_wait_time = random.uniform(2, 5)
                time.sleep(random_wait_time)
                try_counter = try_counter + 1
            else:
                if display:
                    __color_print(0, '無BOM ' + filename + ' 儲存成功', status=2, no_sn=True)
                break


def read_settings(config=''):
    if config == '':
        if not os.path.exists(config_path):
            __init_settings()

        settings = __read_settings_file()
    else:
        # used to check whether the config returned from the web dashboard is valid
        settings = config

    if 'database_version' in settings.keys():
        if settings['database_version'] < latest_database_version:
            __update_database(settings['database_version'])
    else:
        # if this config version has no database_version field, the database version should be 1.0
        settings['database_version'] = 1.0
        __update_database(1.0)

    if settings['config_version'] < latest_config_version:
        __update_settings(settings)  # upgrade config
        settings = __read_settings_file()  # reload

    if settings['ftp']['port']:
        settings['ftp']['port'] = int(settings['ftp']['port'])

    # sanity guards
    settings['check_frequency'] = int(settings['check_frequency'])
    settings['download_resolution'] = str(settings['download_resolution'])
    settings['multi-thread'] = int(settings['multi-thread'])
    settings['zerofill'] = int(settings['zerofill'])  # ensure it is an integer
    if not re.match(r'^(all|latest|largest-sn)$', settings['default_download_mode']):
        settings['default_download_mode'] = 'latest'  # if an illegal mode is entered, reset to latest mode
    if settings['quantity_of_logs'] < 1:  # log quantity cannot be less than 1
        settings['quantity_of_logs'] = 7

    if not settings['ua']:
        # if the ua field is empty
        settings['ua'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/72.0.3626.96 Safari/537.36"

    # if the user customized the anime directory and it exists
    if settings['bangumi_dir'] and os.path.exists(settings['bangumi_dir']):
        # normalize the anime path
        settings['bangumi_dir'] = os.path.abspath(settings['bangumi_dir'])
    else:
        # if the user did not customize the anime directory or it does not exist, store in the local bangumi directory
        settings['bangumi_dir'] = os.path.join(data_dir, 'bangumi')

    # if the user customized the temp directory and it exists
    if settings['temp_dir'] and os.path.exists(settings['temp_dir']):
        # normalize the temp path
        settings['temp_dir'] = os.path.abspath(settings['temp_dir'])
    else:
        # if the user did not customize the temp directory or it does not exist, store in the local temp directory
        settings['temp_dir'] = os.path.join(data_dir, 'temp')

    settings['working_dir'] = working_dir
    settings['AniGamerDownloader_version'] = AniGamerDownloader_version

    use_gost = False
    if not (re.match(r'^http://', settings['proxy'].lower())
            or re.match(r'^https://', settings['proxy'].lower())
            or re.match(r'^socks5://', settings['proxy'].lower())  # v12 onward natively supports the socks5 proxy
            or re.match(r'^socks5h://', settings['proxy'].lower())):  # socks5h resolves the domain remotely
        #  if a protocol we do not support ourselves appears
        use_gost = True  # then enable gost
    settings['use_gost'] = use_gost
    if not settings['proxy']:
        settings['use_proxy'] = False

    if settings['multi-thread'] > max_multi_thread:
        # if the thread count exceeds the limit
        settings['multi-thread'] = max_multi_thread

    if settings['multi_downloading_segment'] > max_multi_downloading_segment:
        # if the concurrent segment count exceeds the limit
        settings['multi_downloading_segment'] = max_multi_downloading_segment

    if settings['video_filename_extension'].lower() == 'flv':
        # the flv format produces broken output, force reset
        settings['video_filename_extension'] = 'mp4'

    if settings['video_filename_extension'].lower() != 'mp4':
        # if the container format is not mp4, force-disable metadata fronting
        settings['faststart_movflags'] = False

    if settings['save_logs']:
        # delete expired logs
        __remove_superfluous_logs(settings['quantity_of_logs'])

    if settings['use_dashboard']:
        # if the dashboard is enabled, check whether the Dashboard directory exists
        if not os.path.exists(web_dir):
            settings['use_dashboard'] = False
            __color_print(0, 'Web控制面板', '未發現控制面板所必須的 web 資料夾, 強制禁用控制面板!', no_sn=True, status=1)
            write_settings(settings)

    return settings


def check_encoding(file_path):
    # detect file encoding, convert non-UTF-8 encodings to UTF-8
    with open(file_path, 'rb') as f:
        data = f.read()
        file_encoding = chardet.detect(data)['encoding']  # detect file encoding
        if file_encoding == 'utf-8' or file_encoding == 'ascii':
            # if it is UTF-8 encoded, no action needed
            return
        else:
            # if it is another encoding, convert to UTF-8, including handling the BOM header
            with open(file_path, 'wb') as f2:
                __color_print(0, '檔案讀取', file_path + ' 編碼為 ' + file_encoding + ' 將轉碼為 UTF-8', no_sn=True, status=1)
                data = data.decode(file_encoding)  # decode
                data = data.encode('utf-8')  # encode
                f2.write(data)  # write to file
                __color_print(0, '檔案讀取', file_path + ' 轉碼成功', no_sn=True, status=2)


def read_sn_list():
    settings = read_settings()

    # sanity guard https://github.com/miyouzi/aniGamerPlus/issues/5
    error_sn_list_path = sn_list_path.replace('sn_list.txt', 'sn_list.txt.txt')
    if os.path.exists(error_sn_list_path):
        os.rename(error_sn_list_path, sn_list_path)

    if not os.path.exists(sn_list_path):
        return {}

    if not os.path.getsize(sn_list_path):
        # if the file is empty, https://github.com/miyouzi/aniGamerPlus/issues/38
        return {}

    # del_bom(sn_list_path)  # strip BOM
    check_encoding(sn_list_path)
    with open(sn_list_path, 'r', encoding='utf-8') as f:
        sn_dict = {}
        bangumi_tag = ''
        for i in f.readlines():
            if re.match(r'^@.+', i):  # read the anime category
                bangumi_tag = i[1:-1]
                continue
            elif re.match(r'^@ *', i):
                bangumi_tag = ''
                continue
            i = re.sub(r'#.+\n', '', i).strip()  # delete comments
            i = re.sub(r' +', ' ', i)  # remove extra spaces
            a = i.split(" ")
            if not a[0]:  # skip pure-comment lines
                continue
            if re.match(r'^\d+$', a[0]):
                rename = ''
                if len(a) > 1:  # if a download mode is explicitly specified
                    if re.match(r'^(all|latest|largest-sn)$', a[1]):  # only accept legal modes
                        sn_dict[int(a[0])] = {'mode': a[1]}
                    else:
                        sn_dict[int(a[0])] = {'mode': settings['default_download_mode']}  # replace any illegal mode with the default mode
                    # whether anime rename is configured
                    if re.match(r'.*<.*>.*', i):
                        rename = re.findall(r'<.*>', i)[0][1:-1]
                else:  # if no download mode is specified, use the default setting
                    sn_dict[int(a[0])] = {'mode': settings['default_download_mode']}
                bangumi_tag = re.sub(r"( )+$", "", bangumi_tag)
                sn_dict[int(a[0])]['tag'] = bangumi_tag
                sn_dict[int(a[0])]['rename'] = rename
        return sn_dict


def time_stamp_to_time(timestamp):
    # convert a timestamp to a time: 1479264792 to 2016-11-16 10:53:12
    # code from: https://www.cnblogs.com/shaosks/p/5614630.html
    timeStruct = time.localtime(timestamp)
    return time.strftime('%Y-%m-%d %H:%M:%S', timeStruct)


def read_latest_version_on_github():
    req = 'https://api.github.com/repos/miyouzi/aniGamerPlus/releases/latest'
    session = requests.session()
    remote_version = {}
    try:
        latest_releases_info = session.get(req, timeout=3).json()
        remote_version['tag_name'] = latest_releases_info['tag_name']
        remote_version['body'] = latest_releases_info['body']  # update content
        __color_print(0, '檢查更新', '檢查更新成功', no_sn=True, display=False)
    except:
        remote_version['tag_name'] = AniGamerDownloader_version  # failed to fetch the github version number
        remote_version['body'] = ''
        __color_print(0, '檢查更新', '檢查更新失敗', no_sn=True, display=False)
    return remote_version


def __remove_superfluous_logs(max_num):
    if os.path.exists(logs_dir):
        logs_list = [x for x in os.listdir(logs_dir) if 'web' not in x]
        if len(logs_list) > max_num:
            logs_list.sort()
            logs_need_remove = logs_list[0:len(logs_list) - max_num]
            for log in logs_need_remove:
                log_path = os.path.join(logs_dir, log)
                os.remove(log_path)
                __color_print(0, '刪除過期日誌: ' + log, no_sn=True, display=False)


def write_settings(web_config):
    web_config = read_settings(web_config)  # normalize the config

    # restore the config
    a = os.path.join(data_dir, 'bangumi')  # default anime directory
    b = os.path.join(data_dir, 'temp')  # default temp directory
    if os.path.normcase(web_config['bangumi_dir']) == os.path.normcase(a):
        web_config["bangumi_dir"] = ''
    if os.path.normcase(web_config['temp_dir']) == os.path.normcase(b):
        web_config['temp_dir'] = ''
    del web_config['working_dir']
    del web_config['AniGamerDownloader_version']
    del web_config['use_gost']

    # write the config to disk
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(web_config, f, ensure_ascii=False, indent=4)


def write_sn_list(sn_list_content):
    with open(sn_list_path, 'w', encoding='utf-8') as f:
        f.write(sn_list_content)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except:
        local_ip.close()
    return local_ip


def parse_proxy(proxy_str: str) -> dict:
    if len(proxy_str) == 0 or proxy_str.isspace():
        return {}

    result = {}

    if re.match(r'.*@.*', proxy_str):
        proxy_user = re.sub(r':(\/\/)?', '', re.findall(r':\/\/.*?:', proxy_str)[0])
        proxy_passwd = re.sub(r'(:\/\/:)?@?', '', re.sub(proxy_user, '', re.findall(r':.*@', proxy_str)[0]))
        result['proxy_user'] = proxy_user
        result['proxy_passwd'] = proxy_passwd
        proxy_str = proxy_str.replace(proxy_user + ':' + proxy_passwd + '@', '')
    else:
        result['proxy_user'] = None
        result['proxy_passwd'] = None

    proxy_protocol = re.sub(r':\/\/.*', '', proxy_str).upper()
    proxy_ip = re.sub(r':(\/\/)?', '', re.findall(r':.*:', proxy_str)[0])
    proxy_port = re.sub(r':', '', re.findall(r':\d+', proxy_str)[0])

    result['proxy_protocol'] = proxy_protocol
    result['proxy_ip'] = proxy_ip
    result['proxy_port'] = proxy_port

    return result


if __name__ == '__main__':
    pass
