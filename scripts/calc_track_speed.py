#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
馬場指数計算スクリプト（実力点数化 Step 1）

各開催日・競馬場・走路（芝/ダート/障害）ごとに馬場指数を計算し
nl_track_speed テーブルへ UPSERT する。

計算式:
  各レースの基準タイム差 = 過去5年同条件平均タイム（秒） / 当日レース平均タイム（秒）
  馬場指数 = 当日全レースの基準タイム差の平均 × 100

同条件 = 競馬場 × 距離 × 走路 × クラス（gradecd + jyokencd から導出）
走路コード: T=芝, D=ダート, J=障害
フォールバック: 走路別レース数が3未満の場合は直近前日値を継承

time カラムの形式: MMSS.T（例: 113.26 = 1分13.26秒 = 73.26秒）
  実秒数変換: FLOOR(time/100)*60 + MOD(time, 100)

Usage:
    py -3.12-32 scripts/calc_track_speed.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import pg8000.native

# 走路コード（trackcd 先頭1文字 → surface）
_SURF = {'1': 'T', '2': 'D', '5': 'J', '6': 'J'}

# フォールバック閾値（これ未満のレース数なら前日値継承）
_MIN_RACES = 3

# 過去参照年数
_LOOKBACK_YEARS = 5

# ──────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────

_SQL_CALC = """
WITH current_races AS (
    -- 対象日のレースごとの平均走破タイム（秒換算）
    SELECT
        ra.jyocd,
        ra.racenum,
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
        AVG(FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)) AS cur_sec
    FROM nl_ra ra
    JOIN nl_se se
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
    GROUP BY
        ra.jyocd, ra.racenum, ra.kyori, ra.trackcd,
        ra.gradecd, ra.jyokencd1, ra.jyokencd2,
        ra.jyokencd3, ra.jyokencd4, ra.jyokencd5
),
hist_race_avg AS (
    -- 過去N年の同条件レースごと平均タイム（秒換算）
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
        AVG(FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)) AS race_avg_sec
    FROM nl_ra ra
    JOIN nl_se se
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
      AND ra.year * 10000 + ra.monthday < :ref_date
      AND ra.year >= :ref_year
    GROUP BY
        ra.jyocd, ra.kyori, ra.trackcd,
        ra.gradecd, ra.jyokencd1, ra.jyokencd2,
        ra.jyokencd3, ra.jyokencd4, ra.jyokencd5,
        ra.year, ra.monthday, ra.racenum
),
hist_avg AS (
    -- 条件別の過去平均タイム（レース単位で平均してから集計）
    SELECT
        jyocd,
        kyori,
        surface,
        race_class,
        AVG(race_avg_sec) AS hist_sec
    FROM hist_race_avg
    GROUP BY jyocd, kyori, surface, race_class
),
ratios AS (
    -- レースごとの基準タイム差
    SELECT
        cr.jyocd,
        cr.surface,
        CASE
            WHEN ha.hist_sec > 0 AND cr.cur_sec > 0
            THEN ha.hist_sec / cr.cur_sec
            ELSE NULL
        END AS ratio
    FROM current_races cr
    LEFT JOIN hist_avg ha
      ON  cr.jyocd      = ha.jyocd
      AND cr.kyori      = ha.kyori
      AND cr.surface    = ha.surface
      AND cr.race_class = ha.race_class
)
SELECT
    jyocd,
    surface,
    ROUND((AVG(ratio) * 100)::numeric, 2) AS track_index,
    COUNT(*)                               AS race_count
FROM ratios
WHERE ratio IS NOT NULL
GROUP BY jyocd, surface
ORDER BY jyocd, surface
"""

_SQL_PREV = """
SELECT track_index
FROM   nl_track_speed
WHERE  jyocd     = :jyocd
  AND  surface   = :surface
  AND  race_date < :race_date
ORDER BY race_date DESC
LIMIT 1
"""

_SQL_UPSERT = """
INSERT INTO nl_track_speed (race_date, jyocd, surface, track_index, race_count, fallback)
VALUES (:race_date, :jyocd, :surface, :track_index, :race_count, :fallback)
ON CONFLICT (race_date, jyocd, surface) DO UPDATE SET
    track_index = EXCLUDED.track_index,
    race_count  = EXCLUDED.race_count,
    fallback    = EXCLUDED.fallback,
    updated_at  = NOW()
"""

# ──────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return datetime.strptime(s, '%Y%m%d').date()


def _date_range(from_date: date, to_date: date):
    d = from_date
    while d <= to_date:
        yield d
        d += timedelta(days=1)


def _to_year_monthday(d: date) -> tuple[int, int]:
    """date → (year, monthday) where monthday = MMDD as int (e.g., May 3 → 503)"""
    return d.year, d.month * 100 + d.day


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


def _to_float(v) -> float | None:
    if v is None:
        return None
    return float(v)


# ──────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────

def calc_one_day(conn: pg8000.native.Connection, target: date) -> list[dict]:
    """
    1日分の馬場指数を計算して upsert する。
    Returns: upsert したレコードのリスト
    """
    year, monthday = _to_year_monthday(target)
    race_date_str  = target.strftime('%Y%m%d')
    ref_date       = year * 10000 + monthday          # YYYYMMDD int (当日除外用)
    ref_year       = year - _LOOKBACK_YEARS

    rows = conn.run(
        _SQL_CALC,
        year=year, monthday=monthday,
        ref_date=ref_date, ref_year=ref_year,
    )

    if not rows:
        print(f"  {race_date_str}: 対象レースなし（スキップ）")
        return []

    results = []
    for row in rows:
        jyocd      = row[0]
        surface    = row[1]
        track_idx  = _to_float(row[2])
        race_count = int(row[3])

        fallback = False
        if race_count < _MIN_RACES:
            prev_rows = conn.run(
                _SQL_PREV,
                jyocd=jyocd, surface=surface, race_date=race_date_str,
            )
            if prev_rows and prev_rows[0][0] is not None:
                track_idx = _to_float(prev_rows[0][0])
                fallback  = True

        rec = dict(
            race_date=race_date_str,
            jyocd=jyocd,
            surface=surface,
            track_index=track_idx,
            race_count=race_count,
            fallback=fallback,
        )
        conn.run(_SQL_UPSERT, **rec)
        results.append(rec)

    return results


def _summarize(results: list[dict]) -> str:
    parts = []
    for r in sorted(results, key=lambda x: (x['jyocd'], x['surface'])):
        fb = ' [FB]' if r['fallback'] else ''
        idx = f"{r['track_index']:.2f}" if r['track_index'] is not None else 'N/A'
        parts.append(
            f"  jyo={r['jyocd']} surf={r['surface']} idx={idx} "
            f"races={r['race_count']}{fb}"
        )
    return '\n'.join(parts)


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='馬場指数計算 (nl_track_speed UPSERT)',
    )
    parser.add_argument(
        '--date',
        metavar='YYYYMMDD',
        help='対象日（単日）',
    )
    parser.add_argument(
        '--date-from',
        metavar='YYYYMMDD',
        help='対象期間の開始日（--date-to と併用）',
    )
    parser.add_argument(
        '--date-to',
        metavar='YYYYMMDD',
        help='対象期間の終了日（デフォルト: 本日）',
    )
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD', ''))

    args = parser.parse_args()

    # 対象日付リストを決定
    today = date.today()
    if args.date:
        dates = [_parse_date(args.date)]
    elif args.date_from:
        from_d = _parse_date(args.date_from)
        to_d   = _parse_date(args.date_to) if args.date_to else today
        dates  = list(_date_range(from_d, to_d))
    else:
        # デフォルト: 先週土日
        last_sunday   = today - timedelta(days=today.weekday() + 1)
        last_saturday = last_sunday - timedelta(days=1)
        dates = [last_saturday, last_sunday]

    conn = _connect(args)
    try:
        total_upserted = 0
        for d in dates:
            results = calc_one_day(conn, d)
            if results:
                print(f"{d.strftime('%Y%m%d')} ({len(results)} records):")
                print(_summarize(results))
                total_upserted += len(results)
        print(f"\n完了: 合計 {total_upserted} レコードを upsert しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
