#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
パフォーマンス指数計算スクリプト（実力点数化 Step 4）

各種補正値（time_dev, futan_dev, disadv_dev, pace_dev, bias_dev）を集計し
馬場指数（track_index）による乗算補正を加えて perf_index を計算する。

計算式:
  perf_index = time_dev × (track_index / 100)
             + moisture_index
             + futan_dev + disadv_dev + pace_dev + bias_dev

  track_index    : nl_track_speed から (race_date, jyocd, surface) で検索
                   取得できない場合は 100（補正なし）を使用
  moisture_index : nl_track_speed から同じキーで取得
                   芝=(cushion_value-9.0)×0.12 / ダート=(dirt_moisture-5.9)×0.095
                   取得できない場合は 0（補正なし）を使用

  surface 変換: nl_ra.trackcd 先頭文字
    '1' → 'T'（芝）
    '2' → 'D'（ダート）
    その他 → 'J'（障害）

Usage:
    py -3.12-32 scripts/calc_performance_index.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# CTE で race ごとの馬場指数を解決し、一括 UPDATE
_SQL_UPDATE = """
WITH race_surface AS (
    SELECT DISTINCT
        year, monthday, jyocd, racenum,
        CASE LEFT(trackcd, 1)
            WHEN '1' THEN 'T'
            WHEN '2' THEN 'D'
            ELSE 'J'
        END AS surface
    FROM nl_ra
    WHERE year = :year AND monthday = :monthday
      AND jyocd BETWEEN '01' AND '10'
),
track_idx AS (
    SELECT
        rs.jyocd,
        rs.racenum,
        COALESCE(MAX(ts.track_index),    100) AS track_index,
        COALESCE(MAX(ts.moisture_index),   0) AS moisture_index
    FROM race_surface rs
    LEFT JOIN nl_track_speed ts
      ON ts.race_date = :race_date
     AND ts.jyocd    = rs.jyocd
     AND ts.surface  = rs.surface
    GROUP BY rs.jyocd, rs.racenum
)
UPDATE nl_performance p
SET perf_index = ROUND((
    COALESCE(p.time_dev,   0) * ti.track_index / 100.0
    + ti.moisture_index
    + COALESCE(p.futan_dev,  0)
    + COALESCE(p.disadv_dev, 0)
    + COALESCE(p.pace_dev,   0)
    + COALESCE(p.bias_dev,   0)
)::numeric, 4),
updated_at = NOW()
FROM track_idx ti
WHERE p.year     = :year
  AND p.monthday = :monthday
  AND p.jyocd BETWEEN '01' AND '10'
  AND p.jyocd        = ti.jyocd
  AND p.racenum::int = ti.racenum
  AND p.time_dev IS NOT NULL
"""

_SQL_SUMMARY = """
SELECT
    jyocd,
    COUNT(*)                                 AS cnt,
    ROUND(AVG(perf_index)::numeric, 4)       AS avg_perf,
    ROUND(MIN(perf_index)::numeric, 4)       AS min_perf,
    ROUND(MAX(perf_index)::numeric, 4)       AS max_perf
FROM nl_performance
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd BETWEEN '01' AND '10'
  AND perf_index IS NOT NULL
GROUP BY jyocd
ORDER BY jyocd
"""

_SQL_COUNT = """
SELECT COUNT(*) FROM nl_performance
WHERE year = :year AND monthday = :monthday
  AND jyocd BETWEEN '01' AND '10'
  AND perf_index IS NOT NULL
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


def _to_race_date(year: int, monthday: int) -> str:
    """nl_track_speed.race_date 形式（YYYYMMDD）へ変換する。"""
    return str(year) + str(monthday).zfill(4)


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
) -> dict:
    """1日分のパフォーマンス指数を計算して nl_performance を UPDATE する。"""
    year, monthday = _to_year_monthday(target)
    race_date = _to_race_date(year, monthday)
    date_str = target.strftime('%Y%m%d')

    conn.run(_SQL_UPDATE, year=year, monthday=monthday, race_date=race_date)

    count_row = conn.run(_SQL_COUNT, year=year, monthday=monthday)
    updated = int(count_row[0][0]) if count_row else 0

    if updated == 0:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return {'updated': 0}

    summary_rows = conn.run(_SQL_SUMMARY, year=year, monthday=monthday)
    for jyocd, cnt, avg_p, min_p, max_p in summary_rows:
        print(f"  jyo={jyocd}  {cnt}頭  avg={avg_p:+.4f}  min={min_p:+.4f}  max={max_p:+.4f}")

    return {'updated': updated}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='パフォーマンス指数計算 (nl_performance.perf_index UPDATE)',
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
        total_updated = 0

        for d in dates:
            stats = calc_one_day(conn, d)
            if stats['updated']:
                print(f"{d.strftime('%Y%m%d')}: {stats['updated']} 頭の perf_index を更新しました。")
            total_updated += stats['updated']

        print(f"\n完了: 合計 {total_updated} 頭の perf_index を計算しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
