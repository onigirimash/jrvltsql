#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
個馬補正計算スクリプト（実力点数化 Step 3②）

回顧ツールの review_disadvantage テーブルから不利情報を取得し
nl_performance.disadv_dev へ UPDATE する。

計算式:
  disadv_dev（秒/ハロン）= Σ(程度 × 不利種別係数) ÷ ハロン数
  ・複数の不利がある場合は合算
  ・プラス = 不利を受けた（実際より遅く走った分を補正加点）
  ・0      = 不利なし（review_disadvantage にデータなし）

不利種別係数（秒/程度）:
  出遅れ     : 0.15
  前が壁     : 0.12
  外回しロス : 0.10
  掛かり     : 0.08

Usage:
    py -3.12-32 scripts/calc_disadv_correction.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# 不利種別係数
# ──────────────────────────────────────────────────────

_COEFFS: dict[str, float] = {
    '出遅れ':     0.15,
    '前が壁':     0.12,
    '外回しロス': 0.10,
    '掛かり':     0.08,
}

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の不利情報を一括取得（horse_num/severity が NULL のものは除外）
_SQL_DISADV = """
SELECT
    rk.jyo_cd,
    rr.race_num,
    rd.horse_num,
    rd.disadvantage_type,
    rd.severity
FROM review_disadvantage rd
JOIN review_race    rr ON rr.id = rd.race_id
JOIN review_kaishi  rk ON rk.id = rr.kaishi_id
WHERE rk.race_date = :race_date
  AND rd.horse_num IS NOT NULL
  AND rd.severity  IS NOT NULL
ORDER BY rk.jyo_cd, rr.race_num, rd.horse_num
"""

# 対象日の全 nl_performance を disadv_dev=0 にリセット
_SQL_RESET = """
UPDATE nl_performance
SET disadv_dev = 0, updated_at = NOW()
WHERE year = :year AND monthday = :monthday
"""

# 同一レース内の furlongs を取得（レース内全馬同一なので LIMIT 1）
_SQL_GET_FURLONGS = """
SELECT furlongs
FROM nl_performance
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd    = :jyocd
  AND racenum  = :racenum
LIMIT 1
"""

# 個馬の disadv_dev を UPDATE
_SQL_UPDATE = """
UPDATE nl_performance
SET disadv_dev = :disadv_dev, updated_at = NOW()
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd    = :jyocd
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
    """1日分の個馬補正を計算して nl_performance を UPDATE する。戻り値は UPDATE 件数。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    # 不利データ取得
    rows = conn.run(_SQL_DISADV, race_date=target)

    # 全馬をまず 0 にリセット
    conn.run(_SQL_RESET, year=year, monthday=monthday)

    if not rows:
        print(f"  {date_str}: 不利データなし（全馬 disadv_dev=0）")
        return 0

    # (jyo_cd, race_num, horse_num) → weighted_loss を合算
    totals: dict[tuple, float] = defaultdict(float)
    for jyo_cd, race_num, horse_num, disadv_type, severity in rows:
        coeff = _COEFFS.get(disadv_type, 0.0)
        totals[(jyo_cd, int(race_num), int(horse_num))] += int(severity) * coeff

    updated = 0
    missing = 0

    for (jyo_cd, race_num, horse_num), weighted_loss in totals.items():
        # nl_performance の racenum/umaban は CHAR(2) 後方スペース形式（'7 '等）
        # zfill(2)='07' は一致しないため、ゼロパディングなしで渡す
        racenum_str = str(race_num)
        umaban_str  = str(horse_num)

        # furlongs を nl_performance から取得
        furlongs_rows = conn.run(
            _SQL_GET_FURLONGS,
            year=year, monthday=monthday,
            jyocd=jyo_cd, racenum=racenum_str,
        )
        if not furlongs_rows or furlongs_rows[0][0] is None:
            print(f"  WARN: {date_str} jyo={jyo_cd} R{race_num} 馬{horse_num} - nl_performance に対応レコードなし")
            missing += 1
            continue

        furlongs = float(furlongs_rows[0][0])
        disadv_dev = round(weighted_loss / furlongs, 3)

        conn.run(
            _SQL_UPDATE,
            disadv_dev=disadv_dev,
            year=year,
            monthday=monthday,
            jyocd=jyo_cd,
            racenum=racenum_str,
            umaban=umaban_str,
        )
        updated += 1

    if missing:
        print(f"  {date_str}: nl_performance 未登録 {missing} 件スキップ")

    return updated


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='個馬補正計算 (nl_performance.disadv_dev UPDATE)',
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
                print(f"{d.strftime('%Y%m%d')}: {n} 件更新（不利補正あり）")
            total_updated += n

        print(f"\n完了: 合計 {total_updated} 件に不利補正を適用しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
