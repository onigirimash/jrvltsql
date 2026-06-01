-- Step4〜Step7の各段階での着順相関（2024年）
-- 注: norm_index / current_index は nl_horse_index の現在値（最新スナップショット）
--     adjusted_index は nl_race_prediction の予測時点値（per-race snapshot）

WITH base AS (
    SELECT
        se.kakuteijyuni,
        se.year,
        se.monthday,
        se.jyocd,
        se.racenum,
        se.umaban,
        TRIM(se.kettonum) AS kettonum,
        ra.kyori,
        CASE LEFT(ra.trackcd,1) WHEN '1' THEN 'T' WHEN '2' THEN 'D' ELSE 'J' END AS surface,
        CASE WHEN ra.kyori <= 1400 THEN 'S'
             WHEN ra.kyori <= 1800 THEN 'M'
             WHEN ra.kyori <= 2200 THEN 'I'
             ELSE 'L' END AS dist_cat,
        -- Step4: perf_index（nl_performance：レース単位スナップショット）
        p.perf_index,
        -- Step5/6: norm_index / current_index（nl_horse_index：現在値）
        hi.norm_index,
        hi.current_index,
        -- Step7: adjusted_index（nl_race_prediction：予測時点値）
        rp.adjusted_index
    FROM nl_se se
    JOIN nl_ra ra
      ON ra.year=se.year AND ra.monthday=se.monthday
     AND ra.jyocd=se.jyocd AND ra.racenum=se.racenum
    LEFT JOIN nl_performance p
      ON p.year=se.year AND p.monthday=se.monthday
     AND p.jyocd=se.jyocd AND p.racenum::int=se.racenum
     AND p.umaban::int=se.umaban
    LEFT JOIN nl_horse_index hi
      ON hi.kettonum = TRIM(se.kettonum)
     AND hi.distance_cat = CASE WHEN ra.kyori<=1400 THEN 'S'
                                WHEN ra.kyori<=1800 THEN 'M'
                                WHEN ra.kyori<=2200 THEN 'I'
                                ELSE 'L' END
     AND hi.surface = CASE LEFT(ra.trackcd,1) WHEN '1' THEN 'T'
                                              WHEN '2' THEN 'D' ELSE 'J' END
    LEFT JOIN nl_race_prediction rp
      ON rp.year=se.year AND rp.monthday=se.monthday
     AND rp.jyocd=se.jyocd AND rp.racenum=se.racenum
     AND rp.umaban=se.umaban
    WHERE se.year=2024
      AND se.jyocd BETWEEN '01' AND '10'
      AND se.kakuteijyuni BETWEEN 1 AND 18
      AND LEFT(ra.trackcd,1) IN ('1','2')
)

-- 各ステップの相関係数
SELECT
    COUNT(*) AS n,
    -- Step4: perf_index（馬場補正済み時間偏差）
    ROUND(CORR(perf_index,     kakuteijyuni)::numeric, 4) AS step4_perf_x_rank,
    COUNT(perf_index)                                     AS step4_n,
    -- Step5: norm_index（スケール正規化後のキャリア指数）
    ROUND(CORR(norm_index,     kakuteijyuni)::numeric, 4) AS step5_norm_x_rank,
    COUNT(norm_index)                                     AS step5_n,
    -- Step6: current_index（直近×0.4 + ベスト×0.6 の時系列合成）
    ROUND(CORR(current_index,  kakuteijyuni)::numeric, 4) AS step6_curr_x_rank,
    COUNT(current_index)                                  AS step6_n,
    -- Step7: adjusted_index（信頼度補正後の最終指数）
    ROUND(CORR(adjusted_index, kakuteijyuni)::numeric, 4) AS step7_adj_x_rank,
    COUNT(adjusted_index)                                 AS step7_n
FROM base;
