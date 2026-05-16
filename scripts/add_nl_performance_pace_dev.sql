-- nl_performance に展開補正カラムを追加（Step 3③）
--
-- pace_dev の読み方:
--   マイナス = ペース適性外（ハイペース×逃先 or スロー×差追）
--   0        = ペース中立 or PCI/脚質が算出不可
--   NULL     = 未計算（calc_pace_correction.py 未実行）
--
-- PCI（Pace Change Index）= 上がり3F ÷ (前半Ave-3F + 上がり3F) × 100
--   PCI < 47 : ハイペース
--   PCI 47-53: ミドルペース
--   PCI > 53 : スロー

ALTER TABLE nl_performance
    ADD COLUMN IF NOT EXISTS pace_dev NUMERIC(5,3);

COMMENT ON COLUMN nl_performance.pace_dev IS
    '展開補正（秒/ハロン）= -(PCIと基準値の差 × 0.02) / ハロン数。ハイペース×逃先orスロー×差追のみマイナス、それ以外=0';
