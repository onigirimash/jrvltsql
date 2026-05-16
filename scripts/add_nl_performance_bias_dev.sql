-- nl_performance にバイアス補正カラムを追加（Step 3④）
--
-- bias_dev の読み方:
--   マイナス = バイアス不利（内有利×外枠 or 外有利×内枠 or 前有利×差追 or 後有利×逃先）
--   プラス   = バイアス有利（同上の逆）
--   0        = バイアスなし or review_track_bias にデータなし
--   NULL     = 未計算（calc_bias_correction.py 未実行）
--
-- 内外判定：枠番 1-4 = 内、5-8 = 外
-- 前後判定：コーナー順位の相対位置から脚質推定（calc_pace_correction.py と同一ロジック）

ALTER TABLE nl_performance
    ADD COLUMN IF NOT EXISTS bias_dev NUMERIC(5,3);

COMMENT ON COLUMN nl_performance.bias_dev IS
    'バイアス補正（秒/ハロン）= (内外スコア×内外係数 + 前後スコア×前後係数) / ハロン数。バイアスなし=0、未計算=NULL';
