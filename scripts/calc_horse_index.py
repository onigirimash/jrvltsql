#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
馬別実力指数計算スクリプト（実力点数化 Step 5）

perf_index（Step4）を入力として、レース強度補正をイテレーションで適用し
平均50・標準偏差10に正規化した norm_index を nl_horse_index へ保存する。

アルゴリズム:
  1. 直近3年の全出走馬の perf_index を取得
  2. 馬ごとに (kettonum, 距離区分, 走路) 単位で初期評価値を計算
       horse_rating = avg(perf_index)
  3. イテレーション:
       a. 各レースの強度 = 出走馬の horse_rating の平均
       b. 各レースの強度補正 = レース強度 − グローバル平均
       c. 各馬の race-adjusted score = perf_index − 強度補正
       d. horse_rating = avg(race-adjusted score) per (kettonum, 距離区分, 走路)
       e. 全馬の変化量最大値 ≤ 0.01 → 収束
  4. 正規化: norm_index = (horse_rating − 全馬平均) ÷ 全馬標準偏差 × 10 + 50
  5. nl_horse_index へ UPSERT

距離区分:
  S = 短距離（〜1400m）
  M = マイル（1500〜1800m）
  I = 中距離（1900〜2200m）
  L = 長距離（2300m〜）

走路変換 (nl_ra.trackcd 先頭):
  '1' → T（芝）  '2' → D（ダート）  その他 → J（障害）

Usage:
    py -3.12-32 scripts/calc_horse_index.py [options]

    --years N              直近N年のデータを使用（デフォルト: 3）
    --max-iter N           イテレーション上限（デフォルト: 20）
    --converge THRESHOLD   収束閾値（デフォルト: 0.01）
    --as-of-date YYYYMMDD  この日付以降のレースを除外（ルックアヘッド防止。省略時は今日）
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from collections import defaultdict
from datetime import date
from statistics import mean, stdev

import pg8000.native

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# 直近N年の全出走馬 × perf_index を一括取得
_SQL_FETCH = """
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.racenum,
    se.kettonum,
    ra.kyori,
    LEFT(ra.trackcd, 1)  AS track_first,
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
WHERE (se.year > :cutoff_year
    OR (se.year = :cutoff_year AND se.monthday >= :cutoff_monthday))
  AND (se.year < :as_of_year
    OR (se.year = :as_of_year AND se.monthday <= :as_of_monthday))
  AND se.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni >= 1
  AND se.kettonum IS NOT NULL
  AND se.kettonum <> ''
  AND p.perf_index IS NOT NULL
ORDER BY se.year, se.monthday, se.jyocd, se.racenum, se.umaban
"""

_SQL_UPSERT = """
INSERT INTO nl_horse_index (kettonum, distance_cat, surface, norm_index, updated_at)
VALUES (:kettonum, :distance_cat, :surface, :norm_index, NOW())
ON CONFLICT (kettonum, distance_cat, surface)
DO UPDATE SET
    norm_index = EXCLUDED.norm_index,
    updated_at = EXCLUDED.updated_at
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


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


def _cutoff_year_monthday(years: int, as_of: date) -> tuple[int, int]:
    """as_of日からN年前の起点日を (year, monthday) 形式で返す。"""
    cutoff = as_of.replace(year=as_of.year - years)
    return cutoff.year, cutoff.month * 100 + cutoff.day


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def _build_structures(rows: list) -> tuple[dict, dict]:
    """
    取得行からイテレーション用データ構造を構築する。

    Returns:
        race_horses: race_key -> [(horse_key, perf_index)]
        horse_races: horse_key -> [(race_key, perf_index)]
    """
    race_horses: dict = defaultdict(list)
    horse_races: dict = defaultdict(list)

    for row in rows:
        year, monthday, jyocd, racenum, kettonum, kyori, track_first, perf_index = row
        dist_cat = _kyori_to_dist_cat(int(kyori))
        surface  = _track_to_surface(track_first or '')
        race_key  = (year, monthday, jyocd, int(racenum), dist_cat, surface)
        horse_key = (str(kettonum).strip(), dist_cat, surface)
        perf = float(perf_index)
        race_horses[race_key].append((horse_key, perf))
        horse_races[horse_key].append((race_key, perf))

    return dict(race_horses), dict(horse_races)


def _iterate(
    race_horses: dict,
    horse_races: dict,
    max_iter: int,
    converge_threshold: float,
) -> dict:
    """
    レース強度補正イテレーションを実行し、収束後の horse_rating を返す。

    horse_rating: horse_key -> float (raw score)
    """
    # 初期値: perf_index の単純平均
    horse_rating: dict[tuple, float] = {
        hk: mean(p for _, p in races)
        for hk, races in horse_races.items()
    }

    for iteration in range(1, max_iter + 1):
        # 各レースの強度（出走馬 horse_rating の平均）
        race_avg: dict[tuple, float] = {}
        for rk, participants in race_horses.items():
            ratings = [horse_rating.get(hk, 0.0) for hk, _ in participants]
            race_avg[rk] = mean(ratings)

        global_avg = mean(race_avg.values())

        # horse_rating 更新: perf_index - (race_strength - global_avg)
        new_rating: dict[tuple, float] = {}
        for hk, races in horse_races.items():
            adjusted = [
                perf - (race_avg[rk] - global_avg)
                for rk, perf in races
            ]
            new_rating[hk] = mean(adjusted)

        # 収束チェック
        max_change = max(
            abs(new_rating[hk] - horse_rating[hk]) for hk in new_rating
        )
        horse_rating = new_rating

        print(f"  反復 {iteration:2d}: 最大変化量={max_change:.6f}  "
              f"グローバル平均={global_avg:+.4f}")

        if max_change <= converge_threshold:
            print(f"  → 収束（{iteration}回）")
            break
    else:
        print(f"  → 上限 {max_iter} 回に達したため終了")

    return horse_rating


def _normalize(horse_rating: dict) -> dict[tuple, float]:
    """horse_rating を平均50・標準偏差10に正規化する。"""
    values = list(horse_rating.values())
    g_mean = mean(values)
    g_std  = stdev(values) if len(values) > 1 else 1.0
    return {
        hk: round((v - g_mean) / g_std * 10 + 50, 2)
        for hk, v in horse_rating.items()
    }


def _upsert(conn: pg8000.native.Connection, norm_index: dict) -> int:
    """nl_horse_index へ UPSERT し、更新件数を返す。"""
    count = 0
    for (kettonum, dist_cat, surface), ni in norm_index.items():
        conn.run(
            _SQL_UPSERT,
            kettonum=kettonum,
            distance_cat=dist_cat,
            surface=surface,
            norm_index=ni,
        )
        count += 1
    return count


def calc_horse_index(
    conn: pg8000.native.Connection,
    years: int,
    max_iter: int,
    converge_threshold: float,
    as_of: date | None = None,
) -> dict:
    """馬別実力指数を計算して nl_horse_index へ保存する。"""
    if as_of is None:
        as_of = date.today()
    as_of_year     = as_of.year
    as_of_monthday = as_of.month * 100 + as_of.day
    cutoff_year, cutoff_monthday = _cutoff_year_monthday(years, as_of)
    print(f"  対象期間: {cutoff_year}/{cutoff_monthday:04d}〜{as_of_year}/{as_of_monthday:04d}（直近{years}年）")

    rows = conn.run(
        _SQL_FETCH,
        cutoff_year=cutoff_year,
        cutoff_monthday=cutoff_monthday,
        as_of_year=as_of_year,
        as_of_monthday=as_of_monthday,
    )
    print(f"  取得: {len(rows)} 行")

    if not rows:
        print("  対象データなし（スキップ）")
        return {'horses': 0, 'rows': 0, 'iterations': 0}

    race_horses, horse_races = _build_structures(rows)
    print(f"  レース数: {len(race_horses)}  馬×区分数: {len(horse_races)}")

    horse_rating = _iterate(race_horses, horse_races, max_iter, converge_threshold)

    norm_result = _normalize(horse_rating)

    # 正規化後の分布サマリ
    nv = list(norm_result.values())
    print(f"  正規化後: avg={mean(nv):.2f}  std={stdev(nv):.2f}  "
          f"min={min(nv):.2f}  max={max(nv):.2f}")

    upserted = _upsert(conn, norm_result)
    return {'horses': len(horse_races), 'rows': upserted, 'iterations': max_iter}


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='馬別実力指数計算 (nl_horse_index UPSERT)',
    )
    parser.add_argument('--years',    type=int,   default=3,    metavar='N',
                        help='直近N年のデータを使用（デフォルト: 3）')
    parser.add_argument('--max-iter', type=int,   default=20,   metavar='N',
                        help='イテレーション上限（デフォルト: 20）')
    parser.add_argument('--converge', type=float, default=0.01, metavar='THRESHOLD',
                        help='収束閾値（デフォルト: 0.01）')
    parser.add_argument('--as-of-date', default=None, metavar='YYYYMMDD',
                        help='この日付以降のレースを除外（ルックアヘッド防止。省略時は今日）')
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     default=os.environ.get('POSTGRES_PORT',     '5432'))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD', ''))
    args = parser.parse_args()

    as_of = None
    if args.as_of_date:
        d = args.as_of_date.replace('-', '')
        as_of = date(int(d[:4]), int(d[4:6]), int(d[6:8]))

    conn = _connect(args)
    try:
        stats = calc_horse_index(
            conn,
            years=args.years,
            max_iter=args.max_iter,
            converge_threshold=args.converge,
            as_of=as_of,
        )
        print(f"\n完了: {stats['horses']} 馬×区分を処理、"
              f"{stats['rows']} 件を nl_horse_index へ保存しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
