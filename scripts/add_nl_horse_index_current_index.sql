-- nl_horse_index に current_index カラムを追加（Step 6）
--
-- current_index の算出式:
--   直近5走加重平均(exp(-経過日数/180)) × 0.7
--   + キャリアベスト3走平均（直近2年以内）× 0.3
--
-- ※ perf_index（秒/ハロン）スケールで格納
-- ※ 同一 (distance_cat, surface) 内のレースのみ対象
-- ※ 直近5走未満の場合は得られた走数で加重平均
-- ※ キャリアベスト対象外（直近2年以内のレースなし）の場合は直近5走加重平均のみ

ALTER TABLE nl_horse_index
    ADD COLUMN IF NOT EXISTS current_index NUMERIC(6,3);

COMMENT ON COLUMN nl_horse_index.current_index IS
    '時系列補正後の現在実力指数（秒/ハロン）= 直近5走加重平均(exp(-日数/180))×0.7 + キャリアベスト3走平均(直近2年)×0.3';
