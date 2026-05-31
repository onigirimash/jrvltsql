-- 1. track_index 基本統計（2024年・surface別）
SELECT
  surface,
  ROUND(AVG(track_index)::numeric,    2) AS avg,
  ROUND(STDDEV(track_index)::numeric,  2) AS std,
  MIN(track_index)                        AS min,
  MAX(track_index)                        AS max,
  PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY track_index)::numeric AS p25,
  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY track_index)::numeric AS p50,
  PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY track_index)::numeric AS p75,
  COUNT(*)                                AS cnt
FROM nl_track_speed
WHERE LEFT(race_date, 4) = '2024'
  AND track_index IS NOT NULL
GROUP BY surface
ORDER BY surface;

-- 2. track_index ヒストグラム（芝 surface='T'・2024年）
--    実データ範囲 96-103 に合わせて 0.5 幅バケット
SELECT
  ROUND(FLOOR(track_index / 0.5) * 0.5, 1) AS range_from,
  COUNT(*)                                   AS cnt,
  REPEAT('|', COUNT(*)::int)                 AS bar
FROM nl_track_speed
WHERE LEFT(race_date, 4) = '2024'
  AND track_index IS NOT NULL
  AND surface = 'T'
GROUP BY range_from
ORDER BY range_from;

-- 3. cushion_value vs track_index 相関（芝 surface='T'・2024年）
SELECT
  COUNT(*)                                                AS n,
  ROUND(CORR(cushion_value, track_index)::numeric, 4)     AS corr_cushion_vs_index,
  ROUND(CORR(moisture_index, track_index)::numeric, 4)    AS corr_moisture_vs_index,
  ROUND(AVG(cushion_value)::numeric,   2)                 AS avg_cushion,
  ROUND(STDDEV(cushion_value)::numeric, 2)                AS std_cushion,
  ROUND(AVG(track_index)::numeric,     2)                 AS avg_index,
  ROUND(STDDEV(track_index)::numeric,  2)                 AS std_index
FROM nl_track_speed
WHERE LEFT(race_date, 4) = '2024'
  AND track_index IS NOT NULL
  AND surface = 'T';
