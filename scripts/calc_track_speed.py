#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
馬場指数計算スクリプト（実力点数化 Step 1）

[A] 日次 track_index（既存）
  各開催日・競馬場・走路ごとに馬場指数を計算し nl_track_speed へ UPSERT する。
  計算式: 過去5年同条件平均タイム / 当日レース平均タイム の平均 × 100

[B] per_race_factor（新規追加）
  レース単位の馬場係数を nl_ra.per_race_factor に格納する。
  計算式: 過去5年同条件（競馬場×距離±200m×芝ダ×クラス5区分）上位3頭平均タイム中央値
          ÷ 当該レース上位3頭平均タイム × 100
  クラス5区分:
    cls1 = 新馬・未勝利 (jyokencd 010/005)
    cls2 = 1勝・2勝クラス (jyokencd 703)
    cls3 = 3勝・OP (jyokencd 701/016, gradecd L/H/G)
    cls4 = G3 (gradecd C)
    cls5 = G2・G1 (gradecd A/B)

Usage:
    py -3.12-32 scripts/calc_track_speed.py [options]

    --date YYYYMMDD            対象日（単日）
    --date-from / --date-to    対象期間
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import os
from datetime import date, datetime, timedelta

import pg8000.native

# 走路コード（trackcd 先頭1文字 → surface）
_SURF = {'1': 'T', '2': 'D', '5': 'J', '6': 'J'}

# フォールバック閾値（これ未満のレース数なら前日値継承）
_MIN_RACES = 3

# 過去参照年数
_LOOKBACK_YEARS = 5

# per_race_factor: 過去同条件の最小サンプル数
_MIN_SAMPLES = 30

# ── 馬場補正係数（2024相関分析より）──────────────────────
_CUSHION_STD     = 9.0
_CUSHION_COEF    = 0.12
_DIRT_MOIST_MEAN = 5.9
_DIRT_MOIST_COEF = 0.095

# クラス区分の CASE 式（SQL 埋め込み用）
_CLASS_GROUP_CASE = """
    CASE
      WHEN TRIM(ra.gradecd) IN ('A','B')  THEN 'cls5'
      WHEN TRIM(ra.gradecd) = 'C'         THEN 'cls4'
      WHEN TRIM(ra.gradecd) IN ('L','H','G') THEN 'cls3'
      WHEN COALESCE(
             NULLIF(ra.jyokencd1,'000'), NULLIF(ra.jyokencd2,'000'),
             NULLIF(ra.jyokencd3,'000'), NULLIF(ra.jyokencd4,'000'),
             NULLIF(ra.jyokencd5,'000'), 'OP'
           ) IN ('010','005','016')         THEN 'cls1'
      WHEN COALESCE(
             NULLIF(ra.jyokencd1,'000'), NULLIF(ra.jyokencd2,'000'),
             NULLIF(ra.jyokencd3,'000'), NULLIF(ra.jyokencd4,'000'),
             NULLIF(ra.jyokencd5,'000'), 'OP'
           ) = '703'                        THEN 'cls2'
      ELSE 'cls3'
    END
"""

# ──────────────────────────────────────────────
# [A] 日次 track_index 用 SQL（既存ロジック）
# ──────────────────────────────────────────────

_SQL_CALC = """
WITH current_races AS (
    SELECT
        ra.jyocd,
        ra.racenum,
        ra.kyori,
        CASE
            WHEN LEFT(ra.trackcd, 1) = '1' THEN 'T'
            WHEN LEFT(ra.trackcd, 1) = '2' THEN 'D'
            ELSE 'J'
        END AS surface,
        CASE
            WHEN ra.gradecd IN ('A', 'B', 'C') THEN ra.gradecd
            WHEN ra.gradecd = 'E'              THEN 'E'
            ELSE COALESCE(
                NULLIF(ra.jyokencd1, '000'),
                NULLIF(ra.jyokencd2, '000'),
                NULLIF(ra.jyokencd3, '000'),
                NULLIF(ra.jyokencd4, '000'),
                NULLIF(ra.jyokencd5, '000'),
                'OP'
            )
        END AS race_class,
        AVG(FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)) AS cur_sec
    FROM nl_ra ra
    JOIN nl_se se
      ON  ra.year     = se.year
      AND ra.monthday = se.monthday
      AND ra.jyocd    = se.jyocd
      AND ra.kaiji    = se.kaiji
      AND ra.nichiji  = se.nichiji
      AND ra.racenum  = se.racenum
    WHERE ra.year     = :year
      AND ra.monthday = :monthday
      AND ra.jyocd BETWEEN '01' AND '10'
      AND LEFT(ra.trackcd, 1) IN ('1', '2', '5', '6')
      AND se.kakuteijyuni >= 1
      AND se.time > 0
    GROUP BY
        ra.jyocd, ra.racenum, ra.kyori, ra.trackcd,
        ra.gradecd, ra.jyokencd1, ra.jyokencd2,
        ra.jyokencd3, ra.jyokencd4, ra.jyokencd5
),
hist_race_avg AS (
    SELECT
        ra.jyocd,
        ra.kyori,
        CASE
            WHEN LEFT(ra.trackcd, 1) = '1' THEN 'T'
            WHEN LEFT(ra.trackcd, 1) = '2' THEN 'D'
            ELSE 'J'
        END AS surface,
        CASE
            WHEN ra.gradecd IN ('A', 'B', 'C') THEN ra.gradecd
            WHEN ra.gradecd = 'E'              THEN 'E'
            ELSE COALESCE(
                NULLIF(ra.jyokencd1, '000'),
                NULLIF(ra.jyokencd2, '000'),
                NULLIF(ra.jyokencd3, '000'),
                NULLIF(ra.jyokencd4, '000'),
                NULLIF(ra.jyokencd5, '000'),
                'OP'
            )
        END AS race_class,
        AVG(FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100)) AS race_avg_sec
    FROM nl_ra ra
    JOIN nl_se se
      ON  ra.year     = se.year
      AND ra.monthday = se.monthday
      AND ra.jyocd    = se.jyocd
      AND ra.kaiji    = se.kaiji
      AND ra.nichiji  = se.nichiji
      AND ra.racenum  = se.racenum
    WHERE ra.jyocd BETWEEN '01' AND '10'
      AND LEFT(ra.trackcd, 1) IN ('1', '2', '5', '6')
      AND se.kakuteijyuni >= 1
      AND se.time > 0
      AND ra.year * 10000 + ra.monthday < :ref_date
      AND ra.year >= :ref_year
    GROUP BY
        ra.jyocd, ra.kyori, ra.trackcd,
        ra.gradecd, ra.jyokencd1, ra.jyokencd2,
        ra.jyokencd3, ra.jyokencd4, ra.jyokencd5,
        ra.year, ra.monthday, ra.racenum
),
hist_avg AS (
    SELECT
        jyocd, kyori, surface, race_class,
        AVG(race_avg_sec) AS hist_sec
    FROM hist_race_avg
    GROUP BY jyocd, kyori, surface, race_class
),
ratios AS (
    SELECT
        cr.jyocd,
        cr.surface,
        CASE
            WHEN ha.hist_sec > 0 AND cr.cur_sec > 0
            THEN ha.hist_sec / cr.cur_sec
            ELSE NULL
        END AS ratio
    FROM current_races cr
    LEFT JOIN hist_avg ha
      ON  cr.jyocd      = ha.jyocd
      AND cr.kyori      = ha.kyori
      AND cr.surface    = ha.surface
      AND cr.race_class = ha.race_class
)
SELECT
    jyocd,
    surface,
    ROUND((AVG(ratio) * 100)::numeric, 2) AS track_index,
    COUNT(*)                               AS race_count
FROM ratios
WHERE ratio IS NOT NULL
GROUP BY jyocd, surface
ORDER BY jyocd, surface
"""

_SQL_PREV = """
SELECT track_index, moisture_index
FROM   nl_track_speed
WHERE  jyocd     = :jyocd
  AND  surface   = :surface
  AND  race_date < :race_date
ORDER BY race_date DESC
LIMIT 1
"""

_SQL_BABA = """
SELECT
    cushion_value,
    ROUND((turf_moisture_goal + turf_moisture_4corner) / 2, 1) AS turf_moisture,
    ROUND((dirt_moisture_goal + dirt_moisture_4corner) / 2, 1) AS dirt_moisture
FROM nl_baba_moisture
WHERE race_date = :race_date AND jyo_cd = :jyocd
"""

_SQL_UPSERT = """
INSERT INTO nl_track_speed (
    race_date, jyocd, surface, track_index, race_count, fallback,
    cushion_value, turf_moisture, dirt_moisture, moisture_index
)
VALUES (
    :race_date, :jyocd, :surface, :track_index, :race_count, :fallback,
    :cushion_value, :turf_moisture, :dirt_moisture, :moisture_index
)
ON CONFLICT (race_date, jyocd, surface) DO UPDATE SET
    track_index    = EXCLUDED.track_index,
    race_count     = EXCLUDED.race_count,
    fallback       = EXCLUDED.fallback,
    cushion_value  = EXCLUDED.cushion_value,
    turf_moisture  = EXCLUDED.turf_moisture,
    dirt_moisture  = EXCLUDED.dirt_moisture,
    moisture_index = EXCLUDED.moisture_index,
    updated_at     = NOW()
"""

# ──────────────────────────────────────────────
# [B] per_race_factor 用 SQL（新規）
# ──────────────────────────────────────────────

_SQL_ADD_PER_RACE_COL = """
ALTER TABLE nl_ra
  ADD COLUMN IF NOT EXISTS per_race_factor  NUMERIC,
  ADD COLUMN IF NOT EXISTS prf_class_group  VARCHAR(4),
  ADD COLUMN IF NOT EXISTS prf_exact_n      SMALLINT,
  ADD COLUMN IF NOT EXISTS prf_fallback     VARCHAR(16)
"""

_SQL_PER_RACE = """
WITH cur_races AS (
    -- 当日レースごとの上位3頭平均タイム
    SELECT
        ra.year, ra.monthday, ra.jyocd, ra.kaiji, ra.nichiji, ra.racenum,
        ra.kyori,
        CASE WHEN LEFT(ra.trackcd,1)='1' THEN 'T'
             WHEN LEFT(ra.trackcd,1)='2' THEN 'D'
             ELSE 'J' END                                          AS surface,
        {class_group_case}                                         AS class_group,
        AVG(FLOOR(se.time/100)*60 + MOD(se.time::numeric,100))    AS top3_sec,
        COUNT(*)                                                   AS n3
    FROM nl_ra ra
    JOIN nl_se se
      ON ra.year=se.year AND ra.monthday=se.monthday AND ra.jyocd=se.jyocd
     AND ra.kaiji=se.kaiji AND ra.nichiji=se.nichiji AND ra.racenum=se.racenum
     AND se.kakuteijyuni BETWEEN 1 AND 3
    WHERE ra.year=:year AND ra.monthday=:monthday
      AND ra.jyocd BETWEEN '01' AND '10'
      AND LEFT(ra.trackcd,1) IN ('1','2')
      AND se.time > 0
    GROUP BY ra.year, ra.monthday, ra.jyocd, ra.kaiji, ra.nichiji, ra.racenum,
             ra.kyori, ra.trackcd, ra.gradecd,
             ra.jyokencd1, ra.jyokencd2, ra.jyokencd3, ra.jyokencd4, ra.jyokencd5
    HAVING COUNT(*) = 3
),
hist_races AS (
    -- 過去5年の同条件レースごとの上位3頭平均タイム
    SELECT
        ra.jyocd, ra.kyori,
        CASE WHEN LEFT(ra.trackcd,1)='1' THEN 'T'
             WHEN LEFT(ra.trackcd,1)='2' THEN 'D'
             ELSE 'J' END                                          AS surface,
        {class_group_case}                                         AS class_group,
        AVG(FLOOR(se.time/100)*60 + MOD(se.time::numeric,100))    AS top3_sec
    FROM nl_ra ra
    JOIN nl_se se
      ON ra.year=se.year AND ra.monthday=se.monthday AND ra.jyocd=se.jyocd
     AND ra.kaiji=se.kaiji AND ra.nichiji=se.nichiji AND ra.racenum=se.racenum
     AND se.kakuteijyuni BETWEEN 1 AND 3
    WHERE ra.jyocd BETWEEN '01' AND '10'
      AND LEFT(ra.trackcd,1) IN ('1','2')
      AND ra.year >= :ref_year
      AND ra.year*10000 + ra.monthday < :ref_date
      AND se.time > 0
    GROUP BY ra.jyocd, ra.kyori, ra.trackcd, ra.gradecd,
             ra.jyokencd1, ra.jyokencd2, ra.jyokencd3, ra.jyokencd4, ra.jyokencd5,
             ra.year, ra.monthday, ra.racenum
    HAVING COUNT(*) = 3
),
hist_exact AS (
    -- 完全一致の中央値
    SELECT jyocd, kyori, surface, class_group,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY top3_sec) AS median_sec,
           COUNT(*)                                               AS n
    FROM hist_races
    GROUP BY jyocd, kyori, surface, class_group
),
hist_fallback AS (
    -- 距離±200m 補完（距離比でスケール）
    SELECT cr.jyocd, cr.kyori, cr.surface, cr.class_group,
           PERCENTILE_CONT(0.5) WITHIN GROUP (
               ORDER BY hr.top3_sec * cr.kyori::float / hr.kyori::float
           ) AS median_sec,
           COUNT(*) AS n
    FROM cur_races cr
    JOIN hist_races hr
      ON cr.jyocd        = hr.jyocd
     AND cr.surface      = hr.surface
     AND cr.class_group  = hr.class_group
     AND hr.kyori       != cr.kyori
     AND ABS(hr.kyori - cr.kyori) <= 200
    GROUP BY cr.jyocd, cr.kyori, cr.surface, cr.class_group
)
SELECT
    cr.jyocd,
    cr.racenum,
    cr.kyori,
    cr.surface,
    cr.class_group,
    cr.top3_sec,
    COALESCE(
        CASE WHEN he.n >= :min_samples THEN he.median_sec END,
        CASE WHEN hf.n >= :min_samples THEN hf.median_sec END
    )                                        AS hist_median,
    COALESCE(he.n, 0)                        AS exact_n,
    COALESCE(hf.n, 0)                        AS fallback_n,
    CASE WHEN he.n >= :min_samples THEN 'exact'
         WHEN hf.n >= :min_samples THEN 'dist200'
         ELSE 'no_data'
    END                                      AS fallback_type
FROM cur_races cr
LEFT JOIN hist_exact   he ON cr.jyocd=he.jyocd AND cr.kyori=he.kyori
                          AND cr.surface=he.surface AND cr.class_group=he.class_group
LEFT JOIN hist_fallback hf ON cr.jyocd=hf.jyocd AND cr.kyori=hf.kyori
                           AND cr.surface=hf.surface AND cr.class_group=hf.class_group
ORDER BY cr.jyocd, cr.racenum
"""

_SQL_UPDATE_PER_RACE = """
UPDATE nl_ra
SET per_race_factor = :factor,
    prf_class_group = :class_group,
    prf_exact_n     = :exact_n,
    prf_fallback    = :fallback_type
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd    = :jyocd
  AND racenum  = :racenum
"""


# ──────────────────────────────────────────────
# ヘルパー
# ──────────────────────────────────────────────

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


def _to_float(v) -> float | None:
    if v is None:
        return None
    return float(v)


def _calc_moisture_index(surface: str, cushion: float | None, dirt_moisture: float | None) -> float | None:
    if surface == 'T':
        if cushion is None:
            return None
        return round((cushion - _CUSHION_STD) * _CUSHION_COEF, 4)
    if surface == 'D':
        if dirt_moisture is None:
            return None
        return round((dirt_moisture - _DIRT_MOIST_MEAN) * _DIRT_MOIST_COEF, 4)
    return None


# ──────────────────────────────────────────────
# [A] 日次 track_index 計算
# ──────────────────────────────────────────────

def calc_one_day(conn: pg8000.native.Connection, target: date) -> list[dict]:
    year, monthday = _to_year_monthday(target)
    race_date_str  = target.strftime('%Y%m%d')
    ref_date       = year * 10000 + monthday
    ref_year       = year - _LOOKBACK_YEARS

    rows = conn.run(
        _SQL_CALC,
        year=year, monthday=monthday,
        ref_date=ref_date, ref_year=ref_year,
    )

    if not rows:
        print(f"  {race_date_str}: 対象レースなし（スキップ）")
        return []

    baba_cache: dict[str, dict] = {}

    results = []
    for row in rows:
        jyocd      = row[0]
        surface    = row[1]
        track_idx  = _to_float(row[2])
        race_count = int(row[3])

        if jyocd not in baba_cache:
            baba_rows = conn.run(_SQL_BABA, race_date=race_date_str, jyocd=jyocd)
            if baba_rows:
                baba_cache[jyocd] = {
                    'cushion':    _to_float(baba_rows[0][0]),
                    'turf_moist': _to_float(baba_rows[0][1]),
                    'dirt_moist': _to_float(baba_rows[0][2]),
                }
            else:
                baba_cache[jyocd] = {'cushion': None, 'turf_moist': None, 'dirt_moist': None}

        baba = baba_cache[jyocd]

        fallback     = False
        moist_idx_fb = None
        if race_count < _MIN_RACES:
            prev_rows = conn.run(
                _SQL_PREV,
                jyocd=jyocd, surface=surface, race_date=race_date_str,
            )
            if prev_rows and prev_rows[0][0] is not None:
                track_idx    = _to_float(prev_rows[0][0])
                moist_idx_fb = _to_float(prev_rows[0][1])
                fallback     = True

        moisture_idx = (
            moist_idx_fb if fallback
            else _calc_moisture_index(surface, baba['cushion'], baba['dirt_moist'])
        )

        rec = dict(
            race_date=race_date_str,
            jyocd=jyocd,
            surface=surface,
            track_index=track_idx,
            race_count=race_count,
            fallback=fallback,
            cushion_value=baba['cushion'],
            turf_moisture=baba['turf_moist'],
            dirt_moisture=baba['dirt_moist'],
            moisture_index=moisture_idx,
        )
        conn.run(_SQL_UPSERT, **rec)
        results.append(rec)

    return results


# ──────────────────────────────────────────────
# [B] per_race_factor 計算
# ──────────────────────────────────────────────

def _ensure_per_race_cols(conn: pg8000.native.Connection) -> None:
    conn.run(_SQL_ADD_PER_RACE_COL)


def calc_per_race(conn: pg8000.native.Connection, target: date) -> list[dict]:
    """当日の全レースについて per_race_factor を計算し nl_ra を UPDATE する。"""
    year, monthday = _to_year_monthday(target)
    ref_date       = year * 10000 + monthday
    ref_year       = year - _LOOKBACK_YEARS

    sql = _SQL_PER_RACE.format(class_group_case=_CLASS_GROUP_CASE)

    rows = conn.run(
        sql,
        year=year, monthday=monthday,
        ref_date=ref_date, ref_year=ref_year,
        min_samples=_MIN_SAMPLES,
    )

    results = []
    no_data_count = 0
    for row in rows:
        jyocd         = row[0]
        racenum       = row[1]
        kyori         = row[2]
        surface       = row[3]
        class_group   = row[4]
        top3_sec      = _to_float(row[5])
        hist_median   = _to_float(row[6])
        exact_n       = int(row[7]) if row[7] is not None else 0
        fallback_n    = int(row[8]) if row[8] is not None else 0
        fallback_type = row[9]

        if hist_median and top3_sec and top3_sec > 0:
            factor = round(hist_median / top3_sec * 100, 2)
        else:
            factor = None
            no_data_count += 1

        used_n = exact_n if fallback_type == 'exact' else fallback_n

        # nl_ra の kaiji/nichiji は1文字なのでクエリで取得しない → monthday/year/jyocd/racenum で UPDATE
        conn.run(
            _SQL_UPDATE_PER_RACE,
            factor=factor,
            class_group=class_group,
            exact_n=used_n,
            fallback_type=fallback_type,
            year=year,
            monthday=monthday,
            jyocd=jyocd,
            racenum=racenum,
        )
        results.append({
            'jyocd': jyocd, 'racenum': racenum, 'kyori': kyori,
            'surface': surface, 'class_group': class_group,
            'top3_sec': top3_sec, 'hist_median': hist_median,
            'factor': factor, 'exact_n': exact_n,
            'fallback_n': fallback_n, 'fallback_type': fallback_type,
        })

    if no_data_count:
        print(f"    per_race_factor: {len(results)} races, {no_data_count} no_data")

    return results


def _summarize(results: list[dict]) -> str:
    parts = []
    for r in sorted(results, key=lambda x: (x['jyocd'], x['surface'])):
        fb  = ' [FB]' if r['fallback'] else ''
        idx = f"{r['track_index']:.2f}" if r['track_index'] is not None else 'N/A'
        midx = f"{r['moisture_index']:+.4f}" if r['moisture_index'] is not None else 'N/A'
        parts.append(
            f"  jyo={r['jyocd']} surf={r['surface']} idx={idx} "
            f"moist_idx={midx} races={r['race_count']}{fb}"
        )
    return '\n'.join(parts)


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='馬場指数計算 (nl_track_speed + nl_ra per_race_factor)')
    parser.add_argument('--date',      metavar='YYYYMMDD')
    parser.add_argument('--date-from', metavar='YYYYMMDD')
    parser.add_argument('--date-to',   metavar='YYYYMMDD')
    parser.add_argument('--skip-per-race', action='store_true',
                        help='per_race_factor 計算をスキップ（日次 track_index のみ）')
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
        if not args.skip_per_race:
            _ensure_per_race_cols(conn)

        total_upserted = 0
        for d in dates:
            results = calc_one_day(conn, d)
            if results:
                print(f"{d.strftime('%Y%m%d')} ({len(results)} records):")
                print(_summarize(results))
                total_upserted += len(results)

            if not args.skip_per_race:
                prf = calc_per_race(conn, d)
                ok  = sum(1 for r in prf if r['factor'] is not None)
                nd  = sum(1 for r in prf if r['factor'] is None)
                if prf:
                    print(f"    per_race_factor: {ok} OK / {nd} no_data")

        print(f"\n完了: 合計 {total_upserted} レコードを upsert しました。")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
