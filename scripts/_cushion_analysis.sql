-- ============================================================
-- cushion_value vs perf_index 相関・係数推定
-- 芝（surface='T'）・2022-2024年
-- ============================================================

WITH perf_ts AS (
  SELECT
    p.year,
    p.monthday,
    p.jyocd,
    p.racenum,
    p.kyori,
    p.run_sec,
    p.base_sec,
    p.time_dev,
    p.perf_index,
    ts.track_index,
    ts.cushion_value,
    ts.moisture_index,
    (p.year::text || LPAD(p.monthday::text, 4, '0')) AS race_date_str
  FROM nl_performance p
  JOIN nl_track_speed ts
    ON (p.year::text || LPAD(p.monthday::text, 4, '0')) = ts.race_date
   AND p.jyocd   = ts.jyocd
   AND p.surface = ts.surface
  WHERE p.surface = 'T'
    AND p.year BETWEEN 2022 AND 2024
    AND ts.cushion_value IS NOT NULL
    AND ts.track_index   IS NOT NULL
    AND p.perf_index     IS NOT NULL
    AND p.base_sec       IS NOT NULL
    AND p.kyori BETWEEN 1200 AND 3600
),

race_day AS (
  SELECT
    race_date_str,
    jyocd,
    kyori,
    COUNT(*)                           AS n_runners,
    AVG(time_dev)                      AS avg_time_dev,
    AVG(perf_index)                    AS avg_perf_index,
    AVG(run_sec - base_sec)            AS avg_raw_gap_sec,
    MAX(track_index)                   AS track_index,
    MAX(cushion_value)                 AS cushion_value
  FROM perf_ts
  GROUP BY race_date_str, jyocd, kyori
  HAVING COUNT(*) >= 6
)

-- ============================================================
-- 1. 個体レベル相関
-- ============================================================
SELECT
  '1_individual' AS query,
  COUNT(*)                                               AS n,
  ROUND(CORR(cushion_value, perf_index)::numeric, 4)    AS corr_cushion_perf,
  ROUND(CORR(track_index,   perf_index)::numeric, 4)    AS corr_tindex_perf,
  ROUND(CORR(cushion_value, time_dev)::numeric,   4)    AS corr_cushion_timedev,
  ROUND(CORR(track_index,   time_dev)::numeric,   4)    AS corr_tindex_timedev
FROM perf_ts

UNION ALL

-- ============================================================
-- 2. レース日×場レベル相関（track effect の粒度）
-- ============================================================
SELECT
  '2_race_day' AS query,
  COUNT(*)                                                     AS n,
  ROUND(CORR(cushion_value,     avg_time_dev)::numeric, 4)    AS corr_cushion_perf,
  ROUND(CORR(track_index,       avg_time_dev)::numeric, 4)    AS corr_tindex_perf,
  ROUND(CORR(cushion_value - 9, avg_raw_gap_sec)::numeric, 4) AS corr_cushion_timedev,
  ROUND(CORR(track_index - 100, avg_raw_gap_sec)::numeric, 4) AS corr_tindex_timedev
FROM race_day

ORDER BY query;
