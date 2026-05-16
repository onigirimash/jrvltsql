#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
バイアス補正計算スクリプト（実力点数化 Step 3④）

回顧ツールの review_track_bias テーブルから馬場バイアスを取得し
nl_performance.bias_dev へ UPDATE する。

計算式:
  bias_dev（秒/ハロン）= (内外効果 × 内外係数 + 前後効果 × 前後係数) ÷ ハロン数

  内外効果 = -(inside_outside_score × io_factor)
    io_factor : 枠番 1-4 = -1（内）/ 枠番 5-8 = +1（外）
    内有利(score>0) × 外枠(io_factor=+1) → 内外効果 = 負 → bias_dev 減少
    外有利(score<0) × 内枠(io_factor=-1) → 内外効果 = 負 → bias_dev 減少

  前後効果 = -(front_back_score × fb_factor)
    fb_factor : 逃げ・先行(脚質1,2) = -1（前） / 差し・追込(脚質3,4) = +1（後）
    前有利(score>0) × 差追(fb_factor=+1)  → 前後効果 = 負 → bias_dev 減少
    後有利(score<0) × 逃先(fb_factor=-1)  → 前後効果 = 負 → bias_dev 減少

係数:
  内外係数: 0.03秒/スコア点
  前後係数: 0.03秒/スコア点

脚質判定:
  nl_se.kyakusitukubun は現行 JRA-VAN データで無効値のため
  jyuni4c/3c/2c の相対位置（÷完走頭数）から推定（calc_pace_correction.py と同一）

バイアスデータの紐付け:
  review_track_bias → review_kaishi で (race_date, jyo_cd) に紐付け
  track_type（芝/ダート/障害）と distance_category（短距離/マイル/中距離/長距離）で絞り込み
  distance_category が NULL のエントリは全距離に適用（距離別エントリがあればそちらを優先）

Usage:
    py -3.12-32 scripts/calc_bias_correction.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────────────

_IO_COEFF = 0.03   # 内外係数（秒/スコア点）
_FB_COEFF = 0.03   # 前後係数（秒/スコア点）

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 対象日のバイアスデータを一括取得
_SQL_BIAS = """
SELECT
    rk.jyo_cd,
    rtb.track_type,
    rtb.distance_category,
    COALESCE(rtb.inside_outside_score, 0) AS io_score,
    COALESCE(rtb.front_back_score, 0)     AS fb_score
FROM review_track_bias rtb
JOIN review_kaishi rk ON rk.id = rtb.kaishi_id
WHERE rk.race_date = :race_date
"""

# 対象日の全完走馬（コーナー順位・枠番・走路付き）
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
    se.wakuban,
    se.jyuni2c,
    se.jyuni3c,
    se.jyuni4c,
    ra.kyori,
    LEFT(ra.trackcd, 1) AS track_first,
    rf.finisher_count,
    p.furlongs
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
JOIN nl_performance p
  ON  p.year     = se.year
  AND p.monthday = se.monthday
  AND p.jyocd    = se.jyocd
  AND p.racenum::int = se.racenum
  AND p.umaban::int  = se.umaban
WHERE ra.year     = :year
  AND ra.monthday = :monthday
  AND ra.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni >= 1
  AND se.time > 0
ORDER BY se.jyocd, se.racenum, se.umaban
"""

# 対象日の全 nl_performance を bias_dev=0 にリセット
_SQL_RESET = """
UPDATE nl_performance
SET bias_dev = 0, updated_at = NOW()
WHERE year = :year AND monthday = :monthday
"""

# 個馬の bias_dev を UPDATE
_SQL_UPDATE = """
UPDATE nl_performance
SET bias_dev = :bias_dev, updated_at = NOW()
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


def _trackcd_to_ja(track_first: str) -> str:
    """nl_ra.trackcd の先頭文字を日本語走路名に変換する。"""
    if track_first == '1':
        return '芝'
    if track_first == '2':
        return 'ダート'
    return '障害'


def _kyori_to_dist_cat(kyori: int) -> str:
    """距離から distance_category 文字列へ変換する。"""
    if kyori <= 1400:
        return '短距離'
    if kyori <= 1800:
        return 'マイル'
    if kyori <= 2200:
        return '中距離'
    return '長距離'


def _waku_to_io_factor(wakuban: int) -> int:
    """枠番から内外因子を返す（内=-1, 外=+1, 不明=0）。"""
    try:
        w = int(wakuban)
        if w <= 0:
            return 0
        return -1 if w <= 4 else 1
    except (TypeError, ValueError):
        return 0


def _derive_running_style(
    jyuni4c: int, jyuni3c: int, jyuni2c: int, finisher_count: int,
) -> str | None:
    """
    最終コーナー順位の相対位置から脚質を推定する。

    Returns:
        '1'=逃げ, '2'=先行, '3'=差し, '4'=追込, None=不明
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


def _style_to_fb_factor(style: str | None) -> int:
    """脚質から前後因子を返す（前=-1, 後=+1, 不明=0）。"""
    if style in ('1', '2'):
        return -1   # 逃げ・先行 = 前
    if style in ('3', '4'):
        return 1    # 差し・追込 = 後
    return 0


def _build_bias_map(rows: list) -> dict:
    """
    バイアスデータから検索用 dict を構築する。

    Key: (jyo_cd, track_type_ja, distance_category_or_None)
    Value: (io_score, fb_score)
    距離別エントリが優先され、NULL（全距離適用）はフォールバックとして使用。
    """
    bias_map = {}
    for jyo_cd, track_type, dist_cat, io_score, fb_score in rows:
        bias_map[(jyo_cd, track_type, dist_cat)] = (int(io_score), int(fb_score))
    return bias_map


def _lookup_bias(
    bias_map: dict,
    jyo_cd: str,
    track_type_ja: str,
    dist_cat: str,
) -> tuple[int, int] | None:
    """
    バイアスを検索する。距離別エントリ優先、なければ全距離適用エントリを返す。

    Returns:
        (io_score, fb_score) or None（データなし）
    """
    entry = bias_map.get((jyo_cd, track_type_ja, dist_cat))
    if entry is None:
        entry = bias_map.get((jyo_cd, track_type_ja, None))
    return entry


def _calc_bias_dev(
    io_score: int,
    fb_score: int,
    io_factor: int,
    fb_factor: int,
    furlongs: float,
) -> float:
    """バイアス補正値を計算する。"""
    if furlongs <= 0:
        return 0.0
    io_effect = -(io_score * io_factor)
    fb_effect = -(fb_score * fb_factor)
    return round((io_effect * _IO_COEFF + fb_effect * _FB_COEFF) / furlongs, 3)


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
) -> dict:
    """1日分のバイアス補正を計算して nl_performance を UPDATE する。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')

    # バイアスデータ取得
    bias_rows = conn.run(_SQL_BIAS, race_date=target)

    # 全馬を bias_dev=0 にリセット
    conn.run(_SQL_RESET, year=year, monthday=monthday)

    if not bias_rows:
        print(f"  {date_str}: バイアスデータなし（全馬 bias_dev=0）")
        return {'total': 0, 'nonzero': 0}

    bias_map = _build_bias_map(bias_rows)

    # 対象馬取得
    horse_rows = conn.run(_SQL_TARGET, year=year, monthday=monthday)
    if not horse_rows:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return {'total': 0, 'nonzero': 0}

    total = 0
    nonzero = 0

    for row in horse_rows:
        (year_h, monthday_h, jyocd, kaiji, nichiji, racenum, umaban,
         wakuban, jyuni2c, jyuni3c, jyuni4c,
         kyori_raw, track_first, finisher_count, furlongs_raw) = row

        kyori    = int(kyori_raw)
        furlongs = float(furlongs_raw) if furlongs_raw else kyori / 200.0

        track_type_ja = _trackcd_to_ja(track_first or '')
        dist_cat      = _kyori_to_dist_cat(kyori)

        bias = _lookup_bias(bias_map, jyocd, track_type_ja, dist_cat)
        if bias is None:
            # このレースの会場・走路のバイアスデータなし → RESET 済みの 0 のまま
            total += 1
            continue

        io_score, fb_score = bias

        style     = _derive_running_style(
            int(jyuni4c or 0), int(jyuni3c or 0), int(jyuni2c or 0),
            int(finisher_count),
        )
        io_factor = _waku_to_io_factor(wakuban)
        fb_factor = _style_to_fb_factor(style)

        bias_dev = _calc_bias_dev(io_score, fb_score, io_factor, fb_factor, furlongs)

        if bias_dev == 0.0:
            total += 1
            continue  # RESET 済みのため UPDATE 不要

        conn.run(
            _SQL_UPDATE,
            bias_dev=bias_dev,
            year=int(year_h),
            monthday=int(monthday_h),
            jyocd=jyocd,
            racenum=str(racenum),
            umaban=str(umaban),
        )
        total += 1
        nonzero += 1

    return {'total': total, 'nonzero': nonzero}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='バイアス補正計算 (nl_performance.bias_dev UPDATE)',
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
            stats = calc_one_day(conn, d)
            if stats['total']:
                print(f"{d.strftime('%Y%m%d')}: {stats['total']} 馬処理"
                      f"（バイアス補正あり: {stats['nonzero']} 件）")
            total_horses += stats['total']
            total_nonzero += stats['nonzero']

        print(f"\n完了: 合計 {total_horses} 馬処理、うち {total_nonzero} 件にバイアス補正を適用しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
