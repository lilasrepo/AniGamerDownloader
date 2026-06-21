#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# One-off migration: bring already-downloaded files + the DB into line with the
# new zero-pad scheme, and re-point stale DB paths at the files on disk.
#
# Two problems this fixes:
#   (2) An earlier bulk-rename changed files on disk (e.g. [S2E1] -> [S02E01])
#       but did NOT update anime.local_file_path, so the Database page flags those
#       rows as missing even though the file is there.
#   (3) A/B split episodes ("1A"/"1B") were never zero-padded (regex required a
#       pure number), so they stayed [S01E1A] instead of [S01E01A].
#
# Strategy per DB row (grouped by folder = one season): compute the DESIRED name
# by re-padding the existing filename's [S..E..] token (season>=2 digits, episode
# numeric-prefix padded to the season's width, A/B suffix preserved). Then locate
# the file actually on disk (old name, desired name, or a token match), plan a
# rename if needed, and always re-point the DB path at the final file.
#
# Default = PREVIEW (no changes). Pass --apply to execute (backs up the DB first
# and logs every rename to scripts/migrate_filenames.log).

import io
import os
import re
import sys
import shutil
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import config as Config  # for cookie/db paths + zerofill default

DB_PATH = os.path.join(_ROOT, 'data', 'aniGamer.db')
LOG_PATH = os.path.join(_ROOT, 'scripts', 'migrate_filenames.log')
TOKEN_RE = re.compile(r'\[S(\d+)E([^\]]+)\]')
APPLY = '--apply' in sys.argv


def _zerofill_min():
    try:
        return max(1, int(Config.read_settings().get('zerofill', 2)))
    except Exception:
        return 2


def _ep_leading_int(ep):
    m = re.match(r'^(\d+)', str(ep))
    return int(m.group(1)) if m else None


def _pad_episode(ep, width):
    # Mirror engine.__get_filename: pad the leading integer, keep .frac / A,B suffix.
    m = re.match(r'^([+-]?)(\d+)(\.\d+)?([A-Za-z].*)?$', str(ep))
    if not m:
        return str(ep)  # non-numeric (movie/special/OVA...) -> untouched
    sign, num, frac, suffix = m.group(1), m.group(2), m.group(3) or '', m.group(4) or ''
    return sign + num.zfill(width) + frac + suffix


def _repad_token(base, season_width, ep_width):
    # Re-pad ONLY the [S<season>E<episode>] token inside an existing filename,
    # leaving bangumi name / resolution / extension exactly as they are.
    def repl(m):
        season, ep = m.group(1), m.group(2)
        season_new = str(int(season)).zfill(season_width)
        ep_new = _pad_episode(ep, ep_width)
        return '[S' + season_new + 'E' + ep_new + ']'
    return TOKEN_RE.sub(repl, base)


def _norm_token(base):
    # (season:int, ep_int:int|None, ep_suffix:str) for matching across pad widths.
    m = TOKEN_RE.search(base)
    if not m:
        return None
    season = int(m.group(1))
    ep = m.group(2)
    em = re.match(r'^([+-]?)(\d+)(\.\d+)?([A-Za-z].*)?$', ep)
    if em:
        return (season, int(em.group(2)), (em.group(4) or '').upper())
    return (season, None, ep.upper())


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute('SELECT sn, anime_name, episode, status, local_file_path, '
                      'file_size FROM anime WHERE local_file_path IS NOT NULL '
                      "AND local_file_path != ''").fetchall()

    zmin = _zerofill_min()
    season_width = max(zmin, 2) if zmin > 1 else 2  # season uses the same minimum

    # group rows by folder; per-folder episode width = max(zmin, max-leading-int digits)
    by_folder = {}
    for r in rows:
        folder = os.path.dirname(r['local_file_path'])
        by_folder.setdefault(folder, []).append(r)

    plans = []           # (sn, folder, cur_base, desired_base, action, db_path_new, size)
    missing = []         # (sn, old_base)
    for folder, frows in by_folder.items():
        max_int = max([_ep_leading_int(r['episode']) or 0 for r in frows] + [0])
        ep_width = max(zmin, len(str(max_int))) if (zmin > 1 and max_int > 0) else zmin
        disk = {}
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                nt = _norm_token(f)
                if nt is not None:
                    disk.setdefault(nt, f)
        for r in frows:
            old_path = r['local_file_path']
            old_base = os.path.basename(old_path)
            desired_base = _repad_token(old_base, season_width, ep_width)
            # locate the actual on-disk file for this row
            cur_base = None
            if os.path.exists(old_path):
                cur_base = old_base
            elif os.path.exists(os.path.join(folder, desired_base)):
                cur_base = desired_base
            else:
                nt = _norm_token(old_base)
                if nt is not None and nt in disk:
                    cur_base = disk[nt]
            if cur_base is None:
                missing.append((r['sn'], old_base))
                continue
            cur_path = os.path.join(folder, cur_base)
            desired_path = os.path.join(folder, desired_base)
            try:
                size = os.path.getsize(cur_path)
            except OSError:
                size = r['file_size']
            if cur_base == desired_base:
                action = 'OK' if old_path == desired_path else 'DBONLY'
            elif os.path.exists(desired_path):
                # target name already taken by a DIFFERENT file -> do not overwrite
                action = 'CONFLICT'
            else:
                action = 'RENAME'
            plans.append((r['sn'], folder, cur_base, desired_base, action,
                          desired_path, size))

    cats = {'RENAME': [], 'DBONLY': [], 'OK': [], 'CONFLICT': []}
    for p in plans:
        cats[p[4]].append(p)

    print('=' * 70)
    print('MODE:', 'APPLY' if APPLY else 'PREVIEW (no changes)')
    print('DB  :', DB_PATH)
    print('rows considered:', len(rows), '| folders:', len(by_folder))
    print('-' * 70)
    print('RENAME (file改名 + db路徑更新):', len(cats['RENAME']))
    print('DBONLY (檔案已是新名, 只更新 db 路徑):', len(cats['DBONLY']))
    print('OK     (已正確, 不動):', len(cats['OK']))
    print('CONFLICT (目標名已被別的檔佔用, 跳過):', len(cats['CONFLICT']))
    print('MISSING (找不到實體檔, 不動 db):', len(missing))
    print('-' * 70)

    def show(tag, items, n=5):
        if not items:
            return
        print('--- %s 範例 (最多 %d) ---' % (tag, n))
        for sn, folder, cur, des, act, dbp, size in items[:n]:
            short = os.path.basename(folder)
            print('  sn=%-6s [%s]' % (sn, short.encode('ascii', 'replace').decode()))
            print('      改名: %s' % cur.encode('ascii', 'replace').decode())
            print('      ->    %s' % des.encode('ascii', 'replace').decode())

    show('RENAME', cats['RENAME'])
    show('DBONLY', cats['DBONLY'])
    if cats['CONFLICT']:
        print('--- CONFLICT (需人工檢查) ---')
        for sn, folder, cur, des, act, dbp, size in cats['CONFLICT']:
            print('  sn=%s cur=%s des=%s' % (sn, cur.encode('ascii', 'replace').decode(),
                                             des.encode('ascii', 'replace').decode()))
    if missing:
        print('--- MISSING (找不到檔, 維持原狀供你檢查) ---')
        for sn, ob in missing[:10]:
            print('  sn=%s %s' % (sn, ob.encode('ascii', 'replace').decode()))
        if len(missing) > 10:
            print('  ... 共 %d 筆' % len(missing))
    print('=' * 70)

    if not APPLY:
        print('這是預覽。確認無誤後，加 --apply 實際執行 (會先備份 db)。')
        return

    # --- APPLY ---
    bak = DB_PATH + '.bak-migrate'
    shutil.copy2(DB_PATH, bak)
    print('DB 已備份 ->', bak)
    log = io.open(LOG_PATH, 'a', encoding='utf-8')
    renamed = dbupd = 0
    for sn, folder, cur, des, act, dbp, size in plans:
        if act in ('OK',):
            continue
        if act == 'CONFLICT':
            continue
        try:
            if act == 'RENAME':
                os.rename(os.path.join(folder, cur), dbp)
                log.write('RENAME\t%s\t%s\t%s\n' % (sn, cur, des))
                renamed += 1
            # only the path moved; file_size is stored in MB and is unchanged by a
            # rename, so do NOT overwrite it (os.path.getsize is BYTES -> corruption).
            db.execute('UPDATE anime SET local_file_path=? WHERE sn=?', (dbp, sn))
            dbupd += 1
        except Exception as e:
            log.write('ERROR\t%s\t%s\t%s\n' % (sn, cur, e))
            print('  ! sn=%s 失敗: %s' % (sn, e))
    db.commit()
    log.close()
    print('完成: 改名 %d 檔, 更新 db %d 筆。log -> %s' % (renamed, dbupd, LOG_PATH))


if __name__ == '__main__':
    main()
