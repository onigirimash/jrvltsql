-- nl_performance にパフォーマンス指数カラムを追加（Step 4）
--
-- perf_index の読み方:
--   プラス  = 平均より高いパフォーマンス
--   マイナス = 平均より低いパフォーマンス
--   単位: 秒/ハロン（各補正項と同一スケール）
--
-- 計算式:
--   perf_index = time_dev × (track_index / 100)
--              + futan_dev + disadv_dev + pace_dev + bias_dev
--
-- track_index が取得できない場合は 100（補正なし）として計算

ALTER TABLE nl_performance
    ADD COLUMN IF NOT EXISTS perf_index NUMERIC(7,4);

COMMENT ON COLUMN nl_performance.perf_index IS
    'パフォーマンス指数（秒/ハロン）= time_dev×(track_index/100) + futan_dev + disadv_dev + pace_dev + bias_dev';
