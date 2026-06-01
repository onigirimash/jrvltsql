-- ABテスト用: adjusted_index × log(odds) 相関 + std（2024年）
SELECT
    ROUND(CORR(rp.adjusted_index, LN(se.odds))::numeric,     4) AS adj_x_ln_odds,
    ROUND(CORR(rp.adjusted_index, se.kakuteijyuni)::numeric,  4) AS adj_x_rank,
    ROUND(STDDEV(rp.adjusted_index)::numeric,                 3) AS adj_std,
    ROUND(AVG(rp.adjusted_index)::numeric,                    3) AS adj_avg,
    COUNT(*) AS n
FROM nl_race_prediction rp
JOIN nl_se se
  ON se.year=rp.year AND se.monthday=rp.monthday
 AND se.jyocd=rp.jyocd AND se.racenum=rp.racenum
 AND se.umaban=rp.umaban
WHERE rp.year=2024
  AND rp.jyocd BETWEEN '01' AND '10'
  AND rp.adjusted_index IS NOT NULL
  AND se.odds > 0
  AND se.kakuteijyuni BETWEEN 1 AND 18;
