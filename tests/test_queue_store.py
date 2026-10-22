#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.queue_store -- the persistent manual download queue.

Every test gets a fresh ``QueueStore`` (not the module-level ``store``) and
points ``core.config.data_dir`` at a scratch directory, so the real
``data/download_queue.json`` is never read or written.
"""

import json
import os
import threading
import unittest
from unittest import mock

from tests import LOG, make_tmpdir
from core import config
from core import queue_store
from core.queue_store import QueueStore


class QueueTestCase(unittest.TestCase):
    def setUp(self):
        LOG.clear()
        self.data_dir = os.path.join(make_tmpdir(self), 'data')
        p = mock.patch.object(config, 'data_dir', self.data_dir)
        p.start()
        self.addCleanup(p.stop)
        self.path = os.path.join(self.data_dir, 'download_queue.json')

    def write_file(self, payload, raw=False):
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(payload if raw else json.dumps(payload, ensure_ascii=False))

    def read_file(self):
        with open(self.path, 'r', encoding='utf-8') as f:
            return json.load(f)


class AddAndIdentity(QueueTestCase):
    def test_add_returns_counts_and_lists_oldest_first(self):
        q = QueueStore()
        self.assertEqual(q.add([{'kind': 'db', 'sn': 111, 'title': 'A'},
                                {'kind': 'manual', 'sn': 222, 'title': 'B'}]), (2, 0))
        self.assertEqual(q.add([{'kind': 'db', 'sn': 333, 'title': 'C'}]), (1, 0))
        self.assertEqual([i['id'] for i in q.list()], ['db:111', 'manual:222', 'db:333'])

    def test_readding_same_kind_sn_replaces(self):
        q = QueueStore()
        q.add([{'kind': 'manual', 'sn': 500, 'title': 'X', 'options': {'mode': 'all'}}])
        self.assertEqual(q.add([{'kind': 'manual', 'sn': 500, 'title': 'X',
                                 'options': {'mode': 'latest'}}]), (0, 1))
        items = q.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['options'], {'mode': 'latest'})

    def test_same_sn_different_kind_is_a_different_item(self):
        q = QueueStore()
        self.assertEqual(q.add([{'kind': 'db', 'sn': 7}, {'kind': 'manual', 'sn': 7}]), (2, 0))
        self.assertEqual(sorted(i['id'] for i in q.list()), ['db:7', 'manual:7'])

    def test_string_sn_is_stored_as_int(self):
        q = QueueStore()
        q.add([{'kind': 'db', 'sn': '12345', 'title': 't'}])
        item = q.list()[0]
        self.assertEqual(item['sn'], 12345)
        self.assertEqual(item['id'], 'db:12345')

    def test_title_and_options_preserved(self):
        q = QueueStore()
        q.add([{'kind': 'manual', 'sn': 9, 'title': '某動畫 [3]', 'options': {'resolution': '720'}}])
        item = q.list()[0]
        self.assertEqual(item['title'], '某動畫 [3]')
        self.assertEqual(item['options'], {'resolution': '720'})

    def test_unusable_entries_are_skipped(self):
        q = QueueStore()
        bad = [
            {'kind': 'bogus', 'sn': 1},     # unknown kind
            {'sn': 1},                      # no kind
            {'kind': 'db', 'sn': 'abc'},    # non-numeric sn
            {'kind': 'db'},                 # no sn
            {'kind': 'db', 'sn': 0},        # sn must be positive
            {'kind': 'db', 'sn': -5},
            'db:5',                         # not a dict
            None,
        ]
        self.assertEqual(q.add(bad), (0, 0))
        self.assertEqual(q.list(), [])

    def test_add_none_or_empty(self):
        q = QueueStore()
        self.assertEqual(q.add(None), (0, 0))
        self.assertEqual(q.add([]), (0, 0))

    def test_no_mutation_means_no_file(self):
        q = QueueStore()
        q.add([{'kind': 'bogus', 'sn': 1}])
        q.list()
        self.assertFalse(os.path.exists(self.path))

    def test_list_returns_copies(self):
        q = QueueStore()
        q.add([{'kind': 'db', 'sn': 1, 'title': 'orig'}])
        q.list()[0]['title'] = 'tampered'
        self.assertEqual(q.list()[0]['title'], 'orig')


class RemoveTakeClear(QueueTestCase):
    def setUp(self):
        super().setUp()
        self.q = QueueStore()
        self.q.add([{'kind': 'db', 'sn': n} for n in (1, 2, 3)])

    def test_remove_counts_only_existing(self):
        self.assertEqual(self.q.remove(['db:1', 'db:3', 'db:999']), 2)
        self.assertEqual([i['id'] for i in self.q.list()], ['db:2'])

    def test_remove_empty_or_none(self):
        self.assertEqual(self.q.remove([]), 0)
        self.assertEqual(self.q.remove(None), 0)
        self.assertEqual(len(self.q.list()), 3)

    def test_take_ids_returns_and_removes(self):
        taken = self.q.take(['db:2'])
        self.assertEqual([i['id'] for i in taken], ['db:2'])
        self.assertEqual([i['id'] for i in self.q.list()], ['db:1', 'db:3'])

    def test_take_all(self):
        for arg in (None, []):
            with self.subTest(arg=arg):
                self.q.add([{'kind': 'db', 'sn': n} for n in (1, 2, 3)])
                self.assertEqual(len(self.q.take(arg)), 3)
                self.assertEqual(self.q.list(), [])

    def test_take_is_persisted(self):
        self.q.take(['db:1'])
        self.assertEqual([i['id'] for i in self.read_file()], ['db:2', 'db:3'])

    def test_clear_returns_count(self):
        self.assertEqual(self.q.clear(), 3)
        self.assertEqual(self.q.list(), [])
        self.assertEqual(self.read_file(), [])
        self.assertEqual(self.q.clear(), 0)


class Persistence(QueueTestCase):
    def test_survives_restart(self):
        q1 = QueueStore()
        q1.add([{'kind': 'db', 'sn': 10, 'title': '甲'}, {'kind': 'manual', 'sn': 20, 'options': {'a': 1}}])
        q2 = QueueStore()  # a new process reading the same file
        self.assertEqual(q2.list(), q1.list())

    def test_file_is_a_json_list_written_utf8(self):
        QueueStore().add([{'kind': 'db', 'sn': 10, 'title': '甲'}])
        data = self.read_file()
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]['title'], '甲')

    def test_no_temp_file_left_behind(self):
        QueueStore().add([{'kind': 'db', 'sn': 10}])
        self.assertEqual(os.listdir(self.data_dir), ['download_queue.json'])

    def test_reads_never_touch_disk_after_first_load(self):
        q = QueueStore()
        q.add([{'kind': 'db', 'sn': 10}])
        self.write_file([{'id': 'db:99', 'kind': 'db', 'sn': 99}])
        self.assertEqual([i['id'] for i in q.list()], ['db:10'])

    def test_corrupt_file_gives_empty_queue_without_raising(self):
        self.write_file('{this is not json', raw=True)
        q = QueueStore()
        self.assertEqual(q.list(), [])
        self.assertEqual(len(LOG.with_status(1)), 1)

    def test_non_list_json_gives_empty_queue(self):
        self.write_file({'id': 'db:1', 'kind': 'db', 'sn': 1})
        self.assertEqual(QueueStore().list(), [])

    def test_invalid_rows_in_file_are_dropped(self):
        self.write_file([
            {'id': 'db:1', 'kind': 'db', 'sn': 1},
            {'id': 'x:2', 'kind': 'x', 'sn': 2},         # bad kind
            {'kind': 'db', 'sn': 3},                      # no id
            {'id': 'db:4', 'kind': 'db', 'sn': 'four'},   # bad sn
            'garbage',
        ])
        self.assertEqual([i['id'] for i in QueueStore().list()], ['db:1'])

    def test_unwritable_location_never_raises(self):
        # data_dir's parent is a FILE -> makedirs fails inside _save_locked
        blocker = os.path.join(make_tmpdir(self), 'blocker')
        with open(blocker, 'w') as f:
            f.write('x')
        with mock.patch.object(config, 'data_dir', os.path.join(blocker, 'data')):
            q = QueueStore()
            self.assertEqual(q.add([{'kind': 'db', 'sn': 1}]), (1, 0))
            self.assertEqual([i['id'] for i in q.list()], ['db:1'])  # still held in memory
        self.assertEqual(len(LOG.with_status(1)), 1)


class Concurrency(QueueTestCase):
    def test_parallel_adds_lose_nothing(self):
        q = QueueStore()
        threads, per = 8, 25

        def worker(t):
            for n in range(per):
                q.add([{'kind': 'db', 'sn': t * 1000 + n + 1}])

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(q.list()), threads * per)
        self.assertEqual(len(self.read_file()), threads * per)


class ModuleSurface(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(set(queue_store.KINDS), {'db', 'manual'})


if __name__ == '__main__':
    unittest.main()
