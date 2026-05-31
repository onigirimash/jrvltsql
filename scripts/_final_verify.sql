-- std / no_data / class avg final check

SELECT 'std_turf_2022_2025' AS metric,
  ROUND(STDDEV(per_race_factor)::numeric,3) AS std,
  ROUND(AVG(per_race_factor)::numeric,3)    AS avg,
  COUNT(*) AS n
FROM nl_ra
WHERE year BETWEEN 2022 AND 2025
  AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1)='1' AND per_race_factor IS NOT NULL;

SELECT prf_fallback AS fb_type, COUNT(*) AS cnt
FROM nl_ra
WHERE year BETWEEN 2021 AND 2026
  AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
GROUP BY prf_fallback ORDER BY cnt DESC;

SELECT prf_class_group AS cls,
  COUNT(*) AS n,
  ROUND(AVG(per_race_factor)::numeric,3) AS avg_factor,
  ROUND(STDDEV(per_race_factor)::numeric,3) AS std_factor
FROM nl_ra
WHERE year BETWEEN 2022 AND 2025
  AND jyocd BETWEEN '01' AND '10'
  AND LEFT(trackcd,1) IN ('1','2')
  AND per_race_factor IS NOT NULL
GROUP BY prf_class_group ORDER BY prf_class_group;
