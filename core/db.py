#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQLite DB layer for the anime inventory.

Moved verbatim from ``src/aniGamerPlus.py`` (read_db_all / read_db / insert_db /
update_db / reset_db_status + the db_locker semaphore + init_db) and from
``src/Config.py`` (``__update_database`` -> :func:`update_database`).

Design notes:
- ``db_path`` is injected per call instead of read from a module global.
- ``db_locker`` is ONE module-level Semaphore(1) shared by every function, so
  concurrent workers serialize and never hit "database is locked". Do NOT
  create per-call locks.
- ``update_db`` keeps reading the AnimeDownloader instance interface
  (get_sn/get_title/get_bangumi_name/get_episode/video_size/
  upload_succeed_flag/video_resolution/local_video_path) unchanged.
- The IntegrityError message uses an injected ``logger`` (defaults to the
  console+file logger from :mod:`core.logging`).
"""

import os
import sqlite3
import threading

from core.logging import err_print as _default_err_print

# DB schema version the migration brings databases up to. Kept here (not
# imported from core.config) to avoid a config<->db import cycle; mirrors
# Config.latest_database_version.
latest_database_version = 2.0

# ONE lock for the whole DB module: all helpers serialize through it. try/finally
# in each helper guarantees the lock is released even on sqlite exceptions
# (e.g. "database is locked"), otherwise every later db op would deadlock.
db_locker = threading.Semaphore(1)


def read_db_all(db_path):
    db_locker.acquire()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("select * FROM anime")
        values = cursor.fetchall()
        anime_db = [0] * len(values)
        for i in range(len(values)):
            anime_db[i] = {'sn': values[i][0],
                        'title': values[i][1],
                        'anime_name': values[i][2],
                        'episode': values[i][3],
                        'status': values[i][4],
                        'remote_status': values[i][5],
                        'resolution': values[i][6],
                        'file_size': values[i][7],
                        'local_file_path': values[i][8]}
        cursor.close()
        return anime_db
    finally:
        conn.close()
        db_locker.release()


def read_db(db_path, sn):
    db_locker.acquire()
    # take sn(int), read that sn's record, return a dict. When no record is found fetchall()[0] raises IndexError (the caller uses this to determine "not in db")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("select * FROM anime WHERE sn=:sn", {'sn': sn})
        values = cursor.fetchall()[0]  # not found -> IndexError propagates out (finally still releases the lock)
        anime_db = {'sn': values[0],
                    'title': values[1],
                    'anime_name': values[2],
                    'episode': values[3],
                    'status': values[4],
                    'remote_status': values[5],
                    'resolution': values[6],
                    'file_size': values[7],
                    'local_file_path': values[8]}
        cursor.close()
        return anime_db
    finally:
        conn.close()
        db_locker.release()


def insert_db(db_path, anime, logger=None):
    logger = logger or _default_err_print
    db_locker.acquire()
    # insert a new record into the database
    conn = sqlite3.connect(db_path)
    try:
        anime_dict = {'sn': str(anime.get_sn()),
                      'title': anime.get_title(),
                      'anime_name': anime.get_bangumi_name(),
                      'episode': anime.get_episode()}
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO anime (sn, title, anime_name, episode) VALUES (:sn, :title, :anime_name, :episode)",
                           anime_dict)
        except sqlite3.IntegrityError as e:
            logger(anime_dict['sn'], 'ＤＢ錯誤', 'title=' + anime_dict['title'] + ' 數據已存在！' + str(e), status=1)
        conn.commit()
        cursor.close()
    finally:
        conn.close()
        db_locker.release()


def update_db(db_path, anime):
    db_locker.acquire()
    # update the database status, resolution, file_size fields. try/finally ensures db_locker is still released on a sqlite exception (otherwise it deadlocks the whole db)
    conn = sqlite3.connect(db_path)
    try:
        anime_dict = {}
        if anime.video_size > 5:
            anime_dict['status'] = 1
        else:
            # download failed
            anime_dict['status'] = 0

        if anime.upload_succeed_flag:
            anime_dict['remote_status'] = 1
        else:
            anime_dict['remote_status'] = 0

        anime_dict['sn'] = anime.get_sn()
        anime_dict['title'] = anime.get_title()
        anime_dict['anime_name'] = anime.get_bangumi_name()
        anime_dict['episode'] = anime.get_episode()
        anime_dict['file_size'] = anime.video_size
        anime_dict['resolution'] = anime.video_resolution
        anime_dict['local_file_path'] = anime.local_video_path

        cursor = conn.cursor()
        cursor.execute(
            "UPDATE anime SET status=:status,"
            "remote_status=:remote_status,"
            "resolution=:resolution,"
            "file_size=:file_size,"
            "local_file_path=:local_file_path WHERE sn=:sn",
            anime_dict)
        conn.commit()
        cursor.close()
    finally:
        conn.close()
        db_locker.release()


def reset_db_status(db_path, sn):
    # Web dashboard: reset a given sn's download status (status/remote_status -> 0) so it can be re-downloaded.
    # Does not delete the file on disk. If the sn is in sn_list, the next automatic scan will re-fetch it.
    db_locker.acquire()
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE anime SET status=0, remote_status=0 WHERE sn=:sn", {'sn': int(sn)})
        conn.commit()
        cursor.close()
    finally:
        conn.close()
        db_locker.release()


def init_db(db_path):
    # initialize the sqlite3 database (idempotent)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS anime ('
                   'sn INTEGER PRIMARY KEY NOT NULL,'
                   'title VARCHAR(100) NOT NULL,'
                   'anime_name VARCHAR(100) NOT NULL, '
                   'episode VARCHAR(10) NOT NULL,'
                   'status TINYINT DEFAULT 0,'
                   'remote_status INTEGER DEFAULT 0,'
                   'resolution INTEGER DEFAULT 0,'
                   'file_size INTEGER DEFAULT 0,'
                   'local_file_path VARCHAR(500),'
                   "[CreatedTime] TimeStamp NOT NULL DEFAULT (datetime('now','localtime')))")
    conn.commit()
    conn.close()


def update_database(old_version, db_path=None, logger=None):
    # Schema migration (was Config.__update_database). db_path is injected; when
    # omitted it falls back to data/aniGamer.db under the project root so the
    # current src/Config shim and read_settings() call site keep working.
    logger = logger or _default_err_print
    if db_path is None:
        root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        db_path = os.path.join(root, 'data', 'aniGamer.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    def creat_table():
        cursor.execute('CREATE TABLE IF NOT EXISTS anime ('
                       'sn INTEGER PRIMARY KEY NOT NULL,'
                       'title VARCHAR(100) NOT NULL,'
                       'anime_name VARCHAR(100) NOT NULL, '
                       'episode VARCHAR(10) NOT NULL,'
                       'status TINYINT DEFAULT 0,'
                       'remote_status INTEGER DEFAULT 0,'
                       'resolution INTEGER DEFAULT 0,'
                       'file_size INTEGER DEFAULT 0,'
                       'local_file_path VARCHAR(500),'
                       "[CreatedTime] TimeStamp NOT NULL DEFAULT (datetime('now','localtime')))")

    try:
        cursor.execute('SELECT COUNT(*) FROM anime')
    except sqlite3.OperationalError as e:
        if 'no such table' in str(e):
            # if the table does not exist, create it
            creat_table()

    try:
        cursor.execute('SELECT COUNT(local_file_path) FROM anime')
    except sqlite3.OperationalError as e:
        if 'no such column' in str(e):
            # earlier databases have no local_file_path, add it for compatibility
            cursor.execute('ALTER TABLE anime ADD local_file_path VARCHAR(500)')

    try:
        cursor.execute('SELECT COUNT(sn) FROM anime')
    except sqlite3.OperationalError as e:
        if 'no such column' in str(e):
            # database v2.0 renames the ns column to sn
            cursor.execute('ALTER TABLE anime RENAME TO animeOld')
            creat_table()
            cursor.execute("INSERT INTO "
                           "anime (sn,title,anime_name,episode,status,remote_status,resolution,file_size,local_file_path,[CreatedTime]) "
                           "SELECT "
                           "ns,title,anime_name,episode,status,remote_status,resolution,file_size,local_file_path,[CreatedTime] "
                           "FROM animeOld")
            cursor.execute('DROP TABLE animeOld')

    cursor.close()
    conn.commit()
    conn.close()
    msg = '資料庫從 v' + str(old_version) + ' 升級到 v' + str(latest_database_version) + ' 內部資料不會遺失'
    logger(0, msg, status=2, no_sn=True)
