-- 前走脚質 × 今走PCI × 単勝回収率（芝2022-2024）
-- TARGET脚質を統合グループにマッピング:
--   逃げ       → nige
--   先行・まくり → senkou_kei
--   差し・中団  → chudan_kei
--   追込・後方  → koho_kei

WITH horse_races AS (
  SELECT
    se.kettonum,
    se.year,
    se.jyocd,
    se.kaiji,
    se.nichiji,
    se.racenum,
    se.kakuteijyuni,
    se.odds,
    se.target_kyakushitsu,
    ra.trackcd,
    ra.year::text || LPAD(ra.monthday::text, 4, '0') AS race_date_str,
    ra.year * 10000 + ra.monthday                    AS race_sort
  FROM nl_se se
  JOIN nl_ra ra
    ON se.year    = ra.year
   AND se.jyocd   = ra.jyocd
   AND se.kaiji   = ra.kaiji
   AND se.nichiji = ra.nichiji
   AND se.racenum = ra.racenum
  WHERE se.jyocd BETWEEN '01' AND '10'
    AND se.year BETWEEN 2021 AND 2024
    AND se.target_kyakushitsu IS NOT NULL
    AND se.target_kyakushitsu <> ''
),

with_prev AS (
  SELECT
    *,
    LAG(target_kyakushitsu) OVER (
      PARTITION BY kettonum
      ORDER BY race_sort, jyocd, racenum
    ) AS prev_kyakushitsu
  FROM horse_races
),

classified AS (
  SELECT
    *,
    CASE prev_kyakushitsu
      WHEN '逃げ'   THEN 'nige'
      WHEN '先行'   THEN 'senkou_kei'
      WHEN 'まくり' THEN 'senkou_kei'
      WHEN '差し'   THEN 'chudan_kei'
      WHEN '中団'   THEN 'chudan_kei'
      WHEN '追込'   THEN 'koho_kei'
      WHEN '後方'   THEN 'koho_kei'
    END AS prev_style_group
  FROM with_prev
)

SELECT
  c.prev_style_group                                AS prev_style,
  CASE
    WHEN tr.race_pci < 45 THEN '1_lt45(超ハイ)'
    WHEN tr.race_pci < 50 THEN '2_45-49(ハイ)'
    WHEN tr.race_pci < 53 THEN '3_50-52(平均)'
    WHEN tr.race_pci < 57 THEN '4_53-56(ミドル)'
    ELSE                       '5_57+(スロー)'
  END                                               AS pci_band,
  COUNT(*)                                          AS cnt,
  SUM(CASE WHEN c.kakuteijyuni = 1 THEN 1 ELSE 0 END) AS wins,
  ROUND(
    100.0 * SUM(CASE WHEN c.kakuteijyuni = 1 THEN 1 ELSE 0 END)
    / COUNT(*), 1
  )                                                 AS win_pct,
  ROUND(
    (SUM(CASE WHEN c.kakuteijyuni = 1 THEN c.odds ELSE 0 END)
     / COUNT(*)::numeric * 100)::numeric,
    1
  )                                                 AS tan_roi_pct
FROM classified c
JOIN nl_target_race tr
  ON c.race_date_str = tr.race_date
 AND c.jyocd         = tr.jyo_cd
 AND c.racenum       = tr.racenum
WHERE c.year BETWEEN 2022 AND 2024
  AND c.trackcd BETWEEN '10' AND '22'    -- 芝
  AND c.prev_kyakushitsu IS NOT NULL
  AND c.prev_style_group IS NOT NULL
  AND tr.race_pci IS NOT NULL
GROUP BY c.prev_style_group, pci_band
ORDER BY c.prev_style_group, pci_band;
