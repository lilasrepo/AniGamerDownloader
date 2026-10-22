#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.db -- the SQLite inventory layer.

Every test uses a throwaway database file under ``tmp/unit-tests/``. (``:memory:``
cannot work here: each helper opens its own connection, so every call would see
a fresh empty database.) ``db_path`` is always passed explicitly -- the
``update_database(db_path=None)`` default points at the real ``data/aniGamer.db``.
"""

import os
import sqlite3
import unittest

from tests import LogRecorder, make_tmpdir
from core import db


class FakeAnime:
    """The slice of the AnimeDownloader interface that insert_db / update_db read."""

    def __init__(self, sn, title='', bangumi_name='', episode='', video_size=0,
                 upload_succeed_flag=False, video_resolution=0, local_video_path=''):
        self._sn, self._title, self._name, self._ep = sn, title, bangumi_name, episode
        self.video_size = video_size
        self.upload_succeed_flag = upload_succeed_flag
        self.video_resolution = video_resolution
        self.local_video_path = local_video_path

    def get_sn(self):
        return self._sn

    def get_title(self):
        return self._title

    def get_bangumi_name(self):
        return self._name

    def get_episode(self):
        return self._ep


def raw_rows(path, sql, params=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(make_tmpdir(self), 'test.db')

    def assertLockFree(self):
        # every helper must release the module-wide semaphore, even on error
        self.assertTrue(db.db_locker.acquire(blocking=False), 'db_locker left held')
        db.db_locker.release()

    def seed(self, *animes):
        db.init_db(self.path)
        for a in animes:
            db.insert_db(self.path, a)


class StatusConstants(unittest.TestCase):
    def test_values(self):
        # CLAUDE.md: 0 pending / 1 done / 2 = STATUS_SKIPPED
        self.assertEqual((db.STATUS_PENDING, db.STATUS_DONE, db.STATUS_SKIPPED), (0, 1, 2))


class InitAndInsert(DbTestCase):
    def test_init_is_idempotent_and_keeps_rows(self):
        self.seed(FakeAnime(1, 'T [1]', 'T', '1'))
        db.init_db(self.path)
        self.assertEqual(len(db.read_db_all(self.path)), 1)

    def test_insert_then_read_with_schema_defaults(self):
        self.seed(FakeAnime(12345, '某動畫 [01]', '某動畫', '01'))
        self.assertEqual(db.read_db(self.path, 12345), {
            'sn': 12345, 'title': '某動畫 [01]', 'anime_name': '某動畫', 'episode': '01',
            'status': 0, 'remote_status': 0, 'resolution': 0, 'file_size': 0,
            'local_file_path': None,
        })
        self.assertLockFree()

    def test_episode_keeps_leading_zero(self):
        self.seed(FakeAnime(1, 't', 'n', '07'))
        self.assertEqual(db.read_db(self.path, 1)['episode'], '07')

    def test_duplicate_insert_is_logged_not_raised(self):
        self.seed(FakeAnime(5, 'first', 'n', '1'))
        log = LogRecorder()
        db.insert_db(self.path, FakeAnime(5, 'second', 'n', '1'), logger=log)
        self.assertEqual(len(log.with_status(1)), 1)
        self.assertEqual(db.read_db(self.path, 5)['title'], 'first')
        self.assertEqual(len(db.read_db_all(self.path)), 1)
        self.assertLockFree()


class Read(DbTestCase):
    def test_read_missing_sn_raises_indexerror_and_releases_lock(self):
        self.seed(FakeAnime(1, 't', 'n', '1'))
        with self.assertRaises(IndexError):
            db.read_db(self.path, 999)
        self.assertLockFree()

    def test_read_all(self):
        self.seed(FakeAnime(2, 'b', 'n', '2'), FakeAnime(1, 'a', 'n', '1'))
        rows = sorted(db.read_db_all(self.path), key=lambda r: r['sn'])
        self.assertEqual([(r['sn'], r['title']) for r in rows], [(1, 'a'), (2, 'b')])

    def test_read_all_empty(self):
        db.init_db(self.path)
        self.assertEqual(db.read_db_all(self.path), [])

    def test_read_all_without_table_raises_and_releases_lock(self):
        with self.assertRaises(sqlite3.OperationalError):
            db.read_db_all(self.path)
        self.assertLockFree()


class Update(DbTestCase):
    def test_successful_download(self):
        self.seed(FakeAnime(10, 't', 'n', '1'))
        db.update_db(self.path, FakeAnime(10, 't', 'n', '1', video_size=734003200,
                                          upload_succeed_flag=True, video_resolution=1080,
                                          local_video_path=r'F:\capture\t\t [1].mp4'))
        row = db.read_db(self.path, 10)
        self.assertEqual((row['status'], row['remote_status'], row['resolution'],
                          row['file_size'], row['local_file_path']),
                         (1, 1, 1080, 734003200, r'F:\capture\t\t [1].mp4'))
        self.assertLockFree()

    def test_zero_size_download_stays_pending(self):
        self.seed(FakeAnime(10, 't', 'n', '1'))
        db.update_db(self.path, FakeAnime(10, 't', 'n', '1', video_size=0))
        row = db.read_db(self.path, 10)
        self.assertEqual((row['status'], row['remote_status']), (0, 0))

    def test_update_other_rows_untouched(self):
        self.seed(FakeAnime(10, 't', 'n', '1'), FakeAnime(11, 'u', 'n', '2'))
        db.update_db(self.path, FakeAnime(10, 't', 'n', '1', video_size=10 ** 8, video_resolution=720))
        self.assertEqual(db.read_db(self.path, 11)['status'], 0)


class SkipResetDelete(DbTestCase):
    def setUp(self):
        super().setUp()
        self.seed(FakeAnime(1, 'a', 'n', '1'), FakeAnime(2, 'b', 'n', '2'), FakeAnime(3, 'c', 'n', '3'))

    def test_mark_skipped_records_ceiling(self):
        db.mark_db_skipped(self.path, 1, available=540)
        row = db.read_db(self.path, 1)
        self.assertEqual((row['status'], row['resolution']), (2, 540))
        self.assertLockFree()

    def test_mark_skipped_without_available(self):
        db.mark_db_skipped(self.path, '2', available=None)
        row = db.read_db(self.path, 2)
        self.assertEqual((row['status'], row['resolution']), (2, 0))

    def test_reset_done_row(self):
        db.update_db(self.path, FakeAnime(1, 'a', 'n', '1', video_size=10 ** 8,
                                          upload_succeed_flag=True, video_resolution=1080,
                                          local_video_path='x.mp4'))
        db.reset_db_status(self.path, 1)
        row = db.read_db(self.path, 1)
        self.assertEqual((row['status'], row['remote_status']), (0, 0))
        # reset only touches the status columns
        self.assertEqual((row['resolution'], row['file_size'], row['local_file_path']),
                         (1080, 10 ** 8, 'x.mp4'))

    def test_reset_skipped_row(self):
        # 重置 puts a skipped row back to pending
        db.mark_db_skipped(self.path, 3, available=360)
        db.reset_db_status(self.path, '3')
        self.assertEqual(db.read_db(self.path, 3)['status'], 0)

    def test_delete_counts_only_existing_rows(self):
        self.assertEqual(db.delete_db_rows(self.path, [1, '3', 999]), 2)
        self.assertEqual([r['sn'] for r in db.read_db_all(self.path)], [2])
        self.assertLockFree()

    def test_delete_nothing_does_not_open_db(self):
        missing = os.path.join(os.path.dirname(self.path), 'never-created.db')
        self.assertEqual(db.delete_db_rows(missing, []), 0)
        self.assertFalse(os.path.exists(missing))


class Migration(DbTestCase):
    # v1.0 shape reconstructed from what update_database expects: the key column
    # was ``ns`` and there was no ``local_file_path``.
    V1_SCHEMA = ('CREATE TABLE anime ('
                 'ns INTEGER PRIMARY KEY NOT NULL,'
                 'title VARCHAR(100) NOT NULL,'
                 'anime_name VARCHAR(100) NOT NULL, '
                 'episode VARCHAR(10) NOT NULL,'
                 'status TINYINT DEFAULT 0,'
                 'remote_status INTEGER DEFAULT 0,'
                 'resolution INTEGER DEFAULT 0,'
                 'file_size INTEGER DEFAULT 0,'
                 "[CreatedTime] TimeStamp NOT NULL DEFAULT (datetime('now','localtime')))")

    def columns(self):
        return [r[1] for r in raw_rows(self.path, 'PRAGMA table_info(anime)')]

    def tables(self):
        return sorted(r[0] for r in raw_rows(self.path, "SELECT name FROM sqlite_master WHERE type='table'"))

    def test_v1_ns_column_migrates_to_sn_keeping_data(self):
        conn = sqlite3.connect(self.path)
        conn.execute(self.V1_SCHEMA)
        conn.execute("INSERT INTO anime (ns,title,anime_name,episode,status,remote_status,resolution,"
                     "file_size,[CreatedTime]) VALUES (777,'T [1]','T','1',1,0,1080,123456,'2019-01-05 20:23:00')")
        conn.commit()
        conn.close()

        log = LogRecorder()
        db.update_database(1.0, db_path=self.path, logger=log)

        self.assertEqual(self.tables(), ['anime'])
        cols = self.columns()
        self.assertIn('sn', cols)
        self.assertIn('local_file_path', cols)
        self.assertNotIn('ns', cols)
        self.assertEqual(db.read_db(self.path, 777), {
            'sn': 777, 'title': 'T [1]', 'anime_name': 'T', 'episode': '1',
            'status': 1, 'remote_status': 0, 'resolution': 1080, 'file_size': 123456,
            'local_file_path': None,
        })
        self.assertEqual(raw_rows(self.path, 'SELECT [CreatedTime] FROM anime'),
                         [('2019-01-05 20:23:00',)])
        self.assertEqual(len(log.with_status(2)), 1)

    def test_missing_table_is_created(self):
        db.update_database(1.0, db_path=self.path, logger=LogRecorder())
        self.assertEqual(self.tables(), ['anime'])
        self.assertIn('local_file_path', self.columns())

    def test_current_schema_is_left_alone(self):
        self.seed(FakeAnime(1, 'a', 'n', '1'))
        before = self.columns()
        db.update_database(2.0, db_path=self.path, logger=LogRecorder())
        self.assertEqual(self.columns(), before)
        self.assertEqual(db.read_db(self.path, 1)['title'], 'a')


if __name__ == '__main__':
    unittest.main()
