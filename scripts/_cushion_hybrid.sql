-- ============================================================
-- ハイブリッド補正効果試算
-- track_index × cushion_value の組み合わせ
-- ============================================================

WITH perf_ts AS (
  SELECT
    p.year, p.monthday, p.jyocd, p.racenum, p.kyori,
    p.run_sec, p.base_sec, p.time_dev, p.perf_index,
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
    COUNT(*)                                  AS n_runners,
    AVG(time_dev)                             AS avg_time_dev,
    AVG(run_sec - base_sec)                   AS avg_raw_gap_sec,
    MAX(track_index)                          AS track_index,
    MAX(cushion_value)                        AS cushion_value,
    -- クッション補正済み track_index（係数 0.0573 を適用）
    MAX(track_index) + (MAX(cushion_value) - 9.0) * 0.0573 * 10 AS hybrid_index,
    -- クッション単独予測値
    (MAX(cushion_value) - 9.0) * 0.0573      AS cushion_pred
  FROM perf_ts
  GROUP BY race_date_str, jyocd, kyori
  HAVING COUNT(*) >= 6
),

-- 個体レベルにクッション補正を付与
perf_hybrid AS (
  SELECT
    p.time_dev,
    p.perf_index,
    ts.track_index,
    ts.cushion_value,
    -- 補正後パフォーマンス指数（クッション補正分を加算）
    p.perf_index + (ts.cushion_value - 9.0) * 0.0573 AS perf_cushion_adj,
    -- hybrid: track_index × クッション補正の加重平均（重み 0.8/0.2）
    (ts.track_index - 100) * 0.1210
      + (ts.cushion_value - 9.0) * 0.0573              AS hybrid_track_signal
  FROM perf_ts p
  JOIN nl_track_speed ts
    ON p.race_date_str = ts.race_date
   AND p.jyocd         = ts.jyocd
   AND ts.surface       = 'T'
)

-- ============================================================
-- 3-A. レース日レベル: R² 比較
--     track_index 単独 vs cushion 単独 vs 組み合わせ信号 vs hybrid_index
-- ============================================================
SELECT
  '3A_race_day_R2' AS query,
  ROUND(REGR_R2(avg_time_dev, track_index - 100)::numeric,  4) AS r2_tindex,
  ROUND(REGR_R2(avg_time_dev, cushion_value - 9)::numeric,  4) AS r2_cushion,
  ROUND(REGR_R2(avg_time_dev, hybrid_index  - 100)::numeric,4) AS r2_hybrid_index,
  ROUND(CORR(avg_time_dev, track_index - 100)::numeric,     4) AS corr_tindex,
  ROUND(CORR(avg_time_dev, cushion_value - 9)::numeric,     4) AS corr_cushion,
  ROUND(CORR(avg_time_dev, hybrid_index  - 100)::numeric,   4) AS corr_hybrid,
  COUNT(*) AS n
FROM race_day

UNION ALL

-- ============================================================
-- 3-B. 個体レベル: perf_index の相関比較
-- ============================================================
SELECT
  '3B_individual' AS query,
  ROUND(CORR(perf_index,         track_index - 100)::numeric,    4) AS r2_tindex,
  ROUND(CORR(perf_index,         cushion_value - 9)::numeric,    4) AS r2_cushion,
  ROUND(CORR(perf_cushion_adj,   track_index - 100)::numeric,    4) AS r2_hybrid_index,
  ROUND(CORR(perf_index,         hybrid_track_signal)::numeric,  4) AS corr_tindex,
  ROUND(CORR(time_dev,           track_index - 100)::numeric,    4) AS corr_cushion,
  ROUND(CORR(time_dev,           hybrid_track_signal)::numeric,  4) AS corr_hybrid,
  COUNT(*) AS n
FROM perf_hybrid

ORDER BY query;
