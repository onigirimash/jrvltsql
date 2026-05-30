-- nl_target_race のデータを nl_ra へ反映する
-- 【1】laptime 更新（ハイフン区切り形式）
-- 【2】sibababacd / dirtbabacd 補完（不良馬場）

-- ── キー変換メモ ────────────────────────────────────────
-- nl_ra.year::text || LPAD(nl_ra.monthday::text, 4, '0')
--   2025, 507 → '20250507'
-- nl_ra.jyocd = nl_target_race.jyo_cd  (例: '05')
-- ─────────────────────────────────────────────────────────

-- ────────────────────────────────────────────────────────
-- 【1】laptime 更新
-- ────────────────────────────────────────────────────────
UPDATE nl_ra ra
SET laptime = (
    SELECT CONCAT_WS('-',
        lap01::text, lap02::text, lap03::text, lap04::text, lap05::text,
        lap06::text, lap07::text, lap08::text, lap09::text, lap10::text,
        lap11::text, lap12::text, lap13::text, lap14::text, lap15::text
    )
    FROM nl_target_race tr
    WHERE tr.race_date = ra.year::text || LPAD(ra.monthday::text, 4, '0')
      AND tr.jyo_cd    = ra.jyocd
      AND tr.racenum   = ra.racenum
)
WHERE EXISTS (
    SELECT 1
    FROM nl_target_race tr
    WHERE tr.race_date = ra.year::text || LPAD(ra.monthday::text, 4, '0')
      AND tr.jyo_cd    = ra.jyocd
      AND tr.racenum   = ra.racenum
      AND tr.lap01 IS NOT NULL   -- ラップが存在する行のみ
);

-- ────────────────────────────────────────────────────────
-- 【2-A】sibababacd 補完（芝・障害の不良馬場）
--   nl_ra.sibababacd = '0'（未設定）かつ nl_target_race.baba_state = '不'
--   かつ surface IN ('T','J')
-- ────────────────────────────────────────────────────────
UPDATE nl_ra ra
SET sibababacd = '4'
FROM nl_target_race tr
WHERE tr.race_date    = ra.year::text || LPAD(ra.monthday::text, 4, '0')
  AND tr.jyo_cd       = ra.jyocd
  AND tr.racenum      = ra.racenum
  AND tr.baba_state   = '不'
  AND tr.surface      IN ('T', 'J')
  AND ra.sibababacd   = '0';

-- ────────────────────────────────────────────────────────
-- 【2-B】dirtbabacd 補完（ダートの不良馬場）
-- ────────────────────────────────────────────────────────
UPDATE nl_ra ra
SET dirtbabacd = '4'
FROM nl_target_race tr
WHERE tr.race_date    = ra.year::text || LPAD(ra.monthday::text, 4, '0')
  AND tr.jyo_cd       = ra.jyocd
  AND tr.racenum      = ra.racenum
  AND tr.baba_state   = '不'
  AND tr.surface      = 'D'
  AND ra.dirtbabacd   = '0';
