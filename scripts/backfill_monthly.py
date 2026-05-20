#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2021〜2025年 月次バックフィルスクリプト

各月末時点の --as-of-date を使って以下を順番に実行する:
  1. calc_horse_index.py   --as-of-date YYYYMMDD
  2. calc_current_index.py --as-of-date YYYYMMDD
  3. calc_reliability.py
  4. calc_win_prob.py      --date-from YYYYMMDD --date-to YYYYMMDD
  5. calc_expected_value.py --date-from YYYYMMDD --date-to YYYYMMDD

月初〜月末のレースを対象に各指数を再計算する。
ルックアヘッドバイアス防止のため、calc_horse_index / calc_current_index は
その月末時点以前のデータのみを使う。

Usage:
    py -3.12-32 scripts/backfill_monthly.py [options]

    --from-year  N   開始年（デフォルト: 2021）
    --from-month N   開始月（デフォルト: 1）
    --to-year    N   終了年（デフォルト: 2025）
    --to-month   N   終了月（デフォルト: 12）
    --dry-run        コマンドを表示するだけで実行しない
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import calendar
import os
import subprocess
import sys
from datetime import date


def _month_end(year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _fmt(d: date) -> str:
    return d.strftime('%Y%m%d')


def _run(cmd: list[str], dry_run: bool) -> None:
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"  [ERROR] 終了コード {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='2021〜2025年 月次バックフィル',
    )
    parser.add_argument('--from-year',  type=int, default=2021, metavar='N')
    parser.add_argument('--from-month', type=int, default=1,    metavar='N')
    parser.add_argument('--to-year',    type=int, default=2025, metavar='N')
    parser.add_argument('--to-month',   type=int, default=12,   metavar='N')
    parser.add_argument('--dry-run',    action='store_true', help='コマンドを表示のみ')
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD',
                                                                os.environ.get('PGPASSWORD', '')))
    args = parser.parse_args()

    pg_args = [
        '--pg-host',     args.pg_host,
        '--pg-port',     str(args.pg_port),
        '--pg-database', args.pg_database,
        '--pg-user',     args.pg_user,
        '--pg-password', args.pg_password,
    ]

    py = ['py', '-3.12-32']

    year  = args.from_year
    month = args.from_month

    while (year, month) <= (args.to_year, args.to_month):
        end   = _month_end(year, month)
        start = _month_start(year, month)
        as_of = _fmt(end)
        date_from = _fmt(start)
        date_to   = _fmt(end)

        print(f"\n=== {year}/{month:02d}  ({date_from} 〜 {date_to}, as-of={as_of}) ===")

        # Step 5: 馬別実力指数（as-of-date 以前のデータのみ）
        _run(py + ['scripts/calc_horse_index.py',
                   '--as-of-date', as_of] + pg_args, args.dry_run)

        # Step 6: 時系列補正（as-of-date 以前のデータのみ）
        _run(py + ['scripts/calc_current_index.py',
                   '--as-of-date', as_of] + pg_args, args.dry_run)

        # Step 7: 信頼度（全馬のcurrent_indexから一括計算）
        _run(py + ['scripts/calc_reliability.py'] + pg_args, args.dry_run)

        # Step 8: 勝率（当月のレース対象）
        _run(py + ['scripts/calc_win_prob.py',
                   '--date-from', date_from,
                   '--date-to',   date_to] + pg_args, args.dry_run)

        # Step 9: 期待値（当月のレース対象）
        _run(py + ['scripts/calc_expected_value.py',
                   '--date-from', date_from,
                   '--date-to',   date_to] + pg_args, args.dry_run)

        # 次の月へ
        if month == 12:
            year  += 1
            month  = 1
        else:
            month += 1

    print("\n===== バックフィル完了 =====")


if __name__ == '__main__':
    main()
