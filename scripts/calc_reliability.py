#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
信頼度計算スクリプト（実力点数化 Step 7）

馬全体の出走回数合計から信頼度を算出し
adjusted_index を計算して nl_horse_index へ保存する。

縮約先: クラス別平均 current_index（50固定から変更）
  adjusted_index = current_index × reliability + class_avg × (1 - reliability)

  class_avg: 各馬の最新レースの class_code 別の current_index 平均値
  フォールバック: class_code 不明の場合は全馬の current_index 平均

出走回数 → 信頼度 の線形補間:
  1戦  → 0.500
  3戦  → 0.700   （1〜3戦区間: +0.10/戦）
  5戦  → 0.850   （3〜5戦区間: +0.075/戦）
  10戦 → 1.000   （5〜10戦区間: +0.03/戦）
  10戦以上 → 1.0 固定

Usage:
    py -3.12-32 scripts/calc_reliability.py [options]

    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from collections import defaultdict

import pg8000.native

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 馬全体の出走回数を一括集計（perf_index 計測済みレースのみ）
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
      ON  p.year         = se.year
      AND p.monthday     = se.monthday
      AND p.jyocd        = se.jyocd
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

# 各馬の最新レースの class_code を取得
_SQL_LATEST_CLASS = """
SELECT DISTINCT ON (TRIM(kettonum))
    TRIM(kettonum) AS kettonum,
    class_code
FROM nl_se
WHERE class_code IS NOT NULL
  AND class_code <> ''
ORDER BY TRIM(kettonum), year DESC, monthday DESC, racenum DESC
"""

_SQL_KEYS = """
SELECT kettonum, distance_cat, surface, current_index
FROM nl_horse_index
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

_BREAKPOINTS = [
    (1,  3,  0.5,  0.7),
    (3,  5,  0.7,  0.85),
    (5, 10,  0.85, 1.0),
]

def _race_count_to_reliability(count: int) -> float:
    if count <= 0:
        return 0.5
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
    # 1. 出走回数集計
    count_rows = conn.run(_SQL_RACE_COUNT)
    count_map: dict[str, int] = {str(k).strip(): int(c) for k, c in count_rows}
    print(f"  出走回数集計: {len(count_map)} 頭")

    # 2. 各馬の最新 class_code を取得
    class_rows  = conn.run(_SQL_LATEST_CLASS)
    horse_class: dict[str, str] = {str(k).strip(): str(c) for k, c in class_rows if c}
    print(f"  class_code 取得: {len(horse_class)} 頭")

    # 3. nl_horse_index の全エントリを取得（リストに保持）
    keys = list(conn.run(_SQL_KEYS))
    print(f"  nl_horse_index エントリ数: {len(keys)}")

    # 4. クラス別の current_index 集計 → クラス平均を計算
    class_idx: dict[str, list[float]] = defaultdict(list)
    all_vals: list[float] = []
    for kettonum, dist_cat, surface, current_index in keys:
        if current_index is not None:
            val = float(current_index)
            all_vals.append(val)
            cls = horse_class.get(str(kettonum).strip())
            if cls:
                class_idx[cls].append(val)

    # クラス別平均（サンプル少ないクラスは全体平均にフォールバック）
    global_avg: float = sum(all_vals) / len(all_vals) if all_vals else 50.0
    class_avg: dict[str, float] = {}
    for cls, vals in class_idx.items():
        class_avg[cls] = sum(vals) / len(vals)

    print(f"  全体平均: {global_avg:.3f}")
    for cls in sorted(class_avg):
        print(f"    class={cls}: avg={class_avg[cls]:.3f}  n={len(class_idx[cls])}")

    # 5. adjusted_index を計算して UPDATE
    updated    = 0
    no_current = 0

    for kettonum, dist_cat, surface, current_index in keys:
        race_count  = count_map.get(str(kettonum).strip(), 0)
        reliability = round(_race_count_to_reliability(race_count), 3)

        cls          = horse_class.get(str(kettonum).strip())
        shrink_to    = class_avg.get(cls, global_avg) if cls else global_avg

        if current_index is None:
            adjusted = None
            no_current += 1
        else:
            adjusted = round(
                float(current_index) * reliability + shrink_to * (1.0 - reliability),
                3,
            )
            updated += 1

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
