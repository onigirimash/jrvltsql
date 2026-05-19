#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
時系列補正スクリプト（実力点数化 Step 6）

nl_performance の perf_index を norm_index スケール（avg=50, std=10）に正規化してから
時系列集約し nl_horse_index.current_index を更新する。

正規化:
  norm_scaled = (perf_index - global_mean) / global_std × 10 + 50
  これにより current_index が norm_index と同一スケール（avg≈50, std≈8〜9）になる。

算出式:
  current_index = 直近5走加重平均 × 0.7 + キャリアベスト3走平均 × 0.3

直近5走加重平均:
  対象: 同一 (distance_cat, surface) の直近5走（perf_index IS NOT NULL）
  重み: exp(-経過日数 / 180)
  計算: Σ(norm_scaled × weight) / Σ(weight)
  ※ 5走未満でも得られた走数で計算

キャリアベスト3走平均:
  対象: 同一 (distance_cat, surface) の直近2年以内のレース
  選択: norm_scaled 上位3走の単純平均
  ※ 3走未満でも得られた走数で計算

合成フォールバック:
  両方あり  → 0.7 × 直近加重 + 0.3 × キャリアベスト
  直近のみ  → 直近加重
  ベストのみ → キャリアベスト
  なし       → NULL（更新しない）

Usage:
    py -3.12-32 scripts/calc_current_index.py [options]

    --decay N       減衰定数（日数、デフォルト: 180）
    --recent-n N    直近N走（デフォルト: 5）
    --best-n N      キャリアベストN走（デフォルト: 3）
    --best-years N  キャリアベスト対象年数（デフォルト: 2）
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import math
import os
from collections import defaultdict
from datetime import date, timedelta

import pg8000.native

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# perf_index のグローバル統計（正規化に使用）
_SQL_PERF_STATS = """
SELECT AVG(perf_index), STDDEV_POP(perf_index)
FROM nl_performance
WHERE perf_index IS NOT NULL
"""

# 中央競馬の全出走馬 × perf_index を取得（日付制限なし）
_SQL_FETCH = """
SELECT
    TRIM(se.kettonum)          AS kettonum,
    se.year,
    se.monthday,
    ra.kyori,
    LEFT(ra.trackcd, 1)        AS track_first,
    p.perf_index
FROM nl_se se
JOIN nl_ra ra
  ON  ra.year      = se.year
  AND ra.monthday  = se.monthday
  AND ra.jyocd     = se.jyocd
  AND ra.racenum   = se.racenum
JOIN nl_performance p
  ON  p.year       = se.year
  AND p.monthday   = se.monthday
  AND p.jyocd      = se.jyocd
  AND p.racenum::int = se.racenum
  AND p.umaban::int  = se.umaban
WHERE se.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni >= 1
  AND se.kettonum IS NOT NULL
  AND se.kettonum <> ''
  AND p.perf_index IS NOT NULL
ORDER BY se.kettonum, se.year DESC, se.monthday DESC
"""

# nl_horse_index の全キーを取得
_SQL_KEYS = """
SELECT kettonum, distance_cat, surface
FROM nl_horse_index
ORDER BY kettonum, distance_cat, surface
"""

_SQL_UPDATE = """
UPDATE nl_horse_index
SET current_index = :current_index,
    updated_at    = NOW()
WHERE kettonum     = :kettonum
  AND distance_cat = :distance_cat
  AND surface      = :surface
"""

_SQL_CLEAR = """
UPDATE nl_horse_index
SET current_index = NULL,
    updated_at    = NOW()
WHERE kettonum     = :kettonum
  AND distance_cat = :distance_cat
  AND surface      = :surface
"""

# ──────────────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────────────

def _kyori_to_dist_cat(kyori: int) -> str:
    if kyori <= 1400:
        return 'S'
    if kyori <= 1800:
        return 'M'
    if kyori <= 2200:
        return 'I'
    return 'L'


def _track_to_surface(track_first: str) -> str:
    if track_first == '1':
        return 'T'
    if track_first == '2':
        return 'D'
    return 'J'


def _to_date(year: int, monthday: int) -> date:
    month = monthday // 100
    day   = monthday % 100
    return date(year, month, day)


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


# ──────────────────────────────────────────────────────
# コア計算
# ──────────────────────────────────────────────────────

def _weighted_recent(
    races: list[tuple[date, float]],
    today: date,
    decay: float,
    n: int,
) -> float | None:
    """
    直近N走の指数加重平均を返す。

    races: [(race_date, perf_index)] — 日付降順ソート済み
    """
    recent = races[:n]
    if not recent:
        return None
    weights = [math.exp(-(today - rd).days / decay) for rd, _ in recent]
    w_sum = sum(weights)
    if w_sum == 0:
        return None
    return sum(w * pi for (_, pi), w in zip(recent, weights)) / w_sum


def _career_best(
    races: list[tuple[date, float]],
    cutoff: date,
    n: int,
) -> float | None:
    """
    直近best_years年以内のレースからperf_index上位N走の平均を返す。

    races: [(race_date, perf_index)] — 日付降順ソート済み
    """
    within = [(rd, pi) for rd, pi in races if rd >= cutoff]
    if not within:
        return None
    top_n = sorted(within, key=lambda x: x[1], reverse=True)[:n]
    return sum(pi for _, pi in top_n) / len(top_n)


def _calc_current(
    races: list[tuple[date, float]],
    today: date,
    decay: float,
    recent_n: int,
    best_n: int,
    best_years: int,
) -> float | None:
    """current_index を計算する。"""
    cutoff = today - timedelta(days=best_years * 365)
    recent = _weighted_recent(races, today, decay, recent_n)
    best   = _career_best(races, cutoff, best_n)

    if recent is not None and best is not None:
        return round(recent * 0.7 + best * 0.3, 3)
    if recent is not None:
        return round(recent, 3)
    if best is not None:
        return round(best, 3)
    return None


# ──────────────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────────────

def calc_current_index(
    conn: pg8000.native.Connection,
    decay: float,
    recent_n: int,
    best_n: int,
    best_years: int,
) -> dict:
    today = date.today()

    # perf_index のグローバル統計を取得（norm_index スケールへの正規化に使用）
    stat_row = conn.run(_SQL_PERF_STATS)[0]
    perf_mean = float(stat_row[0])
    perf_std  = float(stat_row[1])
    if perf_std == 0:
        perf_std = 1.0
    print(f"  perf_index 統計: mean={perf_mean:.4f}  std={perf_std:.4f}")
    print(f"  正規化式: (perf_index - {perf_mean:.4f}) / {perf_std:.4f} × 10 + 50")

    # 全出走履歴を取得
    rows = conn.run(_SQL_FETCH)
    print(f"  取得: {len(rows)} 行")

    # (kettonum, dist_cat, surface) → [(race_date, norm_scaled)] 日付降順
    # norm_scaled = (perf_index - global_mean) / global_std * 10 + 50
    horse_races: dict[tuple, list] = defaultdict(list)
    for row in rows:
        kettonum, year, monthday, kyori, track_first, perf_index = row
        race_date   = _to_date(int(year), int(monthday))
        dist_cat    = _kyori_to_dist_cat(int(kyori))
        surface     = _track_to_surface(track_first or '')
        norm_scaled = (float(perf_index) - perf_mean) / perf_std * 10 + 50
        horse_races[(str(kettonum), dist_cat, surface)].append(
            (race_date, norm_scaled)
        )

    # nl_horse_index の全キーを取得
    keys = conn.run(_SQL_KEYS)
    print(f"  nl_horse_index エントリ数: {len(keys)}")

    updated  = 0
    no_races = 0

    for kettonum, dist_cat, surface in keys:
        races = horse_races.get((str(kettonum).strip(), dist_cat.strip(), surface.strip()), [])

        current = _calc_current(races, today, decay, recent_n, best_n, best_years)

        if current is None:
            conn.run(_SQL_CLEAR,
                     kettonum=kettonum, distance_cat=dist_cat, surface=surface)
            no_races += 1
        else:
            conn.run(_SQL_UPDATE,
                     current_index=current,
                     kettonum=kettonum, distance_cat=dist_cat, surface=surface)
            updated += 1

    return {'updated': updated, 'no_races': no_races}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='時系列補正計算 (nl_horse_index.current_index UPDATE)',
    )
    parser.add_argument('--decay',      type=float, default=180.0, metavar='DAYS',
                        help='exp 減衰定数（日数、デフォルト: 180）')
    parser.add_argument('--recent-n',   type=int,   default=5,     metavar='N',
                        help='直近N走（デフォルト: 5）')
    parser.add_argument('--best-n',     type=int,   default=3,     metavar='N',
                        help='キャリアベストN走（デフォルト: 3）')
    parser.add_argument('--best-years', type=int,   default=2,     metavar='N',
                        help='キャリアベスト対象年数（デフォルト: 2）')
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD', ''))
    args = parser.parse_args()

    conn = _connect(args)
    try:
        print(f"  設定: decay={args.decay}日, 直近{args.recent_n}走, "
              f"キャリアベスト{args.best_n}走（{args.best_years}年以内）")
        stats = calc_current_index(
            conn,
            decay=args.decay,
            recent_n=args.recent_n,
            best_n=args.best_n,
            best_years=args.best_years,
        )
        print(f"\n完了: {stats['updated']} 件を更新、"
              f"{stats['no_races']} 件はレースなしのため NULL。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
