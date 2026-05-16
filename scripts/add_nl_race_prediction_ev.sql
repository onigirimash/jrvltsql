-- nl_race_prediction にオッズ・期待値カラムを追加（Step 10）
--
-- 計算式:
--   expected_value = win_prob × odds - 1
--   is_recommended = expected_value > ev_threshold
--
-- odds の取得元:
--   nl_o1.tanodds（単勝オッズ）
--   NULL の場合は expected_value / is_recommended も NULL
--
-- ev_threshold:
--   DEFAULT 0.15（計算スクリプト実行時に上書き可）

ALTER TABLE nl_race_prediction
    ADD COLUMN IF NOT EXISTS odds            NUMERIC(7,1),
    ADD COLUMN IF NOT EXISTS expected_value  NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS ev_threshold    NUMERIC(5,3) NOT NULL DEFAULT 0.15,
    ADD COLUMN IF NOT EXISTS is_recommended  BOOLEAN;

COMMENT ON COLUMN nl_race_prediction.odds           IS '単勝オッズ（nl_o1.tanodds、取得不可時は NULL）';
COMMENT ON COLUMN nl_race_prediction.expected_value IS '期待値 = win_prob × odds - 1（NULL=オッズなし）';
COMMENT ON COLUMN nl_race_prediction.ev_threshold   IS '買い推奨閾値（デフォルト 0.15）';
COMMENT ON COLUMN nl_race_prediction.is_recommended IS '買い推奨フラグ（expected_value > ev_threshold）';
