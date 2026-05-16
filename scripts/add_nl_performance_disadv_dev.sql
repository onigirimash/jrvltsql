-- nl_performance に個馬補正カラムを追加（Step 3②）
--
-- disadv_dev の読み方:
--   プラス = 不利を受けた（補正加点）
--   0      = 不利なし（review_disadvantage にデータなし）
--   NULL   = 未計算（calc_disadv_correction.py 未実行）

ALTER TABLE nl_performance
    ADD COLUMN IF NOT EXISTS disadv_dev NUMERIC(5,3);

COMMENT ON COLUMN nl_performance.disadv_dev IS
    '個馬補正（秒/ハロン）= Σ(程度 × 不利種別係数) / ハロン数。不利なし=0、未計算=NULL';
