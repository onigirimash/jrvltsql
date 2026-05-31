-- ============================================================
-- per_race_factor 検証 v2（class_codeベース）
-- ============================================================

-- 1. 基本統計（芝・ダート別）
SELECT
  LEFT(trackcd,1)                           AS surf,
  COUNT(*)                                  AS total,
  COUNT(per_race_factor)                    AS with_factor,
  COUNT(*) - COUNT(per_race_factor)         AS no_factor,
  ROUND(STDDEV(per_race_factor)::numeric,3) AS std,
  ROUND(AVG(per_race_factor)::numeric,3)    AS avg,
  MIN(per_race_factor)                      AS min,
  MAX(per_race_factor)                      AS max
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
GROUP BY LEFT(trackcd,1)
ORDER BY surf;

-- 2. クラス別平均（cls1〜cls5 の平均がほぼ同値か検証）
SELECT
  prf_class_group                           AS cls,
  COUNT(*)                                  AS n,
  ROUND(AVG(per_race_factor)::numeric,3)    AS avg_factor,
  ROUND(STDDEV(per_race_factor)::numeric,3) AS std_factor,
  ROUND(MIN(per_race_factor)::numeric,2)    AS min,
  ROUND(MAX(per_race_factor)::numeric,2)    AS max
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
  AND per_race_factor IS NOT NULL
GROUP BY prf_class_group
ORDER BY prf_class_group;

-- 3. no_data 内訳（prf_fallbackがno_dataまたはNULLの件数）
SELECT
  COALESCE(prf_fallback, 'null_no_result') AS fallback_type,
  COUNT(*)                                  AS cnt
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
GROUP BY prf_fallback
ORDER BY cnt DESC;

-- 4. ヒストグラム（芝・2024年）
SELECT
  ROUND(FLOOR(per_race_factor / 0.5) * 0.5, 1) AS range_from,
  COUNT(*)                                        AS cnt,
  REPEAT('|', LEAST(COUNT(*)::int / 3, 50))       AS bar
FROM nl_ra
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1)='1' AND per_race_factor IS NOT NULL
GROUP BY range_from
ORDER BY range_from;
