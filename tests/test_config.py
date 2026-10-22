#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.config -- config.json parsing, defaults, upgrade and sn_list parsing.

``core.config`` keeps its paths in module globals. Every test runs inside a
sandbox that repoints them (config_path / data_dir / logs_dir / sn_list_path /
cookie_path / web_dir) at a scratch directory and replaces ``__update_database``
with a mock -- its real default targets ``data/aniGamer.db``.

The module's helpers are double-underscore *module* functions; they are fetched
with ``getattr`` because ``config.__x`` inside a class body would be name-mangled.
"""

import codecs
import json
import os
import unittest
from unittest import mock

from tests import LOG, make_tmpdir
from core import config

loads_config = getattr(config, '__loads_config')
update_settings = getattr(config, '__update_settings')


class ConfigSandbox(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        root = make_tmpdir(self)
        self.data_dir = os.path.join(root, 'data')
        self.web_dir = os.path.join(root, 'web')
        os.makedirs(self.data_dir)
        os.makedirs(self.web_dir)   # present, so use_dashboard is not force-disabled
        self.config_path = os.path.join(self.data_dir, 'config.json')
        self.sn_list_path = os.path.join(self.data_dir, 'sn_list.txt')
        self.logs_dir = os.path.join(self.data_dir, 'logs')
        self.update_db = mock.Mock(name='__update_database')
        for name, value in (('data_dir', self.data_dir), ('web_dir', self.web_dir),
                            ('config_path', self.config_path),
                            ('sn_list_path', self.sn_list_path),
                            ('logs_dir', self.logs_dir),
                            ('cookie_path', os.path.join(self.data_dir, 'cookie.txt')),
                            ('_warned_dir_fallback', set()),
                            ('__update_database', self.update_db)):
            p = mock.patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)

    # -- helpers --
    def defaults(self):
        """A full default config dict, as a fresh install writes it."""
        config.read_settings()
        with open(self.config_path, encoding='utf-8') as f:
            return json.load(f)

    def write_config(self, settings, encoding='utf-8', bom=False):
        data = json.dumps(settings, ensure_ascii=False, indent=4).encode(encoding)
        with open(self.config_path, 'wb') as f:
            f.write((codecs.BOM_UTF8 if bom else b'') + data)

    def read_config_file(self):
        with open(self.config_path, encoding='utf-8') as f:
            return json.load(f)

    def warnings_about(self, name):
        return [c for c in LOG.with_status(1) if name in c['err_msg']]


# ---------------------------------------------------------------------------
class LegalizeFilename(unittest.TestCase):
    def test_each_illegal_char(self):
        self.assertEqual(config.legalize_filename('a|b?c*d<e>f"g:h\\i/j'),
                         'a｜b？c＊d＜e＞f＂g：h＼i／j')

    def test_result_has_no_windows_illegal_chars(self):
        out = config.legalize_filename('Re:Zero? <第2季> "OVA" 1/2 *特別篇* a|b \\x')
        for ch in '\\/:*?"<>|':
            self.assertNotIn(ch, out)

    def test_clean_name_unchanged(self):
        self.assertEqual(config.legalize_filename('【動畫瘋】某動畫 [01] 1080P'),
                         '【動畫瘋】某動畫 [01] 1080P')


# ---------------------------------------------------------------------------
class LoadsConfig(unittest.TestCase):
    def test_program_written_json(self):
        text = json.dumps({'bangumi_dir': 'F:\\capture', 'temp_dir': 'F:\\capture\\.temp'})
        self.assertEqual(loads_config(text), {'bangumi_dir': 'F:\\capture',
                                              'temp_dir': 'F:\\capture\\.temp'})

    def test_round_trip_does_not_double_backslashes(self):
        value = {'bangumi_dir': 'F:\\capture\\anime'}
        for _ in range(3):
            value = loads_config(json.dumps(value))
        self.assertEqual(value['bangumi_dir'], 'F:\\capture\\anime')

    def test_hand_typed_raw_windows_path(self):
        # a human typed  "F:\capture"  (one backslash) -- invalid JSON escape \c
        text = '{"bangumi_dir": "F:\\capture", "temp_dir": "F:\\capture\\.temp"}'
        self.assertEqual(loads_config(text), {'bangumi_dir': 'F:\\capture',
                                              'temp_dir': 'F:\\capture\\.temp'})

    def test_hand_typed_raw_path_that_is_a_valid_json_escape(self):
        # "F:\temp" is ALSO valid JSON (\t = TAB), so json.loads succeeds and the
        # raw-path fallback never runs; the path keys must still come back intact.
        for raw in ('F:\\temp', 'D:\\new', 'E:\\rclone', 'C:\\bangumi', 'G:\\films',
                    'F:\\temp\\new', 'F:\\temp\\.cache'):
            with self.subTest(raw=raw):
                text = '{"bangumi_dir": "%s", "temp_dir": "%s"}' % (raw, raw)
                self.assertEqual(loads_config(text), {'bangumi_dir': raw, 'temp_dir': raw})

    def test_escapes_outside_path_keys_are_untouched(self):
        # a newline in a message suffix is a legitimate value, not a broken path
        text = json.dumps({'temp_dir': 'F:\\capture', 'coolq_settings': {'message_suffix': 'a\nb'},
                           'customized_video_filename_suffix': 'x\ty'})
        got = loads_config(text)
        self.assertEqual(got['coolq_settings']['message_suffix'], 'a\nb')
        self.assertEqual(got['customized_video_filename_suffix'], 'x\ty')

    def test_non_dict_json_passes_through(self):
        self.assertEqual(loads_config('[1, 2]'), [1, 2])


# ---------------------------------------------------------------------------
class FreshInstallDefaults(ConfigSandbox):
    def test_missing_config_is_created_with_documented_defaults(self):
        s = config.read_settings()
        self.assertTrue(os.path.exists(self.config_path))
        expected = {
            'start_paused': True,               # startup is offline
            'auto_pause_after_failures': 5,
            'min_resolution': 720,
            'download_resolution': '1080',
            'lock_resolution': False,
            'default_download_mode': 'latest',
            'skip_chinese_dub': True,
            'check_frequency': 5,
            'multi-thread': 1,
            'config_version': 17.5,
            'database_version': 2.0,
            'AniGamerDownloader_version': config.AniGamerDownloader_version,
        }
        self.assertEqual({k: s[k] for k in expected}, expected)
        for obsolete in ('use_proxy', 'proxy', 'proxies', 'use_gost', 'no_proxy_akamai'):
            self.assertNotIn(obsolete, s)

    def test_version_string_is_semver_tag(self):
        # CI version-guard compares this to a three-component vX.Y.Z tag
        self.assertRegex(config.AniGamerDownloader_version, r'^v\d+\.\d+\.\d+$')

    def test_empty_dirs_fall_back_silently(self):
        s = config.read_settings()
        self.assertEqual(s['bangumi_dir'], os.path.join(self.data_dir, 'bangumi'))
        self.assertEqual(s['temp_dir'], os.path.join(self.data_dir, 'temp'))
        self.assertEqual(self.warnings_about('config.json'), [])

    def test_file_does_not_store_runtime_keys(self):
        config.read_settings()
        on_disk = self.read_config_file()
        self.assertNotIn('working_dir', on_disk)
        self.assertNotIn('AniGamerDownloader_version', on_disk)

    def test_current_db_version_does_not_migrate(self):
        config.read_settings()
        self.update_db.assert_not_called()


# ---------------------------------------------------------------------------
class SanityGuards(ConfigSandbox):
    """read_settings(config=dict) is the web dashboard's validation path."""

    def check(self, **overrides):
        s = self.defaults()
        s.update(overrides)
        return config.read_settings(s)

    def test_thread_counts_are_capped(self):
        s = self.check(**{'multi-thread': 99, 'multi_downloading_segment': 50})
        self.assertEqual((s['multi-thread'], s['multi_downloading_segment']), (5, 5))

    def test_numeric_strings_are_coerced(self):
        s = self.check(**{'check_frequency': '15', 'multi-thread': '2', 'zerofill': '3',
                          'download_resolution': 720})
        self.assertEqual((s['check_frequency'], s['multi-thread'], s['zerofill'],
                          s['download_resolution']), (15, 2, 3, '720'))

    def test_ftp_port_string_becomes_int(self):
        d = self.defaults()
        d['ftp']['port'] = '21'
        self.assertEqual(config.read_settings(d)['ftp']['port'], 21)

    def test_illegal_download_mode_resets_to_latest(self):
        self.assertEqual(self.check(default_download_mode='everything')['default_download_mode'], 'latest')
        self.assertEqual(self.check(default_download_mode='largest-sn')['default_download_mode'], 'largest-sn')
        self.assertEqual(self.check(default_download_mode='all')['default_download_mode'], 'all')

    def test_log_quantity_below_one_resets(self):
        self.assertEqual(self.check(quantity_of_logs=0)['quantity_of_logs'], 7)

    def test_empty_ua_gets_a_default(self):
        self.assertTrue(self.check(ua='')['ua'])

    def test_flv_forced_to_mp4(self):
        self.assertEqual(self.check(video_filename_extension='FLV')['video_filename_extension'], 'mp4')

    def test_faststart_only_for_mp4(self):
        s = self.check(video_filename_extension='mkv', faststart_movflags=True)
        self.assertFalse(s['faststart_movflags'])
        s = self.check(video_filename_extension='mp4', faststart_movflags=True)
        self.assertTrue(s['faststart_movflags'])

    def test_existing_custom_dir_is_kept(self):
        target = os.path.join(make_tmpdir(self), 'capture')
        os.makedirs(target)
        s = self.check(bangumi_dir=target, temp_dir=target)
        self.assertEqual((s['bangumi_dir'], s['temp_dir']), (target, target))

    def test_missing_custom_dir_falls_back_and_warns_once(self):
        gone = os.path.join(self.data_dir, 'no-such-drive', 'capture')
        s = self.check(bangumi_dir=gone)
        self.assertEqual(s['bangumi_dir'], os.path.join(self.data_dir, 'bangumi'))
        self.check(bangumi_dir=gone)   # read_settings runs every cycle
        self.assertEqual(len([c for c in LOG.with_status(1) if 'bangumi_dir' in c['err_msg']]), 1)

    def test_missing_web_dir_disables_dashboard(self):
        os.rmdir(self.web_dir)
        self.assertFalse(self.check(use_dashboard=True)['use_dashboard'])
        self.assertFalse(self.read_config_file()['use_dashboard'])

    def test_missing_database_version_means_v1(self):
        d = self.defaults()
        del d['database_version']
        config.read_settings(d)
        self.update_db.assert_called_once_with(1.0)

    def test_old_database_version_migrates(self):
        config.read_settings(dict(self.defaults(), database_version=1.0))
        self.update_db.assert_called_once_with(1.0)


# ---------------------------------------------------------------------------
class OldLogRotation(ConfigSandbox):
    def test_keeps_newest_n_skips_web_logs_and_diag(self):
        os.makedirs(os.path.join(self.logs_dir, 'diag'))
        days = ['2026-10-%02d.log' % d for d in range(1, 11)]   # 10 daily logs
        for name in days + ['web_2026-10-01.log']:
            open(os.path.join(self.logs_dir, name), 'w').close()
        config.read_settings(dict(self.defaults(), save_logs=True, quantity_of_logs=7))
        self.assertEqual(sorted(os.listdir(self.logs_dir)),
                         sorted(days[3:] + ['web_2026-10-01.log', 'diag']))


# ---------------------------------------------------------------------------
class UpgradeOldConfig(ConfigSandbox):
    def old_config(self, **extra):
        old = {
            'config_version': 1.0,
            'database_version': 2.0,
            'bangumi_dir': '',
            'check_frequency': 10,
            'download_resolution': '720',
            'multi-thread': 2,
            'default_download_mode': 'all',
            'ftp': {'server': '', 'port': '', 'user': '', 'pwd': '', 'cwd': ''},
            # keys removed by the fork
            'use_proxy': True, 'proxy': 'http://127.0.0.1:1080', 'no_proxy_akamai': False,
            'use_gost': False, 'audio_language_jpn': True,
        }
        old.update(extra)
        self.write_config(old)
        return old

    def test_upgrade_preserves_values_adds_defaults_drops_obsolete(self):
        self.old_config()
        s = config.read_settings()
        self.assertEqual((s['check_frequency'], s['download_resolution'], s['multi-thread'],
                          s['default_download_mode']), (10, '720', 2, 'all'))
        self.assertEqual((s['min_resolution'], s['start_paused'], s['auto_pause_after_failures'],
                          s['skip_chinese_dub'], s['config_version']), (720, True, 5, True, 17.5))
        for gone in ('use_proxy', 'proxy', 'no_proxy_akamai', 'use_gost', 'audio_language_jpn'):
            self.assertNotIn(gone, s)

    def test_upgrade_is_written_back(self):
        self.old_config()
        config.read_settings()
        on_disk = self.read_config_file()
        self.assertEqual(on_disk['config_version'], 17.5)
        self.assertEqual(on_disk['min_resolution'], 720)
        self.assertNotIn('use_proxy', on_disk)

    def test_legacy_coolq_settings_become_a_url(self):
        self.old_config(coolq_notify=True, coolq_settings={
            'host': '127.0.0.1', 'port': '5700', 'SSL': False, 'api': 'send_group_msg',
            'query': {'group_id': '123'}, 'user_message': 'hi'})
        cq = config.read_settings()['coolq_settings']
        self.assertEqual(cq['query'], ['http://127.0.0.1:5700/send_group_msg?group_id=123'])
        self.assertEqual(cq['message_suffix'], 'hi')
        for gone in ('host', 'port', 'SSL', 'api', 'user_message'):
            self.assertNotIn(gone, cq)

    def test_upgrade_keeps_users_segment_max_retry(self):
        # the guard used to test 'segment_download_max_retry' while writing
        # 'segment_max_retry', so every upgrade reset the user's value to 8
        self.old_config(segment_max_retry=-1)   # -1 = retry forever
        self.assertEqual(config.read_settings()['segment_max_retry'], -1)

    def test_upgrade_adds_missing_segment_max_retry(self):
        self.old_config()
        self.assertEqual(config.read_settings()['segment_max_retry'], 8)

    def test_hand_typed_path_reaches_the_fallback_warning_intact(self):
        # end to end: a raw  Q:\temp\nope...  in config.json (\t and \n are both valid
        # JSON escapes) must be reported as typed, not with a TAB and a newline in it
        raw = 'Q:\\temp\\nope-anigamer-unit-test'
        text = json.dumps(dict(self.defaults(), temp_dir='@@'), ensure_ascii=False).replace('"@@"', '"%s"' % raw)
        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.write(text)
        config.read_settings()
        warned = self.warnings_about('temp_dir')
        self.assertEqual(len(warned), 1)
        self.assertIn(raw, warned[0]['err_msg'])


# ---------------------------------------------------------------------------
class FileRecovery(ConfigSandbox):
    def test_garbage_file_resets_to_defaults_and_logs(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            f.write('{this is not json')
        s = config.read_settings()
        self.assertEqual(s['min_resolution'], 720)
        self.assertEqual(self.read_config_file()['config_version'], 17.5)
        self.assertTrue(LOG.with_status(1))

    def test_utf8_bom_is_tolerated_and_values_kept(self):
        self.write_config(dict(self.defaults(), check_frequency=17), bom=True)
        self.assertEqual(config.read_settings()['check_frequency'], 17)

    def test_cp950_file_is_transcoded_not_reset(self):
        # reading cp950 as UTF-8 raises UnicodeDecodeError, which used to skip the
        # check_encoding() branch and fall into reset-to-defaults
        self.write_config(dict(self.defaults(), check_frequency=17,
                               customized_video_filename_prefix='【動畫瘋】巴哈'), encoding='cp950')
        s = config.read_settings()
        self.assertEqual(s['check_frequency'], 17)
        self.assertEqual(s['customized_video_filename_prefix'], '【動畫瘋】巴哈')
        # converted in place to UTF-8
        self.assertEqual(self.read_config_file()['customized_video_filename_prefix'], '【動畫瘋】巴哈')


# ---------------------------------------------------------------------------
class WriteSettings(ConfigSandbox):
    def test_default_dirs_are_stored_as_empty(self):
        config.write_settings(config.read_settings())
        on_disk = self.read_config_file()
        self.assertEqual((on_disk['bangumi_dir'], on_disk['temp_dir']), ('', ''))
        self.assertNotIn('working_dir', on_disk)
        self.assertNotIn('AniGamerDownloader_version', on_disk)

    def test_windows_path_survives_repeated_round_trips(self):
        target = os.path.join(make_tmpdir(self), 'capture')
        os.makedirs(target)
        self.write_config(dict(self.defaults(), bangumi_dir=target))
        for _ in range(3):
            config.write_settings(config.read_settings())
        self.assertEqual(self.read_config_file()['bangumi_dir'], target)


# ---------------------------------------------------------------------------
class DelBom(ConfigSandbox):
    def test_strips_bom(self):
        p = os.path.join(self.data_dir, 'x.txt')
        with open(p, 'wb') as f:
            f.write(codecs.BOM_UTF8 + '12345 all\n'.encode('utf-8'))
        config.del_bom(p, display=False)
        with open(p, 'rb') as f:
            self.assertEqual(f.read(), b'12345 all\n')

    def test_no_bom_untouched(self):
        p = os.path.join(self.data_dir, 'x.txt')
        with open(p, 'wb') as f:
            f.write('動畫\n'.encode('utf-8'))
        config.del_bom(p, display=False)
        with open(p, 'rb') as f:
            self.assertEqual(f.read(), '動畫\n'.encode('utf-8'))


# ---------------------------------------------------------------------------
class SnList(ConfigSandbox):
    """Format (docs/SETUP_GUIDE.md): ``sn 模式 <自訂資料夾名> # 註解``; modes all/latest/largest-sn.
    ``@名稱`` lines set a tag for the following entries, a bare ``@`` clears it."""

    def setUp(self):
        super().setUp()
        self.write_config(dict(self.defaults(), default_download_mode='all'))

    def parse(self, text, encoding='utf-8', bom=False):
        with open(self.sn_list_path, 'wb') as f:
            f.write((codecs.BOM_UTF8 if bom else b'') + text.encode(encoding))
        return config.read_sn_list()

    def test_full_example(self):
        got = self.parse(
            '# my list\n'
            '@Season A  \n'
            '12345 latest <My Show>  # first\n'
            '23456\n'
            '34567 bogus\n'
            '45678 largest-sn # comment\n'
            '@\n'
            '56789 all\n'
            '\n')
        self.assertEqual(got, {
            12345: {'mode': 'latest', 'tag': 'Season A', 'rename': 'My Show'},
            23456: {'mode': 'all', 'tag': 'Season A', 'rename': ''},     # default mode
            34567: {'mode': 'all', 'tag': 'Season A', 'rename': ''},     # illegal -> default
            45678: {'mode': 'largest-sn', 'tag': 'Season A', 'rename': ''},
            56789: {'mode': 'all', 'tag': '', 'rename': ''},
        })

    def test_rename_without_mode(self):
        self.assertEqual(self.parse('11111 <某動畫>\n'),
                         {11111: {'mode': 'all', 'tag': '', 'rename': '某動畫'}})

    def test_crlf_line_endings(self):
        self.assertEqual(self.parse('@T\r\n11111 latest\r\n'),
                         {11111: {'mode': 'latest', 'tag': 'T', 'rename': ''}})

    def test_utf8_bom(self):
        self.assertEqual(self.parse('11111 latest\n', bom=True),
                         {11111: {'mode': 'latest', 'tag': '', 'rename': ''}})

    def test_missing_or_empty_file(self):
        self.assertEqual(config.read_sn_list(), {})
        self.assertEqual(self.parse(''), {})

    def test_double_txt_extension_is_repaired(self):
        # issue #5: Windows users saving "sn_list.txt.txt"
        with open(self.sn_list_path + '.txt', 'w', encoding='utf-8') as f:
            f.write('11111 latest\n')
        self.assertEqual(config.read_sn_list(), {11111: {'mode': 'latest', 'tag': '', 'rename': ''}})
        self.assertTrue(os.path.exists(self.sn_list_path))

    def test_comment_on_last_line_without_newline(self):
        # comments used to be stripped with r'#.+\n', so a final line with no
        # trailing newline kept its comment and the greedy <.*> grabbed across it.
        # The web 片單 editor saves the textarea verbatim, so this is the normal case.
        self.assertEqual(self.parse('67890 all <Real Name> # was <Old Name>'),
                         {67890: {'mode': 'all', 'tag': '', 'rename': 'Real Name'}})

    def test_comment_without_newline_after_mode(self):
        self.assertEqual(self.parse('11111 latest\n22222 largest-sn #note'), {
            11111: {'mode': 'latest', 'tag': '', 'rename': ''},
            22222: {'mode': 'largest-sn', 'tag': '', 'rename': ''},
        })

    def test_bare_hash_comment(self):
        self.assertEqual(self.parse('11111 latest #\n'),
                         {11111: {'mode': 'latest', 'tag': '', 'rename': ''}})


# ---------------------------------------------------------------------------
class GetLocalIp(unittest.TestCase):
    """No real socket: socket.socket is replaced by a fake for each test."""

    def fake_socket(self, connect_error=None):
        created = []

        class FakeSocket:
            def __init__(self, *args):
                self.closed = False
                created.append(self)

            def connect(self, addr):
                if connect_error:
                    raise connect_error

            def getsockname(self):
                return ('192.168.1.23', 50000)

            def close(self):
                self.closed = True

        p = mock.patch.object(config.socket, 'socket', FakeSocket)
        p.start()
        self.addCleanup(p.stop)
        return created

    def test_returns_interface_address_and_closes(self):
        created = self.fake_socket()
        self.assertEqual(config.get_local_ip(), '192.168.1.23')
        self.assertTrue(created[0].closed)

    def test_offline_falls_back_to_loopback_and_closes(self):
        # used to raise UnboundLocalError (local_ip.close() on an unbound name)
        created = self.fake_socket(OSError(10051, 'network is unreachable'))
        self.assertEqual(config.get_local_ip(), '127.0.0.1')
        self.assertTrue(created[0].closed)


if __name__ == '__main__':
    unittest.main()
