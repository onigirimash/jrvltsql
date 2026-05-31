-- per_race_factorのサンプル数チェック
-- グループ定義:
--   cls1: 010(新馬),005(未勝利)
--   cls2: 703(1勝・2勝)
--   cls3: 701(3勝・OP), gradecd=L
--   cls4: gradecd=C (G3)
--   cls5: gradecd=A,B (G1・G2)

WITH races AS (
  SELECT
    ra.jyocd,
    ra.kyori,
    CASE WHEN LEFT(ra.trackcd,1) = '1' THEN 'T'
         WHEN LEFT(ra.trackcd,1) = '2' THEN 'D'
         ELSE 'J' END AS surface,
    CASE
      WHEN ra.gradecd IN ('A','B')    THEN 'cls5'
      WHEN ra.gradecd = 'C'           THEN 'cls4'
      WHEN ra.gradecd = 'L'           THEN 'cls3'
      WHEN COALESCE(NULLIF(ra.jyokencd1,'000'),NULLIF(ra.jyokencd2,'000'),
                    NULLIF(ra.jyokencd3,'000'),NULLIF(ra.jyokencd4,'000'),
                    NULLIF(ra.jyokencd5,'000'),'OP') IN ('010','005')
                                      THEN 'cls1'
      WHEN COALESCE(NULLIF(ra.jyokencd1,'000'),NULLIF(ra.jyokencd2,'000'),
                    NULLIF(ra.jyokencd3,'000'),NULLIF(ra.jyokencd4,'000'),
                    NULLIF(ra.jyokencd5,'000'),'OP') = '703'
                                      THEN 'cls2'
      WHEN COALESCE(NULLIF(ra.jyokencd1,'000'),NULLIF(ra.jyokencd2,'000'),
                    NULLIF(ra.jyokencd3,'000'),NULLIF(ra.jyokencd4,'000'),
                    NULLIF(ra.jyokencd5,'000'),'OP') = '701'
                                      THEN 'cls3'
      ELSE 'cls3'
    END AS class_group,
    -- 上位3頭平均タイム（秒換算）
    AVG(FLOOR(se.time / 100) * 60 + MOD(se.time::numeric, 100))
      FILTER (WHERE se.kakuteijyuni <= 3) AS top3_sec
  FROM nl_ra ra
  JOIN nl_se se
    ON ra.year=se.year AND ra.monthday=se.monthday AND ra.jyocd=se.jyocd
   AND ra.kaiji=se.kaiji AND ra.nichiji=se.nichiji AND ra.racenum=se.racenum
  WHERE ra.jyocd BETWEEN '01' AND '10'
    AND LEFT(ra.trackcd,1) IN ('1','2')
    AND ra.year BETWEEN 2019 AND 2023   -- 過去5年（基準データ）
    AND se.time > 0 AND se.kakuteijyuni >= 1
  GROUP BY ra.jyocd, ra.kyori, ra.trackcd, ra.gradecd,
           ra.jyokencd1,ra.jyokencd2,ra.jyokencd3,ra.jyokencd4,ra.jyokencd5,
           ra.year, ra.monthday, ra.racenum
  HAVING COUNT(*) FILTER (WHERE se.kakuteijyuni <= 3) = 3
)

-- サンプル数集計: jyocd × surface × kyori × class_group
SELECT
  surface, class_group, kyori,
  COUNT(*) AS sample_count,
  CASE WHEN COUNT(*) >= 30 THEN 'OK' ELSE 'LOW' END AS status
FROM races
GROUP BY surface, class_group, kyori
ORDER BY surface, class_group, kyori;
