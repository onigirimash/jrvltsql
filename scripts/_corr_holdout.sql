-- ホールドアウト検証用：current_index × 着順相関
-- nl_horse_index の current_index（直前に calc_current_index.py で更新済み）を使用

-- 訓練: 2021-2023
SELECT '2021-2023' AS period,
    ROUND(CORR(hi.current_index, se.kakuteijyuni)::numeric, 4) AS corr,
    COUNT(*) AS n
FROM nl_se se
JOIN nl_ra ra ON ra.year=se.year AND ra.monthday=se.monthday
             AND ra.jyocd=se.jyocd AND ra.racenum=se.racenum
JOIN nl_horse_index hi
    ON hi.kettonum = TRIM(se.kettonum)
   AND hi.distance_cat = CASE WHEN ra.kyori<=1400 THEN 'S'
                              WHEN ra.kyori<=1800 THEN 'M'
                              WHEN ra.kyori<=2200 THEN 'I' ELSE 'L' END
   AND hi.surface = CASE LEFT(ra.trackcd,1) WHEN '1' THEN 'T'
                                            WHEN '2' THEN 'D' ELSE 'J' END
WHERE se.year BETWEEN 2021 AND 2023
  AND se.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni BETWEEN 1 AND 18
  AND LEFT(ra.trackcd,1) IN ('1','2')
  AND hi.current_index IS NOT NULL

UNION ALL

-- 検証: 2024
SELECT '2024' AS period,
    ROUND(CORR(hi.current_index, se.kakuteijyuni)::numeric, 4) AS corr,
    COUNT(*) AS n
FROM nl_se se
JOIN nl_ra ra ON ra.year=se.year AND ra.monthday=se.monthday
             AND ra.jyocd=se.jyocd AND ra.racenum=se.racenum
JOIN nl_horse_index hi
    ON hi.kettonum = TRIM(se.kettonum)
   AND hi.distance_cat = CASE WHEN ra.kyori<=1400 THEN 'S'
                              WHEN ra.kyori<=1800 THEN 'M'
                              WHEN ra.kyori<=2200 THEN 'I' ELSE 'L' END
   AND hi.surface = CASE LEFT(ra.trackcd,1) WHEN '1' THEN 'T'
                                            WHEN '2' THEN 'D' ELSE 'J' END
WHERE se.year = 2024
  AND se.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni BETWEEN 1 AND 18
  AND LEFT(ra.trackcd,1) IN ('1','2')
  AND hi.current_index IS NOT NULL;
