-- nl_target_race（公式記録）で nl_ra の馬場状態コードを全面上書き
-- WF7「1日1値」制約 + コード変換バグの最終修正
--
-- 変換テーブル: TARGET文字列 → JV-Dataコード
--   良 → '1'  稍 → '2'  重 → '3'  不 → '4'
--
-- 対象: year >= 2021 かつ JRA場コード ('01'-'10')

-- ── 芝・障害レース: sibababacd を更新 ──────────────────────────
UPDATE nl_ra ra
SET sibababacd = CASE tr.baba_state
    WHEN '良' THEN '1'
    WHEN '稍' THEN '2'
    WHEN '重' THEN '3'
    WHEN '不' THEN '4'
    ELSE ra.sibababacd
END
FROM nl_target_race tr
WHERE tr.race_date = ra.year::text || LPAD(ra.monthday::text, 4, '0')
  AND tr.jyo_cd   = ra.jyocd
  AND tr.racenum  = ra.racenum
  AND tr.surface  IN ('T', 'J')
  AND ra.year     >= 2021
  AND ra.jyocd    BETWEEN '01' AND '10';

-- ── ダートレース: dirtbabacd を更新 ───────────────────────────
UPDATE nl_ra ra
SET dirtbabacd = CASE tr.baba_state
    WHEN '良' THEN '1'
    WHEN '稍' THEN '2'
    WHEN '重' THEN '3'
    WHEN '不' THEN '4'
    ELSE ra.dirtbabacd
END
FROM nl_target_race tr
WHERE tr.race_date = ra.year::text || LPAD(ra.monthday::text, 4, '0')
  AND tr.jyo_cd   = ra.jyocd
  AND tr.racenum  = ra.racenum
  AND tr.surface  = 'D'
  AND ra.year     >= 2021
  AND ra.jyocd    BETWEEN '01' AND '10';
