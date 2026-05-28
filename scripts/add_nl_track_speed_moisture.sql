-- nl_track_speed: 含水率・クッション値カラムを追加
-- nl_baba_moisture から開催日×競馬場でJOINして値を反映する

ALTER TABLE nl_track_speed
ADD COLUMN IF NOT EXISTS cushion_value NUMERIC(4,1),
ADD COLUMN IF NOT EXISTS turf_moisture NUMERIC(4,1),
ADD COLUMN IF NOT EXISTS dirt_moisture NUMERIC(4,1);

COMMENT ON COLUMN nl_track_speed.cushion_value IS '芝クッション値 (nl_baba_moisture より)';
COMMENT ON COLUMN nl_track_speed.turf_moisture IS '芝含水率 平均% = (ゴール前+4C)/2 (nl_baba_moisture より)';
COMMENT ON COLUMN nl_track_speed.dirt_moisture IS 'ダート含水率 平均% = (ゴール前+4C)/2 (nl_baba_moisture より)';

-- 既存データへの反映（再実行可）
UPDATE nl_track_speed ts
SET
  cushion_value = bm.cushion_value,
  turf_moisture = ROUND((bm.turf_moisture_goal + bm.turf_moisture_4corner) / 2, 1),
  dirt_moisture = ROUND((bm.dirt_moisture_goal + bm.dirt_moisture_4corner) / 2, 1)
FROM nl_baba_moisture bm
WHERE ts.race_date = bm.race_date
  AND ts.jyocd = bm.jyo_cd;
