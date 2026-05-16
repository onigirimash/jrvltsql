#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
斤量補正計算スクリプト（実力点数化 Step 3①）

各出走馬の実際の斤量と基準斤量の差を秒/ハロン換算し
nl_performance.futan_dev へ UPDATE する。

計算式:
  futan_dev（秒/ハロン）= (基準斤量 - 実際の斤量) × 距離別係数 ÷ ハロン数
  ・プラス  = 基準より軽い斤量（有利）
  ・マイナス = 基準より重い斤量（不利）
  ・0       = ハンデ/別定戦（斤量は能力調整済みのため補正なし）
  ・NULL    = 基準斤量が算出不可（牡馬・騸馬が出走しないレース等）

基準斤量:
  牡馬・騸馬(sexcd='1','3') : レース内の牡馬・騸馬の最大斤量
  牝馬      (sexcd='2')     : 牡馬・騸馬の最大斤量 - 2kg

距離別係数（秒/kg）:
  ≤1400m : 0.18
  1500〜1800m : 0.20
  1900〜2200m : 0.22
  ≥2300m : 0.25

斤量種別（nl_ra.jyuryocd）:
  '1'=定量, '2'=馬齢 → 通常計算
  '3'=ハンデ, '4'=別定 → futan_dev = 0.0

Usage:
    py -3.12-32 scripts/calc_futan_correction.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# 距離別係数
# ──────────────────────────────────────────────────────

def _get_coeff(kyori: int) -> float:
    if kyori <= 1400:
        return 0.18
    elif kyori <= 1800:
        return 0.20
    elif kyori <= 2200:
        return 0.22
    else:
        return 0.25


# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の全完走馬（futan > 0）、レース内の牡馬・騸馬最大斤量をウィンドウ関数で取得
_SQL_TARGET = """
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.kaiji,
    se.nichiji,
    se.racenum,
    se.umaban,
    se.futan,
    se.sexcd,
    ra.jyuryocd,
    ra.kyori,
    MAX(se.futan) FILTER (WHERE se.sexcd IN ('1', '3'))
        OVER (
            PARTITION BY
                se.year, se.monthday, se.jyocd,
                se.kaiji, se.nichiji, se.racenum
        ) AS race_male_max
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
  AND se.kakuteijyuni >= 1
  AND se.futan > 0
ORDER BY se.jyocd, se.racenum, se.umaban
"""

_SQL_UPDATE = """
UPDATE nl_performance
SET
    futan_dev  = :futan_dev,
    updated_at = NOW()
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd    = :jyocd
  AND kaiji    = :kaiji
  AND nichiji  = :nichiji
  AND racenum  = :racenum
  AND umaban   = :umaban
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
) -> int:
    """1日分の斤量補正を計算して nl_performance を UPDATE する。戻り値は UPDATE 件数。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    rows = conn.run(_SQL_TARGET, year=year, monthday=monthday)
    if not rows:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return 0

    updated = 0
    skipped = 0

    for row in rows:
        (year_h, monthday_h, jyocd, kaiji, nichiji,
         racenum, umaban,
         futan_raw, sexcd, jyuryocd, kyori_raw, race_male_max) = row

        futan = float(futan_raw)
        kyori = int(kyori_raw)
        furlongs = kyori / 200.0

        # ハンデ/別定戦 → futan_dev = 0
        if jyuryocd in ('3', '4'):
            futan_dev = 0.0
        else:
            # 牡馬・騸馬が出走しないレース（全牝馬戦等）→ NULL
            if race_male_max is None:
                skipped += 1
                continue

            base_weight = float(race_male_max)
            # 牝馬は牡馬基準から -2kg
            if sexcd == '2':
                base_weight -= 2.0

            coeff = _get_coeff(kyori)
            futan_dev = round((base_weight - futan) * coeff / furlongs, 3)

        conn.run(
            _SQL_UPDATE,
            futan_dev=futan_dev,
            year=int(year_h),
            monthday=int(monthday_h),
            jyocd=jyocd,
            kaiji=kaiji,
            nichiji=nichiji,
            racenum=racenum,
            umaban=umaban,
        )
        updated += 1

    if skipped:
        print(f"  {date_str}: 基準斤量算出不可（全牝馬戦等）{skipped} 件スキップ → futan_dev=NULL のまま")

    return updated


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='斤量補正計算 (nl_performance.futan_dev UPDATE)',
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
            n = calc_one_day(conn, d)
            if n:
                print(f"{d.strftime('%Y%m%d')}: {n} 件更新")
            total_updated += n

        print(f"\n完了: 合計 {total_updated} レコードを更新しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
