#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
推定勝率計算スクリプト（実力点数化 Step 9）

nl_horse_index.adjusted_index を脚質×ペース補正後に Softmax 変換し
レース別・馬別の推定勝率を nl_race_prediction へ保存する。

計算式:
  effective_index[i] = adjusted_index[i] + pace_bias_score[i]
  win_prob[i] = exp(effective_index[i] / T) / Σ exp(effective_index[j] / T)
  T = 温度パラメータ（デフォルト: 5.0）

ペース想定（逃げ+先行系の頭数で判定）:
  >= 4頭 → ハイペース / 1〜3頭 → 平均（補正なし）/ 0頭 → スロー

補正値（前走脚質 × ペース想定）:
  ハイ: 後方系+0.8 / 中団系+0.3 / 先行系-0.3 / 逃げ-0.5
  スロー: 逃げ+0.3 / 先行系+0.2 / 中団系-0.5 / 後方系-0.8

Usage:
    py -3.12-32 scripts/calc_win_prob.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --temperature FLOAT        温度パラメータ T（デフォルト: 5.0）
    --max-debut INT            除外閾値: 初出走馬がこの頭数以上で除外（デフォルト: 3）
    --logic-version TEXT       ロジックバージョン文字列（デフォルト: 2.0）
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

_DEFAULT_T       = 5.0
_DEFAULT_DEBUT   = 3
_DEFAULT_VERSION  = '2.0'
_DEFAULT_NO_BIAS  = True   # pace_biasは廃止（相関係数が悪化したため）

# 先行系カウント対象（逃げ+先行系の頭数でペース判定）
_FRONT_KYAKU = {'逃げ', '先行', 'まくり'}

# ペース別・前走脚質別の補正値
_PACE_BIAS: dict[str, dict[str, float]] = {
    'high': {    # 逃げ+先行系 >= 4頭 → ハイペース想定
        '逃げ':   -0.5,
        '先行':   -0.3,
        'まくり': -0.3,
        '差し':   +0.3,
        '中団':   +0.3,
        '追込':   +0.8,
        '後方':   +0.8,
    },
    'slow': {    # 逃げ+先行系 0頭 → スロー想定
        '逃げ':   +0.3,
        '先行':   +0.2,
        'まくり': +0.2,
        '差し':   -0.5,
        '中団':   -0.5,
        '追込':   -0.8,
        '後方':   -0.8,
    },
    'avg':  {},  # 1〜3頭 → 補正なし
}

# ──────────────────────────────────────────────────────
# SQL
# ──────────────────────────────────────────────────────

# nl_race_prediction に pace_bias_score カラムを追加
_SQL_ADD_PACE_COL = """
ALTER TABLE nl_race_prediction
  ADD COLUMN IF NOT EXISTS pace_bias_score NUMERIC
"""

# 対象日の出走馬 + nl_horse_index + 前走脚質を一括取得
_SQL_PARTICIPANTS = """
WITH prev_kyaku AS (
    SELECT DISTINCT ON (TRIM(kettonum))
        TRIM(kettonum)     AS kettonum,
        target_kyakushitsu
    FROM nl_se
    WHERE target_kyakushitsu IS NOT NULL
      AND target_kyakushitsu <> ''
      AND year >= :yr2
      AND (year < :year OR (year = :year AND monthday < :monthday))
    ORDER BY TRIM(kettonum), year DESC, monthday DESC, racenum DESC
)
SELECT
    se.year,
    se.monthday,
    se.jyocd,
    se.racenum,
    se.umaban,
    TRIM(se.kettonum)  AS kettonum,
    hi.kettonum        AS hi_found,
    hi.adjusted_index  AS adjusted_index,
    pk.target_kyakushitsu AS prev_kyaku
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
LEFT JOIN prev_kyaku pk ON pk.kettonum = TRIM(se.kettonum)
WHERE se.year     = :year
  AND se.monthday = :monthday
  AND se.jyocd BETWEEN '01' AND '10'
  AND (se.kakuteijyuni >= 1 OR COALESCE(se.ijyocd, '0') = '0')
  AND se.kettonum IS NOT NULL
  AND se.kettonum <> ''
ORDER BY se.jyocd, se.racenum, se.umaban
"""

_SQL_DELETE = """
DELETE FROM nl_race_prediction
WHERE year = :year AND monthday = :monthday
"""

_SQL_INSERT = """
INSERT INTO nl_race_prediction
    (year, monthday, jyocd, racenum, umaban, kettonum,
     adjusted_index, win_prob, place_prob, t_parameter,
     logic_version, pace_bias_score, created_at)
VALUES
    (:year, :monthday, :jyocd, :racenum, :umaban, :kettonum,
     :adjusted_index, :win_prob, :place_prob, :t_parameter,
     :logic_version, :pace_bias_score, NOW())
ON CONFLICT (year, monthday, jyocd, racenum, umaban)
DO UPDATE SET
    kettonum        = EXCLUDED.kettonum,
    adjusted_index  = EXCLUDED.adjusted_index,
    win_prob        = EXCLUDED.win_prob,
    place_prob      = EXCLUDED.place_prob,
    t_parameter     = EXCLUDED.t_parameter,
    logic_version   = EXCLUDED.logic_version,
    pace_bias_score = EXCLUDED.pace_bias_score,
    created_at      = EXCLUDED.created_at
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


def _plackett_luce_place(probs: list[float]) -> list[float]:
    """Plackett-Luce モデルで3着以内確率を計算する。"""
    n = len(probs)
    if n <= 3:
        return [1.0] * n

    place = list(probs)

    for i in range(n):
        p2 = 0.0
        pi = probs[i]
        for j in range(n):
            if j == i:
                continue
            pj = probs[j]
            dj = 1.0 - pj
            if dj > 1e-9:
                p2 += pj * pi / dj
        place[i] += p2

    for i in range(n):
        p3 = 0.0
        pi = probs[i]
        for j in range(n):
            if j == i:
                continue
            pj = probs[j]
            dj = 1.0 - pj
            if dj <= 1e-9:
                continue
            for k in range(n):
                if k == i or k == j:
                    continue
                pk = probs[k]
                djk = dj - pk
                if djk <= 1e-9:
                    continue
                p3 += pj * (pk / dj) * (pi / djk)
        place[i] += p3

    return [min(1.0, max(0.0, p)) for p in place]


def _pace_scenario(front_count: int) -> str:
    """逃げ+先行系の頭数からペース想定（high/avg/slow）を返す。"""
    if front_count >= 4:
        return 'high'
    if front_count == 0:
        return 'slow'
    return 'avg'


def _calc_pace_bias(scenario: str, prev_kyaku: str | None) -> float:
    """前走脚質とペース想定から補正値を返す。脚質不明は0。"""
    if scenario == 'avg' or prev_kyaku is None:
        return 0.0
    return _PACE_BIAS[scenario].get(prev_kyaku, 0.0)


# ──────────────────────────────────────────────────────
# コア処理
# ──────────────────────────────────────────────────────

def calc_one_day(
    conn: pg8000.native.Connection,
    target: date,
    T: float,
    max_debut: int,
    logic_version: str,
    no_pace_bias: bool = False,
) -> dict:
    """1日分の推定勝率を計算して nl_race_prediction へ保存する。"""
    year, monthday = _to_year_monthday(target)
    date_str = target.strftime('%Y%m%d')
    yr2      = year - 2   # 前走脚質の参照範囲（直近2年）

    rows = conn.run(_SQL_PARTICIPANTS, year=year, monthday=monthday, yr2=yr2)
    if not rows:
        print(f"  {date_str}: 対象レースなし（スキップ）")
        return {'races': 0, 'skipped': 0, 'horses': 0, 'biased': 0}

    # レース別グループ化
    races: dict[tuple, list] = defaultdict(list)
    for (year_, monthday_, jyocd, racenum, umaban,
         kettonum, hi_found, adjusted_index, prev_kyaku) in rows:
        race_key = (str(jyocd), int(racenum))
        races[race_key].append({
            'umaban':         int(umaban),
            'kettonum':       str(kettonum),
            'is_debut':       hi_found is None,
            'adjusted_index': adjusted_index,
            'prev_kyaku':     prev_kyaku,
        })

    total_races  = 0
    skipped      = 0
    total_horses = 0
    total_biased = 0

    for (jyocd, racenum), horses in sorted(races.items()):
        debut_count = sum(1 for h in horses if h['is_debut'])
        if debut_count >= max_debut:
            skipped += 1
            continue

        if not horses:
            continue

        # ペース想定: 逃げ+先行系の頭数で判定（--no-pace-bias 時は全て0）
        adj_values = [
            float(h['adjusted_index']) if h['adjusted_index'] is not None else 0.0
            for h in horses
        ]
        if no_pace_bias:
            bias_scores = [0.0] * len(horses)
        else:
            front_count = sum(
                1 for h in horses if h['prev_kyaku'] in _FRONT_KYAKU
            )
            scenario    = _pace_scenario(front_count)
            bias_scores = [_calc_pace_bias(scenario, h['prev_kyaku']) for h in horses]
        adj_biased = [a + b for a, b in zip(adj_values, bias_scores)]

        probs       = _softmax(adj_biased, T)
        place_probs = _plackett_luce_place(probs)

        biased_cnt = sum(1 for b in bias_scores if b != 0.0)
        total_biased += biased_cnt

        for h, adj, bias, prob, pplace in zip(
            horses, adj_values, bias_scores, probs, place_probs
        ):
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
                place_prob=round(pplace, 6),
                t_parameter=T,
                logic_version=logic_version,
                pace_bias_score=round(bias, 3) if bias != 0.0 else None,
            )
            total_horses += 1

        total_races += 1

    print(f"  {date_str}: {total_races} races "
          f"(skip={skipped} / horses={total_horses} / pace_biased={total_biased})")
    return {
        'races': total_races, 'skipped': skipped,
        'horses': total_horses, 'biased': total_biased,
    }


# ──────────────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='推定勝率計算 Softmax + pace bias (nl_race_prediction INSERT/UPDATE)',
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
    parser.add_argument('--no-pace-bias', action='store_true', default=_DEFAULT_NO_BIAS,
                        help='脚質×ペース補正を無効化（デフォルト: True）')
    parser.add_argument('--pace-bias', dest='no_pace_bias', action='store_false',
                        help='脚質×ペース補正を有効化（ABテスト用）')
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
        # カラム追加（初回のみ実行される）
        conn.run(_SQL_ADD_PACE_COL)

        total_races  = 0
        total_horses = 0
        total_skip   = 0
        total_biased = 0

        for d in dates:
            stats = calc_one_day(
                conn, d,
                T=args.temperature,
                max_debut=args.max_debut,
                logic_version=args.logic_version,
                no_pace_bias=args.no_pace_bias,
            )
            total_races  += stats['races']
            total_horses += stats['horses']
            total_skip   += stats['skipped']
            total_biased += stats['biased']

        print(f"\n完了: {total_races} レース・{total_horses} 頭を計算、"
              f"{total_skip} レース除外、{total_biased} 頭にペース補正適用。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
