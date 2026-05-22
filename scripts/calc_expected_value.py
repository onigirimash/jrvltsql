#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
期待値計算スクリプト（実力点数化 Step 10）

nl_o1 の単勝オッズと nl_race_prediction の win_prob から期待値を算出し
nl_race_prediction.odds / expected_value / is_recommended を更新する。

計算式:
  expected_value = win_prob × odds - 1
  is_recommended = expected_value > ev_threshold

オッズ取得:
  nl_o1.tanodds（単勝オッズ）を使用
  umaban > 0 かつ tanodds > 0 の行のみ対象
  同一馬に複数行存在する場合は makedate 降順で最新を採用（DISTINCT ON）

÷10 変換:
  --odds-factor 0.1 を指定すると tanodds を 10 分の 1 に変換する
  ※ 現行 nl_o1.tanodds は実オッズ（2.4 等）で格納されているため通常は不要

Usage:
    py -3.12-32 scripts/calc_expected_value.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --ev-threshold FLOAT       買い推奨閾値（デフォルト: 0.15）
    --odds-factor  FLOAT       オッズ変換係数（デフォルト: 1.0）
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────

_DEFAULT_EV_THRESHOLD = 2.0
_DEFAULT_ODDS_FACTOR  = 1.0

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の odds / expected_value / is_recommended / place odds をリセット
_SQL_RESET = """
UPDATE nl_race_prediction
SET odds           = NULL,
    expected_value = NULL,
    is_recommended = NULL,
    place_odds_min = NULL,
    place_odds_max = NULL,
    place_ev       = NULL
WHERE year = :year AND monthday = :monthday
"""

# nl_o1 と JOIN して一括更新。nl_o1.tanodds が欠損する日は nl_se.odds にフォールバック。
# DISTINCT ON で最新 makedate の行を採用（複数タイムスタンプ対応）
_SQL_UPDATE = """
UPDATE nl_race_prediction p
SET odds           = ROUND((src.odds_val::numeric * :factor::numeric), 1),
    expected_value = ROUND((p.win_prob::numeric * src.odds_val::numeric * :factor::numeric - 1), 4),
    ev_threshold   = :ev_threshold,
    is_recommended = (p.win_prob::numeric * src.odds_val::numeric * :factor::numeric - 1 > :ev_threshold::numeric)
FROM (
    SELECT
        se.jyocd,
        se.racenum,
        se.umaban,
        COALESCE(o.tanodds, se.odds) AS odds_val
    FROM nl_se se
    LEFT JOIN (
        SELECT DISTINCT ON (jyocd, racenum, umaban)
            jyocd, racenum, umaban, tanodds
        FROM nl_o1
        WHERE year     = :year
          AND monthday = :monthday
          AND umaban   > 0
          AND tanodds IS NOT NULL
          AND tanodds  > 0
        ORDER BY jyocd, racenum, umaban, makedate DESC
    ) o ON o.jyocd = se.jyocd AND o.racenum = se.racenum AND o.umaban = se.umaban
    WHERE se.year     = :year
      AND se.monthday = :monthday
      AND se.umaban   > 0
      AND COALESCE(o.tanodds, se.odds) > 0
) src
WHERE p.year     = :year
  AND p.monthday = :monthday
  AND p.jyocd    = src.jyocd
  AND p.racenum  = src.racenum
  AND p.umaban   = src.umaban
"""

# 複勝オッズ（fukuoddslow / fukuoddshigh）と複勝EVを更新
# DISTINCT ON で最新 makedate の行を採用
_SQL_UPDATE_PLACE = """
UPDATE nl_race_prediction p
SET place_odds_min = ROUND(src.fukuoddslow::numeric,  1),
    place_odds_max = ROUND(src.fukuoddshigh::numeric, 1),
    place_ev       = ROUND((p.place_prob::numeric * src.fukuoddslow::numeric - 1), 4)
FROM (
    SELECT DISTINCT ON (jyocd, racenum, umaban)
        jyocd, racenum, umaban, fukuoddslow, fukuoddshigh
    FROM nl_o1
    WHERE year     = :year
      AND monthday = :monthday
      AND umaban   > 0
      AND fukuoddslow  IS NOT NULL
      AND fukuoddslow  > 0
      AND fukuoddshigh IS NOT NULL
      AND fukuoddshigh > 0
    ORDER BY jyocd, racenum, umaban, makedate DESC
) src
WHERE p.year     = :year
  AND p.monthday = :monthday
  AND p.jyocd    = src.jyocd
  AND p.racenum  = src.racenum
  AND p.umaban   = src.umaban
"""

# 更新後サマリ
_SQL_SUMMARY = """
SELECT
    p.jyocd,
    COUNT(*)                                          AS total,
    COUNT(p.expected_value)                           AS has_ev,
    COUNT(*) FILTER (WHERE p.is_recommended)          AS recommended,
    ROUND(AVG(p.expected_value)::numeric, 4)          AS avg_ev,
    ROUND(MAX(p.expected_value)::numeric, 4)          AS max_ev
FROM nl_race_prediction p
WHERE p.year = :year AND p.monthday = :monthday
GROUP BY p.jyocd
ORDER BY p.jyocd
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
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
    ev_threshold: float,
    odds_factor: float,
) -> dict:
    """1日分の期待値を計算して nl_race_prediction を更新する。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    # リセット
    conn.run(_SQL_RESET, year=year, monthday=monthday)

    # nl_o1 から単勝 odds を取得して期待値を更新
    conn.run(
        _SQL_UPDATE,
        year=year,
        monthday=monthday,
        factor=odds_factor,
        ev_threshold=ev_threshold,
    )

    # nl_o1 から複勝 odds を取得して複勝EVを更新
    conn.run(_SQL_UPDATE_PLACE, year=year, monthday=monthday)

    # サマリ集計
    summary_rows = conn.run(_SQL_SUMMARY, year=year, monthday=monthday)

    total_has_ev     = 0
    total_recommended = 0

    for jyocd, total, has_ev, recommended, avg_ev, max_ev in summary_rows:
        has_ev      = has_ev      or 0
        recommended = recommended or 0
        total_has_ev      += has_ev
        total_recommended += recommended
        if has_ev > 0:
            print(f"  {date_str} jyo={jyocd}: "
                  f"EV算出={has_ev}/{total}頭  "
                  f"推奨={recommended}頭  "
                  f"avg_ev={avg_ev:+.4f}  max_ev={max_ev:+.4f}")

    if not summary_rows:
        print(f"  {date_str}: nl_race_prediction にデータなし（スキップ）")

    return {'has_ev': total_has_ev, 'recommended': total_recommended}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='期待値計算 (nl_race_prediction.odds / expected_value / is_recommended UPDATE)',
    )
    parser.add_argument('--date',         metavar='YYYYMMDD', help='対象日（単日）')
    parser.add_argument('--date-from',    metavar='YYYYMMDD', help='対象期間の開始日')
    parser.add_argument('--date-to',      metavar='YYYYMMDD', help='対象期間の終了日')
    parser.add_argument('--ev-threshold', type=float, default=_DEFAULT_EV_THRESHOLD,
                        metavar='FLOAT',  help=f'買い推奨閾値（デフォルト: {_DEFAULT_EV_THRESHOLD}）')
    parser.add_argument('--odds-factor',  type=float, default=_DEFAULT_ODDS_FACTOR,
                        metavar='FLOAT',  help='オッズ変換係数（×10格納の場合は0.1を指定、デフォルト: 1.0）')
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
        total_has_ev     = 0
        total_recommended = 0

        for d in dates:
            stats = calc_one_day(conn, d,
                                 ev_threshold=args.ev_threshold,
                                 odds_factor=args.odds_factor)
            total_has_ev      += stats['has_ev']
            total_recommended += stats['recommended']

        print(f"\n完了: EV算出={total_has_ev}頭、"
              f"買い推奨（EV>{args.ev_threshold:.2f}）={total_recommended}頭。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
