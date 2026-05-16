#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
推定勝率計算スクリプト（実力点数化 Step 9）

nl_horse_index.adjusted_index を Softmax 変換し
レース別・馬別の推定勝率を nl_race_prediction へ保存する。

計算式:
  win_prob[i] = exp(adjusted_index[i] / T) / Σ exp(adjusted_index[j] / T)
  T = 温度パラメータ（デフォルト: 0.1）

除外条件:
  初出走馬（nl_horse_index に当該 distance_cat/surface で未登録）が
  3頭以上いるレースはスキップ

NULL 処理:
  nl_horse_index に登録済みだが adjusted_index = NULL の馬は 0 として処理
  登録自体がない馬（初出走馬）もカウント後に 0 として処理

Usage:
    py -3.12-32 scripts/calc_win_prob.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --temperature FLOAT        温度パラメータ T（デフォルト: 0.1）
    --max-debut INT            除外閾値: 初出走馬がこの頭数以上で除外（デフォルト: 3）
    --logic-version TEXT       ロジックバージョン文字列（デフォルト: 1.0）
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import math
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────

_DEFAULT_T       = 0.1
_DEFAULT_DEBUT   = 3
_DEFAULT_VERSION = '1.0'

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の出走馬と nl_horse_index を LEFT JOIN で一括取得
_SQL_PARTICIPANTS = """
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.racenum,
    se.umaban,
    TRIM(se.kettonum)  AS kettonum,
    hi.kettonum        AS hi_found,
    hi.adjusted_index  AS adjusted_index
FROM nl_se se
JOIN nl_ra ra
  ON  ra.year      = se.year
  AND ra.monthday  = se.monthday
  AND ra.jyocd     = se.jyocd
  AND ra.racenum   = se.racenum
LEFT JOIN nl_horse_index hi
  ON  hi.kettonum     = TRIM(se.kettonum)
  AND hi.distance_cat = CASE
        WHEN ra.kyori <= 1400 THEN 'S'
        WHEN ra.kyori <= 1800 THEN 'M'
        WHEN ra.kyori <= 2200 THEN 'I'
        ELSE 'L'
      END
  AND hi.surface      = CASE LEFT(ra.trackcd, 1)
        WHEN '1' THEN 'T'
        WHEN '2' THEN 'D'
        ELSE 'J'
      END
WHERE se.year     = :year
  AND se.monthday = :monthday
  AND se.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni >= 1
  AND se.kettonum IS NOT NULL
  AND se.kettonum <> ''
ORDER BY se.jyocd, se.racenum, se.umaban
"""

# 対象日の既存予測を削除してから INSERT（日単位のリセット）
_SQL_DELETE = """
DELETE FROM nl_race_prediction
WHERE year = :year AND monthday = :monthday
"""

_SQL_INSERT = """
INSERT INTO nl_race_prediction
    (year, monthday, jyocd, racenum, umaban, kettonum,
     adjusted_index, win_prob, t_parameter, logic_version, created_at)
VALUES
    (:year, :monthday, :jyocd, :racenum, :umaban, :kettonum,
     :adjusted_index, :win_prob, :t_parameter, :logic_version, NOW())
ON CONFLICT (year, monthday, jyocd, racenum, umaban)
DO UPDATE SET
    kettonum       = EXCLUDED.kettonum,
    adjusted_index = EXCLUDED.adjusted_index,
    win_prob       = EXCLUDED.win_prob,
    t_parameter    = EXCLUDED.t_parameter,
    logic_version  = EXCLUDED.logic_version,
    created_at     = EXCLUDED.created_at
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


def _softmax(values: list[float], T: float) -> list[float]:
    """数値安定版 Softmax（最大値を引いてからexp）。"""
    max_v = max(values)
    exps  = [math.exp((v - max_v) / T) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
    T: float,
    max_debut: int,
    logic_version: str,
) -> dict:
    """1日分の推定勝率を計算して nl_race_prediction へ保存する。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    rows = conn.run(_SQL_PARTICIPANTS, year=year, monthday=monthday)
    if not rows:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return {'races': 0, 'skipped': 0, 'horses': 0}

    # レース別に出走馬をグループ化
    # race_key → list of {'umaban', 'kettonum', 'hi_found', 'adjusted_index'}
    races: dict[tuple, list] = defaultdict(list)
    for (year_, monthday_, jyocd, racenum, umaban,
         kettonum, hi_found, adjusted_index) in rows:
        race_key = (str(jyocd), int(racenum))
        races[race_key].append({
            'umaban':         int(umaban),
            'kettonum':       str(kettonum),
            'is_debut':       hi_found is None,        # nl_horse_index 未登録
            'adjusted_index': adjusted_index,           # None の場合は 0 で処理
        })

    total_races  = 0
    skipped      = 0
    total_horses = 0

    for (jyocd, racenum), horses in sorted(races.items()):
        # 初出走馬カウント（nl_horse_index に該当エントリなし）
        debut_count = sum(1 for h in horses if h['is_debut'])
        if debut_count >= max_debut:
            skipped += 1
            continue

        if not horses:
            continue

        # adjusted_index: NULL（未登録 or NULL カラム）→ 0.0
        adj_values = [
            float(h['adjusted_index']) if h['adjusted_index'] is not None else 0.0
            for h in horses
        ]

        probs = _softmax(adj_values, T)

        for h, adj, prob in zip(horses, adj_values, probs):
            conn.run(
                _SQL_INSERT,
                year=year,
                monthday=monthday,
                jyocd=jyocd,
                racenum=racenum,
                umaban=h['umaban'],
                kettonum=h['kettonum'],
                adjusted_index=round(adj, 3) if h['adjusted_index'] is not None else None,
                win_prob=round(prob, 6),
                t_parameter=T,
                logic_version=logic_version,
            )
            total_horses += 1

        total_races += 1

    print(f"  {date_str}: {total_races} レース処理 "
          f"（除外={skipped} / 馬数={total_horses}）")
    return {'races': total_races, 'skipped': skipped, 'horses': total_horses}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='推定勝率計算 Softmax (nl_race_prediction INSERT/UPDATE)',
    )
    parser.add_argument('--date',          metavar='YYYYMMDD', help='対象日（単日）')
    parser.add_argument('--date-from',     metavar='YYYYMMDD', help='対象期間の開始日')
    parser.add_argument('--date-to',       metavar='YYYYMMDD', help='対象期間の終了日')
    parser.add_argument('--temperature',   type=float, default=_DEFAULT_T,
                        metavar='T',       help=f'温度パラメータ（デフォルト: {_DEFAULT_T}）')
    parser.add_argument('--max-debut',     type=int,   default=_DEFAULT_DEBUT,
                        metavar='N',       help=f'初出走馬除外閾値（デフォルト: {_DEFAULT_DEBUT}）')
    parser.add_argument('--logic-version', default=_DEFAULT_VERSION,
                        metavar='VER',     help=f'ロジックバージョン（デフォルト: {_DEFAULT_VERSION}）')
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
        total_races  = 0
        total_horses = 0
        total_skip   = 0

        for d in dates:
            stats = calc_one_day(
                conn, d,
                T=args.temperature,
                max_debut=args.max_debut,
                logic_version=args.logic_version,
            )
            total_races  += stats['races']
            total_horses += stats['horses']
            total_skip   += stats['skipped']

        print(f"\n完了: {total_races} レース・{total_horses} 頭の勝率を計算、"
              f"{total_skip} レースを除外しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
