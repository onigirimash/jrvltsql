-- nl_performance に斤量補正カラムを追加（Step 3①）
--
-- futan_dev の読み方:
--   プラス = 基準より軽い斤量（有利）
--   マイナス = 基準より重い斤量（不利）
--   0       = ハンデ/別定戦（斤量は能力調整済みのため補正なし）
--   NULL    = 斤量データなし / 補正計算不可

ALTER TABLE nl_performance
    ADD COLUMN IF NOT EXISTS futan_dev NUMERIC(5,3);

COMMENT ON COLUMN nl_performance.futan_dev IS
    '斤量補正（秒/ハロン）= (基準斤量 - 実際の斤量) × 距離別係数 / ハロン数。ハンデ/別定=0、計算不可=NULL';
