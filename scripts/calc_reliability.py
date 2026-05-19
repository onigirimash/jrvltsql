#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
信頼度計算スクリプト（実力点数化 Step 7）

馬全体の出走回数合計から信頼度を算出し
adjusted_index = current_index × reliability + 50 × (1 - reliability) を計算して nl_horse_index へ保存する。
（信頼度が低い馬は平均値50へ縮約。距離区分・馬場に関係なく、その馬の全出走回数を信頼度の基準にする）

出走回数 → 信頼度 の線形補間:
  1戦  → 0.300
  3戦  → 0.600   （1〜3戦区間: +0.15/戦）
  5戦  → 0.800   （3〜5戦区間: +0.10/戦）
  10戦 → 1.000   （5〜10戦区間: +0.04/戦）
  10戦以上 → 1.0 固定

対象となる出走回数:
  nl_performance.perf_index IS NOT NULL のレースのみカウント
  （計測できていないレースは除外）

Usage:
    py -3.12-32 scripts/calc_reliability.py [options]

    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os

import pg8000.native

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 馬全体（distance_cat・surface を問わない）の出走回数を一括集計
_SQL_RACE_COUNT = """
SELECT kettonum, COUNT(*) AS race_count
FROM (
    SELECT TRIM(se.kettonum) AS kettonum
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
) sub
GROUP BY kettonum
"""

_SQL_UPDATE = """
UPDATE nl_horse_index
SET reliability    = :reliability,
    adjusted_index = :adjusted_index,
    updated_at     = NOW()
WHERE kettonum     = :kettonum
  AND distance_cat = :distance_cat
  AND surface      = :surface
"""

# ──────────────────────────────────────────────────────
# 信頼度計算
# ──────────────────────────────────────────────────────

# 区間ごとの (min_races, max_races, min_rel, max_rel)
_BREAKPOINTS = [
    (1,  3,  0.3, 0.6),
    (3,  5,  0.6, 0.8),
    (5, 10,  0.8, 1.0),
]

def _race_count_to_reliability(count: int) -> float:
    """出走回数から信頼度を線形補間で計算する。"""
    if count <= 0:
        return 0.3
    if count >= 10:
        return 1.0
    for lo, hi, rel_lo, rel_hi in _BREAKPOINTS:
        if lo <= count <= hi:
            return rel_lo + (count - lo) / (hi - lo) * (rel_hi - rel_lo)
    return 1.0


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


# ──────────────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────────────

def calc_reliability(conn: pg8000.native.Connection) -> dict:
    # 出走回数を集計（馬全体の合計）
    count_rows = conn.run(_SQL_RACE_COUNT)
    count_map: dict[str, int] = {
        str(k).strip(): int(c)
        for k, c in count_rows
    }
    print(f"  出走回数集計: {len(count_map)} 頭（馬全体の合計出走回数）")

    # nl_horse_index の全エントリを対象に UPDATE
    keys = conn.run(
        "SELECT kettonum, distance_cat, surface, current_index FROM nl_horse_index"
    )
    print(f"  nl_horse_index エントリ数: {len(keys)}")

    updated   = 0
    no_current = 0

    for kettonum, dist_cat, surface, current_index in keys:
        race_count  = count_map.get(str(kettonum).strip(), 0)
        reliability = round(_race_count_to_reliability(race_count), 3)

        if current_index is None:
            adjusted = None
        else:
            adjusted = round(float(current_index) * reliability + 50.0 * (1.0 - reliability), 3)
            updated += 1

        if current_index is None:
            no_current += 1

        conn.run(
            _SQL_UPDATE,
            reliability=reliability,
            adjusted_index=adjusted,
            kettonum=kettonum,
            distance_cat=dist_cat,
            surface=surface,
        )

    return {'total': len(keys), 'updated': updated, 'no_current': no_current}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='信頼度計算 (nl_horse_index.reliability / adjusted_index UPDATE)',
    )
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD', ''))
    args = parser.parse_args()

    conn = _connect(args)
    try:
        stats = calc_reliability(conn)
        print(f"\n完了: {stats['total']} 件処理、"
              f"adjusted_index 更新={stats['updated']} 件、"
              f"current_index なし={stats['no_current']} 件。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
