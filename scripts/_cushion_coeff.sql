-- ============================================================
-- クッション係数推定 + ハイブリッド補正の効果試算
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
    race_date_str, jyocd, kyori,
    COUNT(*)                       AS n_runners,
    AVG(time_dev)                  AS avg_time_dev,
    AVG(run_sec - base_sec)        AS avg_raw_gap_sec,
    MAX(track_index)               AS track_index,
    MAX(cushion_value)             AS cushion_value
  FROM perf_ts
  GROUP BY race_date_str, jyocd, kyori
  HAVING COUNT(*) >= 6
),

-- ============================================================
-- 2. クッション係数推定（回帰スロープ）
-- ============================================================
coeff AS (
  SELECT
    ROUND(REGR_SLOPE(avg_time_dev, cushion_value - 9)::numeric,    4)  AS k_cushion_timedev,
    ROUND(REGR_SLOPE(avg_time_dev, track_index - 100)::numeric,    4)  AS k_tindex_timedev,
    ROUND(REGR_SLOPE(avg_raw_gap_sec, cushion_value - 9)::numeric, 4)  AS k_cushion_sec,
    ROUND(REGR_SLOPE(avg_raw_gap_sec, track_index - 100)::numeric, 4)  AS k_tindex_sec,
    ROUND(REGR_INTERCEPT(avg_time_dev, cushion_value - 9)::numeric, 4) AS intercept_c,
    ROUND(REGR_R2(avg_time_dev, cushion_value - 9)::numeric,       4)  AS r2_cushion,
    ROUND(REGR_R2(avg_time_dev, track_index - 100)::numeric,       4)  AS r2_tindex,
    COUNT(*) AS n
  FROM race_day
)

SELECT * FROM coeff;
