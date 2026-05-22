-- Step 1: Clear outlier haron3l values (< 28s or > 60s are invalid for 600m section)
UPDATE nl_ra
SET haron3l = NULL
WHERE haron3l > 0 AND (haron3l < 28 OR haron3l > 60);

-- Step 2: Re-update nl_ra.haron3l / haron4l from nl_se with valid range filter
WITH se_agg AS (
    SELECT
        year, monthday, jyocd, kaiji, nichiji, racenum,
        MIN(harontimel3) FILTER (WHERE harontimel3 >= 28 AND harontimel3 <= 60) AS min_h3l,
        MIN(harontimel4) FILTER (WHERE harontimel4 >= 40 AND harontimel4 <= 80) AS min_h4l
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

-- Result check
SELECT
    COUNT(*) AS total,
    COUNT(CASE WHEN haron3l > 0 THEN 1 END) AS haron3l_ok,
    COUNT(CASE WHEN haron3l IS NULL OR haron3l = 0 THEN 1 END) AS haron3l_zero,
    ROUND(MIN(haron3l) FILTER (WHERE haron3l > 0)::numeric, 2) AS min_h3l,
    ROUND(AVG(haron3l) FILTER (WHERE haron3l > 0)::numeric, 2) AS avg_h3l,
    ROUND(MAX(haron3l) FILTER (WHERE haron3l > 0)::numeric, 2) AS max_h3l
FROM nl_ra;
