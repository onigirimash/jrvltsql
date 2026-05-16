-- nl_horse_index に信頼度カラムを追加（Step 7）
--
-- reliability の計算:
--   1戦  → 0.3
--   3戦  → 0.6
--   5戦  → 0.8
--   10戦 → 1.0  （以降は 1.0 固定）
--   ※中間値は区間ごとに線形補間
--
-- adjusted_index の計算:
--   adjusted_index = current_index × reliability
--   ※縮約先は 0（current_index が perf_index スケールのため）
--   ※current_index が NULL の場合は NULL

ALTER TABLE nl_horse_index
    ADD COLUMN IF NOT EXISTS reliability     NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS adjusted_index  NUMERIC(6,3);

COMMENT ON COLUMN nl_horse_index.reliability IS
    '信頼度（0.3〜1.0）: 出走回数から線形補間 1戦=0.3 / 3戦=0.6 / 5戦=0.8 / 10戦以上=1.0';
COMMENT ON COLUMN nl_horse_index.adjusted_index IS
    '信頼度補正後指数（秒/ハロン）= current_index × reliability';
