-- Update nl_ra.haron3l / haron4l from nl_se.harontimel3 / harontimel4
-- Uses MIN (fastest) harontimel3 per race from nl_se

WITH se_agg AS (
    SELECT
        year, monthday, jyocd, kaiji, nichiji, racenum,
        MIN(harontimel3) FILTER (WHERE harontimel3 > 0) AS min_h3l,
        MIN(harontimel4) FILTER (WHERE harontimel4 > 0) AS min_h4l
    FROM nl_se
    GROUP BY year, monthday, jyocd, kaiji, nichiji, racenum
)
UPDATE nl_ra ra
SET
    haron3l = se_agg.min_h3l,
    haron4l = COALESCE(se_agg.min_h4l, ra.haron4l)
FROM se_agg
WHERE ra.year     = se_agg.year
  AND ra.monthday = se_agg.monthday
  AND ra.jyocd    = se_agg.jyocd
  AND ra.kaiji    = se_agg.kaiji
  AND ra.nichiji  = se_agg.nichiji
  AND ra.racenum  = se_agg.racenum
  AND se_agg.min_h3l IS NOT NULL;
