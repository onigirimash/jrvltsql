-- pace_bias 導入後の相関係数確認（2024年）

-- 1. 相関係数（adjusted_index × 着順 / log(odds)）
SELECT
    ROUND(CORR(rp.adjusted_index, se.kakuteijyuni)::numeric,  4) AS adj_x_rank,
    ROUND(CORR(rp.adjusted_index, LN(se.odds))::numeric,      4) AS adj_x_ln_odds,
    ROUND(STDDEV(rp.adjusted_index)::numeric,                  3) AS adj_std,
    COUNT(*) AS n
FROM nl_race_prediction rp
JOIN nl_se se
  ON se.year=rp.year AND se.monthday=rp.monthday
 AND se.jyocd=rp.jyocd AND se.racenum=rp.racenum AND se.umaban=rp.umaban
WHERE rp.year=2024 AND rp.jyocd BETWEEN '01' AND '10'
  AND rp.adjusted_index IS NOT NULL
  AND se.odds > 0 AND se.kakuteijyuni BETWEEN 1 AND 18;

-- 2. ペース補正が入った馬の件数と内訳
SELECT
    pace_bias_score,
    COUNT(*) AS cnt
FROM nl_race_prediction
WHERE year=2024 AND jyocd BETWEEN '01' AND '10'
GROUP BY pace_bias_score
ORDER BY pace_bias_score;

-- 3. ペース補正あり/なし別の的中率（1着を当てた割合）
SELECT
    CASE WHEN rp.pace_bias_score IS NULL THEN 'no_bias'
         WHEN rp.pace_bias_score > 0     THEN 'positive'
         ELSE                                 'negative'
    END AS bias_type,
    COUNT(*) AS horses,
    SUM(CASE WHEN se.kakuteijyuni=1 THEN 1 ELSE 0 END) AS wins,
    ROUND(100.0*SUM(CASE WHEN se.kakuteijyuni=1 THEN 1 ELSE 0 END)/COUNT(*),2) AS win_pct,
    ROUND(AVG(rp.win_prob*100)::numeric, 3) AS avg_win_prob_pct
FROM nl_race_prediction rp
JOIN nl_se se
  ON se.year=rp.year AND se.monthday=rp.monthday
 AND se.jyocd=rp.jyocd AND se.racenum=rp.racenum AND se.umaban=rp.umaban
WHERE rp.year=2024 AND rp.jyocd BETWEEN '01' AND '10'
  AND se.kakuteijyuni BETWEEN 1 AND 18
GROUP BY bias_type ORDER BY bias_type;
