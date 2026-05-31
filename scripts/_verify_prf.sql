-- ============================================================
-- per_race_factor 検証: ヒストグラム・クラス別平均・分布
-- ============================================================

-- 4-A. ヒストグラム（芝・2024年）
SELECT
  ROUND(FLOOR(per_race_factor / 0.5) * 0.5, 1) AS range_from,
  COUNT(*)                                        AS cnt,
  REPEAT('|', LEAST(COUNT(*)::int, 50))           AS bar
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) = '1'
  AND per_race_factor IS NOT NULL
GROUP BY range_from
ORDER BY range_from;

-- 4-B. クラス別 per_race_factor 平均（クラス間でほぼ同値か検証）
SELECT
  prf_class_group,
  COUNT(*)                                        AS n,
  ROUND(AVG(per_race_factor)::numeric,  3)        AS avg_factor,
  ROUND(STDDEV(per_race_factor)::numeric, 3)      AS std_factor,
  ROUND(MIN(per_race_factor)::numeric,  2)        AS min_factor,
  ROUND(MAX(per_race_factor)::numeric,  2)        AS max_factor
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
  AND per_race_factor IS NOT NULL
GROUP BY prf_class_group
ORDER BY prf_class_group;

-- 4-C. track_index vs per_race_factor: 同一開催日の標準偏差比較
SELECT
  'track_index_daily' AS metric,
  ROUND(STDDEV(track_index)::numeric, 3) AS std,
  COUNT(*) AS n
FROM nl_track_speed
WHERE LEFT(race_date,4)='2024' AND surface='T'
  AND track_index IS NOT NULL
UNION ALL
SELECT
  'per_race_factor' AS metric,
  ROUND(STDDEV(per_race_factor)::numeric, 3) AS std,
  COUNT(*) AS n
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1)='1'
  AND per_race_factor IS NOT NULL;
