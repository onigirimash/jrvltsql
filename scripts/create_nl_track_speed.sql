-- nl_track_speed: 馬場指数テーブル
-- 開催日 × 競馬場 × 走路(芝/ダート/障害) ごとの馬場速度指数を格納する。
--
-- track_index の読み方:
--   100   = 過去5年平均と同等
--   > 100 = 平均より速い馬場（良・乾燥傾向）
--   < 100 = 平均より遅い馬場（重・稍重傾向）
--
-- surface コード:
--   'T' = 芝  (trackcd LIKE '1%')
--   'D' = ダート (trackcd LIKE '2%')
--   'J' = 障害  (trackcd LIKE '5%' or '6%')

CREATE TABLE IF NOT EXISTS nl_track_speed (
    race_date   CHAR(8)      NOT NULL,          -- YYYYMMDD
    jyocd       CHAR(2)      NOT NULL,          -- 競馬場コード 01-10
    surface     CHAR(1)      NOT NULL,          -- 'T' / 'D' / 'J'
    track_index NUMERIC(7,2),                   -- 馬場指数（基準=100）
    race_count  SMALLINT     NOT NULL DEFAULT 0, -- 計算に使ったレース数
    fallback    BOOLEAN      NOT NULL DEFAULT FALSE, -- TRUE=前日値継承
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (race_date, jyocd, surface)
);

CREATE INDEX IF NOT EXISTS idx_nl_track_speed_date
    ON nl_track_speed (race_date);

CREATE INDEX IF NOT EXISTS idx_nl_track_speed_jyo_surf
    ON nl_track_speed (jyocd, surface, race_date);

COMMENT ON TABLE nl_track_speed IS
    '馬場指数テーブル: 開催日×競馬場×走路別の走破タイム乖離率（100=平均）';
COMMENT ON COLUMN nl_track_speed.track_index IS
    '馬場指数: 過去5年同条件平均タイム / 当日平均タイム × 100';
COMMENT ON COLUMN nl_track_speed.race_count IS
    '指数計算に使用したレース数（3未満はフォールバック対象）';
COMMENT ON COLUMN nl_track_speed.fallback IS
    'TRUE: 当日対象レース数が3未満のため直近の前日値を継承';
