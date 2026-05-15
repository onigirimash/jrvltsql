#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
タイム偏差計算スクリプト（実力点数化 Step 2）

各レースの走破タイムと過去5年同条件平均タイムの差をハロン換算し
nl_performance テーブルへ UPSERT する。

計算式:
  タイム偏差（秒/ハロン）= (基準タイム - 走破タイム) ÷ ハロン数
  ・プラス = 平均より速い（良パフォーマンス）
  ・マイナス = 平均より遅い（低パフォーマンス）

同条件 = 競馬場 × 距離 × 走路 × クラス
  ※馬場状態はStep1（nl_track_speed）で別補正するため含めない

補完: サンプル10件未満の場合、隣接距離（±200m）のハロン単価
     （base_sec / furlongs）で対象距離へスケールして代替基準タイムを算出。

time カラムの形式: MMSS.T（例: 113.26 = 1分13秒26 = 73.26秒）
  実秒数変換: FLOOR(time/100)*60 + MOD(time, 100)

Usage:
    py -3.12-32 scripts/calc_time_deviation.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import pg8000.native

_LOOKBACK_YEARS = 5
_MIN_SAMPLES = 10

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の全完走馬（jyocd 01-10、走破タイムあり）
_SQL_TARGET = """
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.kaiji,
    se.nichiji,
    se.racenum,
    se.umaban,
    FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)  AS run_sec,
    ra.kyori,
    CASE
        WHEN LEFT(ra.trackcd, 1) = '1' THEN 'T'
        WHEN LEFT(ra.trackcd, 1) = '2' THEN 'D'
        ELSE 'J'
    END AS surface,
    CASE
        WHEN ra.gradecd IN ('A', 'B', 'C') THEN ra.gradecd
        WHEN ra.gradecd = 'E'              THEN 'E'
        ELSE COALESCE(
            NULLIF(ra.jyokencd1, '000'),
            NULLIF(ra.jyokencd2, '000'),
            NULLIF(ra.jyokencd3, '000'),
            NULLIF(ra.jyokencd4, '000'),
            NULLIF(ra.jyokencd5, '000'),
            'OP'
        )
    END AS race_class
FROM nl_se se
JOIN nl_ra ra
  ON  ra.year     = se.year
  AND ra.monthday = se.monthday
  AND ra.jyocd    = se.jyocd
  AND ra.kaiji    = se.kaiji
  AND ra.nichiji  = se.nichiji
  AND ra.racenum  = se.racenum
WHERE ra.year     = :year
  AND ra.monthday = :monthday
  AND ra.jyocd BETWEEN '01' AND '10'
  AND LEFT(ra.trackcd, 1) IN ('1', '2', '5', '6')
  AND se.kakuteijyuni >= 1
  AND se.time > 0
ORDER BY se.jyocd, se.racenum, se.umaban
"""

# 過去N年の条件別平均走破タイム（全中央場を一括取得してキャッシュ化）
# CTE で CASE 式を一度だけ定義し GROUP BY はエイリアスで参照
_SQL_BASE_ALL = """
WITH hist AS (
    SELECT
        ra.jyocd,
        ra.kyori,
        CASE
            WHEN LEFT(ra.trackcd, 1) = '1' THEN 'T'
            WHEN LEFT(ra.trackcd, 1) = '2' THEN 'D'
            ELSE 'J'
        END AS surface,
        CASE
            WHEN ra.gradecd IN ('A', 'B', 'C') THEN ra.gradecd
            WHEN ra.gradecd = 'E'              THEN 'E'
            ELSE COALESCE(
                NULLIF(ra.jyokencd1, '000'),
                NULLIF(ra.jyokencd2, '000'),
                NULLIF(ra.jyokencd3, '000'),
                NULLIF(ra.jyokencd4, '000'),
                NULLIF(ra.jyokencd5, '000'),
                'OP'
            )
        END AS race_class,
        FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)  AS run_sec
    FROM nl_se se
    JOIN nl_ra ra
      ON  ra.year     = se.year
      AND ra.monthday = se.monthday
      AND ra.jyocd    = se.jyocd
      AND ra.kaiji    = se.kaiji
      AND ra.nichiji  = se.nichiji
      AND ra.racenum  = se.racenum
    WHERE ra.jyocd BETWEEN '01' AND '10'
      AND LEFT(ra.trackcd, 1) IN ('1', '2', '5', '6')
      AND se.kakuteijyuni >= 1
      AND se.time > 0
      AND ra.year >= :ref_year
      AND ra.year * 10000 + ra.monthday < :today_int
)
SELECT
    jyocd,
    kyori,
    surface,
    race_class,
    AVG(run_sec)  AS base_sec,
    COUNT(*)      AS sample_cnt
FROM hist
GROUP BY jyocd, kyori, surface, race_class
"""

_SQL_UPSERT = """
INSERT INTO nl_performance (
    year, monthday, jyocd, kaiji, nichiji, racenum, umaban,
    kyori, surface, race_class,
    run_sec, base_sec, furlongs, time_dev,
    sample_cnt, interp
)
VALUES (
    :year, :monthday, :jyocd, :kaiji, :nichiji, :racenum, :umaban,
    :kyori, :surface, :race_class,
    :run_sec, :base_sec, :furlongs, :time_dev,
    :sample_cnt, :interp
)
ON CONFLICT (year, monthday, jyocd, kaiji, nichiji, racenum, umaban) DO UPDATE SET
    kyori      = EXCLUDED.kyori,
    surface    = EXCLUDED.surface,
    race_class = EXCLUDED.race_class,
    run_sec    = EXCLUDED.run_sec,
    base_sec   = EXCLUDED.base_sec,
    furlongs   = EXCLUDED.furlongs,
    time_dev   = EXCLUDED.time_dev,
    sample_cnt = EXCLUDED.sample_cnt,
    interp     = EXCLUDED.interp,
    updated_at = NOW()
"""

# ──────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return datetime.strptime(s, '%Y%m%d').date()


def _date_range(from_date: date, to_date: date):
    d = from_date
    while d <= to_date:
        yield d
        d += timedelta(days=1)


def _to_year_monthday(d: date) -> tuple[int, int]:
    return d.year, d.month * 100 + d.day


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


# ──────────────────────────────────────────────────────
# 基準タイムキャッシュ
# ──────────────────────────────────────────────────────

def _build_base_cache(
    conn: pg8000.native.Connection,
    ref_year: int,
    today_int: int,
) -> dict:
    """
    過去 _LOOKBACK_YEARS 年の条件別平均走破タイムを一括取得する。
    Key  : (jyocd, kyori, surface, race_class)
    Value: {'base_sec': float, 'sample_cnt': int}
    """
    rows = conn.run(_SQL_BASE_ALL, ref_year=ref_year, today_int=today_int)
    cache: dict = {}
    for row in rows:
        key = (row[0], int(row[1]), row[2], row[3])
        cache[key] = {
            'base_sec':   float(row[4]),
            'sample_cnt': int(row[5]),
        }
    return cache


def _lookup_base(
    cache: dict,
    jyocd: str,
    kyori: int,
    surface: str,
    race_class: str,
) -> tuple[float, int, bool] | None:
    """
    基準タイムを検索し (base_sec, sample_cnt, interp) を返す。
    サンプル不足（< _MIN_SAMPLES）の場合、距離±200m のハロン単価で補完。
    基準タイムが全く取れなければ None を返す。
    """
    key = (jyocd, kyori, surface, race_class)
    entry = cache.get(key)

    if entry and entry['sample_cnt'] >= _MIN_SAMPLES:
        return entry['base_sec'], entry['sample_cnt'], False

    # 不足時: ±200m の中でよりサンプルが多い方を選ぶ
    best_entry = entry        # None か低サンプルエントリ
    best_kyori = kyori

    for neighbor_kyori in [kyori - 200, kyori + 200]:
        if neighbor_kyori <= 0:
            continue
        nkey = (jyocd, neighbor_kyori, surface, race_class)
        nentry = cache.get(nkey)
        if nentry is None:
            continue
        if best_entry is None or nentry['sample_cnt'] > best_entry['sample_cnt']:
            best_entry = nentry
            best_kyori = neighbor_kyori

    if best_entry is None:
        return None

    if best_kyori == kyori:
        # 隣接より元の条件のサンプルが多い（またはどちらもなし）→ 元のまま使用
        return best_entry['base_sec'], best_entry['sample_cnt'], False

    # 隣接距離のハロン単価（秒/ハロン）で対象距離へスケール
    neighbor_furlongs = best_kyori / 200.0
    target_furlongs   = kyori / 200.0
    scaled_base_sec   = (best_entry['base_sec'] / neighbor_furlongs) * target_furlongs
    return scaled_base_sec, best_entry['sample_cnt'], True


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
    base_cache: dict,
) -> list[dict]:
    """
    1日分のタイム偏差を計算して UPSERT する。
    """
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    horses = conn.run(_SQL_TARGET, year=year, monthday=monthday)
    if not horses:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return []

    results = []
    skipped = 0

    for h in horses:
        (year_h, monthday_h, jyocd, kaiji, nichiji,
         racenum, umaban, run_sec_raw, kyori_raw,
         surface, race_class) = h

        run_sec  = float(run_sec_raw)
        kyori    = int(kyori_raw)
        furlongs = kyori / 200.0

        lookup = _lookup_base(base_cache, jyocd, kyori, surface, race_class)
        if lookup is None:
            skipped += 1
            continue

        base_sec, sample_cnt, interp = lookup
        time_dev = (base_sec - run_sec) / furlongs

        rec = dict(
            year=int(year_h),     monthday=int(monthday_h),
            jyocd=jyocd,          kaiji=kaiji,
            nichiji=nichiji,      racenum=racenum,
            umaban=umaban,
            kyori=kyori,          surface=surface,
            race_class=race_class,
            run_sec=round(run_sec,  3),
            base_sec=round(base_sec, 3),
            furlongs=round(furlongs, 2),
            time_dev=round(time_dev, 3),
            sample_cnt=sample_cnt,
            interp=interp,
        )
        conn.run(_SQL_UPSERT, **rec)
        results.append(rec)

    if skipped:
        print(f"  {date_str}: 基準タイム参照不可 {skipped} 件スキップ")

    return results


def _summarize(results: list[dict]) -> str:
    by_jyo: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if r['time_dev'] is not None:
            by_jyo[r['jyocd']].append(r['time_dev'])

    parts = []
    for jyocd in sorted(by_jyo):
        devs = by_jyo[jyocd]
        avg  = sum(devs) / len(devs)
        interp_n = sum(1 for r in results
                       if r['jyocd'] == jyocd and r['interp'])
        interp_s = f" (補完{interp_n}件)" if interp_n else ""
        parts.append(
            f"  jyo={jyocd}  n={len(devs)}  avg_dev={avg:+.3f}{interp_s}"
        )
    return '\n'.join(parts)


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='タイム偏差計算 (nl_performance UPSERT)',
    )
    parser.add_argument('--date',      metavar='YYYYMMDD', help='対象日（単日）')
    parser.add_argument('--date-from', metavar='YYYYMMDD', help='対象期間の開始日')
    parser.add_argument('--date-to',   metavar='YYYYMMDD', help='対象期間の終了日')
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD', ''))
    args = parser.parse_args()

    today = date.today()
    if args.date:
        dates = [_parse_date(args.date)]
    elif args.date_from:
        from_d = _parse_date(args.date_from)
        to_d   = _parse_date(args.date_to) if args.date_to else today
        dates  = list(_date_range(from_d, to_d))
    else:
        last_sunday   = today - timedelta(days=today.weekday() + 1)
        last_saturday = last_sunday - timedelta(days=1)
        dates = [last_saturday, last_sunday]

    conn = _connect(args)
    try:
        # 基準タイムキャッシュは対象期間の最小日をカットオフとして一括構築
        cutoff     = min(dates)
        cutoff_int = cutoff.year * 10000 + cutoff.month * 100 + cutoff.day
        ref_year   = cutoff.year - _LOOKBACK_YEARS

        print("基準タイムキャッシュを構築中（過去5年・全中央場）...")
        base_cache = _build_base_cache(conn, ref_year, cutoff_int)
        print(f"  {len(base_cache)} 条件ロード完了")

        total_upserted = 0
        for d in dates:
            results = calc_one_day(conn, d, base_cache)
            if results:
                print(f"{d.strftime('%Y%m%d')} ({len(results)} 馬):")
                print(_summarize(results))
                total_upserted += len(results)

        print(f"\n完了: 合計 {total_upserted} レコードを upsert しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
