-- per_race_factor 導入後の相関係数確認（2024年）
-- 修正前: perf×log(odds)=-0.3164 / adj×log(odds)=-0.2722 / perf×着順=-0.4437

WITH base AS (
    SELECT
        p.perf_index,
        rp.adjusted_index,
        se.odds,
        se.kakuteijyuni,
        ra.per_race_factor,
        p.time_dev
    FROM nl_performance p
    JOIN nl_se se
      ON se.year=p.year AND se.monthday=p.monthday
     AND se.jyocd=p.jyocd AND se.racenum::int=p.racenum::int
     AND se.umaban=p.umaban::int
    JOIN nl_ra ra
      ON ra.year=p.year AND ra.monthday=p.monthday
     AND ra.jyocd=p.jyocd AND ra.racenum=p.racenum::int
    LEFT JOIN nl_race_prediction rp
      ON rp.year=p.year AND rp.monthday=p.monthday
     AND rp.jyocd=p.jyocd AND rp.racenum=p.racenum::int
     AND rp.umaban=p.umaban::int
    WHERE p.year=2024
      AND p.jyocd BETWEEN '01' AND '10'
      AND p.perf_index IS NOT NULL
      AND se.odds > 0
      AND se.kakuteijyuni BETWEEN 1 AND 18
)

SELECT
    COUNT(*)                                                    AS n,

    -- perf_index 相関
    ROUND(CORR(perf_index, LN(odds))::numeric,         4)      AS perf_x_ln_odds,
    ROUND(CORR(perf_index, kakuteijyuni)::numeric,     4)      AS perf_x_rank,

    -- adjusted_index 相関
    ROUND(CORR(adjusted_index, LN(odds))::numeric,     4)      AS adj_x_ln_odds,
    ROUND(CORR(adjusted_index, kakuteijyuni)::numeric, 4)      AS adj_x_rank,

    -- per_race_factor 自体の相関（参考）
    ROUND(CORR(per_race_factor, perf_index)::numeric,  4)      AS prf_x_perf,
    ROUND(CORR(per_race_factor, time_dev)::numeric,    4)      AS prf_x_timedev
FROM base;
