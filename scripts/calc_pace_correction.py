#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
展開補正計算スクリプト（実力点数化 Step 3③）

PCI（Pace Change Index）と脚質から展開の恩恵/損失を秒/ハロン換算し
nl_performance.pace_dev へ UPDATE する。

PCI の計算式:
  前半Ave-3F = (走破タイム - 上がり3F) × 600 ÷ (距離 - 600)
  PCI = 上がり3F ÷ (前半Ave-3F + 上がり3F) × 100
  ※PCI < 47 : ハイペース / PCI 47-53 : ミドル / PCI > 53 : スロー

補正値の計算式:
  ハイペース(PCI<47) × 逃げ先行(脚質1,2): pace_dev = -(47-PCI) × 0.02 / ハロン数
  スロー(PCI>53) × 差し追込(脚質3,4)   : pace_dev = -(PCI-53) × 0.02 / ハロン数
  それ以外                               : pace_dev = 0

脚質の導出:
  nl_se.kyakusitukubun は現行 JRA-VAN データで無効値（0x40等）のため使用不可。
  代わりに最終コーナー順位の相対位置から推定する:
    relative = jyuni4c（最終コーナー順位）÷ 完走頭数
    relative ≤ 0.25 → 逃げ(1)
    relative ≤ 0.50 → 先行(2)
    relative ≤ 0.75 → 差し(3)
    relative >  0.75 → 追込(4)
  コーナー順位がすべて0のレース（直線コース等）は pace_dev = 0。

データソース:
  走破タイム : nl_se.time（MMSS.T形式）
  上がり3F   : nl_ra.haron3l（秒）
  距離       : nl_ra.kyori（m）
  コーナー順位: nl_se.jyuni2c / jyuni3c / jyuni4c
  完走頭数   : nl_se.kakuteijyuni >= 1 の COUNT（レース単位集計）

Usage:
    py -3.12-32 scripts/calc_pace_correction.py [options]

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
# 定数
# ──────────────────────────────────────────────────────

_STYLE_COEFF = 0.02   # 秒/PCI点
_PCI_HI      = 47.0   # ハイペース閾値
_PCI_SL      = 53.0   # スロー閾値

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日の全完走馬（コーナー順位・haron3l 付き）
# finisher_count: 同一レースの確定着順馬数（脚質閾値の基準）
_SQL_TARGET = """
WITH race_finishers AS (
    SELECT
        year, monthday, jyocd, kaiji, nichiji, racenum,
        COUNT(*) AS finisher_count
    FROM nl_se
    WHERE year = :year AND monthday = :monthday
      AND kakuteijyuni >= 1
    GROUP BY year, monthday, jyocd, kaiji, nichiji, racenum
)
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.kaiji,
    se.nichiji,
    se.racenum,
    se.umaban,
    FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100) AS run_sec,
    se.jyuni2c,
    se.jyuni3c,
    se.jyuni4c,
    ra.haron3l,
    ra.kyori,
    rf.finisher_count
FROM nl_se se
JOIN nl_ra ra
  ON  ra.year     = se.year
  AND ra.monthday = se.monthday
  AND ra.jyocd    = se.jyocd
  AND ra.kaiji    = se.kaiji
  AND ra.nichiji  = se.nichiji
  AND ra.racenum  = se.racenum
JOIN race_finishers rf
  ON  rf.year     = se.year
  AND rf.monthday = se.monthday
  AND rf.jyocd    = se.jyocd
  AND rf.kaiji    = se.kaiji
  AND rf.nichiji  = se.nichiji
  AND rf.racenum  = se.racenum
WHERE ra.year     = :year
  AND ra.monthday = :monthday
  AND ra.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni >= 1
  AND se.time > 0
ORDER BY se.jyocd, se.racenum, se.umaban
"""

# 対象日の全 nl_performance を pace_dev=0 にリセット
_SQL_RESET = """
UPDATE nl_performance
SET pace_dev = 0, updated_at = NOW()
WHERE year = :year AND monthday = :monthday
"""

# 個馬の pace_dev を UPDATE
# racenum/umaban は CHAR(2) 後方スペース形式（'7 '等）に合わせ str() のみで渡す
_SQL_UPDATE = """
UPDATE nl_performance
SET pace_dev = :pace_dev, updated_at = NOW()
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


def _derive_running_style(
    jyuni4c: int,
    jyuni3c: int,
    jyuni2c: int,
    finisher_count: int,
) -> str | None:
    """
    最終コーナー順位の相対位置から脚質を推定する。

    Returns:
        '1'=逃げ, '2'=先行, '3'=差し, '4'=追込, None=不明（直線コース等）
    """
    pos = (jyuni4c if jyuni4c and jyuni4c > 0 else
           jyuni3c if jyuni3c and jyuni3c > 0 else
           jyuni2c if jyuni2c and jyuni2c > 0 else None)
    if pos is None or finisher_count <= 0:
        return None
    ratio = pos / finisher_count
    if ratio <= 0.25:
        return '1'
    elif ratio <= 0.50:
        return '2'
    elif ratio <= 0.75:
        return '3'
    else:
        return '4'


def _calc_pci(run_sec: float, haron3l: float, kyori: int) -> float | None:
    """
    PCI（Pace Change Index）を計算する。

    Returns:
        PCI 値（0〜100 の範囲）, 計算不可の場合は None
    """
    if not haron3l or haron3l <= 0 or kyori <= 600:
        return None
    if run_sec <= haron3l:
        return None
    zenhan_ave = (run_sec - haron3l) * 600.0 / (kyori - 600)
    total = zenhan_ave + haron3l
    if total <= 0:
        return None
    return haron3l / total * 100.0


def _calc_pace_dev(pci: float | None, style: str | None, furlongs: float) -> float:
    """展開補正値を計算する。補正対象外は 0.0 を返す。"""
    if pci is None or style is None or furlongs <= 0:
        return 0.0
    if style in ('1', '2') and pci < _PCI_HI:      # 逃げ・先行 × ハイペース
        pace_gap = _PCI_HI - pci
        return round(-(pace_gap * _STYLE_COEFF / furlongs), 3)
    elif style in ('3', '4') and pci > _PCI_SL:     # 差し・追込 × スロー
        pace_gap = pci - _PCI_SL
        return round(-(pace_gap * _STYLE_COEFF / furlongs), 3)
    return 0.0


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
) -> dict:
    """1日分の展開補正を計算して nl_performance を UPDATE する。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    rows = conn.run(_SQL_TARGET, year=year, monthday=monthday)

    # 全馬を pace_dev=0 にリセット
    conn.run(_SQL_RESET, year=year, monthday=monthday)

    if not rows:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return {'total': 0, 'nonzero': 0}

    total = 0
    nonzero = 0
    no_style = 0
    no_pci = 0

    for row in rows:
        (year_h, monthday_h, jyocd, kaiji, nichiji, racenum, umaban,
         run_sec_raw, jyuni2c, jyuni3c, jyuni4c,
         haron3l, kyori_raw, finisher_count) = row

        run_sec   = float(run_sec_raw)
        kyori     = int(kyori_raw)
        furlongs  = kyori / 200.0

        style = _derive_running_style(
            int(jyuni4c or 0), int(jyuni3c or 0), int(jyuni2c or 0),
            int(finisher_count),
        )
        pci = _calc_pci(run_sec, float(haron3l) if haron3l else None, kyori)

        if style is None:
            no_style += 1
        if pci is None:
            no_pci += 1

        pace_dev = _calc_pace_dev(pci, style, furlongs)

        # pace_dev=0 は RESET 済みのためスキップ（UPDATE 負荷削減）
        if pace_dev == 0.0:
            total += 1
            continue

        conn.run(
            _SQL_UPDATE,
            pace_dev=pace_dev,
            year=int(year_h),
            monthday=int(monthday_h),
            jyocd=jyocd,
            racenum=str(racenum),
            umaban=str(umaban),
        )
        total += 1
        nonzero += 1

    if no_style:
        print(f"  {date_str}: 脚質算出不可（直線コース等）{no_style} 件 → pace_dev=0")
    if no_pci:
        print(f"  {date_str}: PCI算出不可（haron3l未設定等）{no_pci} 件 → pace_dev=0")

    return {'total': total, 'nonzero': nonzero}


def _summarize_results(conn, year: int, monthday: int, date_str: str):
    """PCI 別補正統計をログ出力する。"""
    rows = conn.run(
        """
        SELECT jyocd,
               COUNT(*) FILTER (WHERE pace_dev < 0) AS penalized,
               COUNT(*) FILTER (WHERE pace_dev = 0) AS neutral,
               ROUND(AVG(pace_dev) FILTER (WHERE pace_dev < 0)::numeric, 3) AS avg_penalty
        FROM nl_performance
        WHERE year = :year AND monthday = :monthday
          AND pace_dev IS NOT NULL
        GROUP BY jyocd ORDER BY jyocd
        """,
        year=year, monthday=monthday,
    )
    for r in rows:
        jyocd, penalized, neutral, avg_penalty = r
        if penalized and penalized > 0:
            print(f"  jyo={jyocd}  補正あり={penalized}件  avg={avg_penalty:+.3f}  中立={neutral}件")


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='展開補正計算 (nl_performance.pace_dev UPDATE)',
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
        total_horses = 0
        total_nonzero = 0

        for d in dates:
            year, monthday = _to_year_monthday(d)
            stats = calc_one_day(conn, d)
            if stats['total']:
                print(f"{d.strftime('%Y%m%d')}: {stats['total']} 馬処理"
                      f"（展開補正あり: {stats['nonzero']} 件）")
                _summarize_results(conn, year, monthday, d.strftime('%Y%m%d'))
            total_horses += stats['total']
            total_nonzero += stats['nonzero']

        print(f"\n完了: 合計 {total_horses} 馬処理、うち {total_nonzero} 件に展開補正を適用しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
