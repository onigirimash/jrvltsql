-- nl_performance: タイム偏差テーブル（実力点数化 Step 2）
--
-- time_dev の読み方:
--   プラス = 基準より速い（良パフォーマンス）
--   マイナス = 基準より遅い（低パフォーマンス）
--   単位: 秒/ハロン（馬場条件・距離を除去した純粋なタイム偏差）
--
-- 基準タイム = 過去5年・同競馬場×同距離×同走路×同クラスの平均走破タイム
-- 馬場状態はStep1（nl_track_speed）で別途補正するため含めない
--
-- sample_cnt < 10 かつ interp=TRUE の場合:
--   距離±200mのハロン単価（秒/ハロン）から対象距離へスケールして補完

CREATE TABLE IF NOT EXISTS nl_performance (
    -- レース識別キー（nl_se と同一体系）
    year        INT          NOT NULL,   -- 開催年
    monthday    INT          NOT NULL,   -- 開催月日 MMDD（例: 503 = 5月3日）
    jyocd       CHAR(2)      NOT NULL,   -- 競馬場コード 01-10
    kaiji       CHAR(2)      NOT NULL,   -- 開催回次
    nichiji     CHAR(2)      NOT NULL,   -- 開催日次
    racenum     CHAR(2)      NOT NULL,   -- レース番号
    umaban      CHAR(2)      NOT NULL,   -- 馬番
    -- レース条件（非正規化：nl_ra を再結合しなくてもよいよう保持）
    kyori       SMALLINT,                -- 距離（m）
    surface     CHAR(1),                 -- 走路: T=芝 / D=ダート / J=障害
    race_class  VARCHAR(10),             -- クラス（A/B/C/E/jyokencd/OP）
    -- タイム偏差
    run_sec     NUMERIC(8,3),            -- 走破タイム（秒）
    base_sec    NUMERIC(8,3),            -- 基準タイム（秒）
    furlongs    NUMERIC(5,2),            -- ハロン数（kyori / 200.0）
    time_dev    NUMERIC(6,3),            -- タイム偏差（秒/ハロン）= (base_sec - run_sec) / furlongs
    sample_cnt  INT          NOT NULL DEFAULT 0,     -- 基準タイム計算サンプル数
    interp      BOOLEAN      NOT NULL DEFAULT FALSE, -- TRUE=距離±200mで補完
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (year, monthday, jyocd, kaiji, nichiji, racenum, umaban)
);

-- 開催日検索（週次バッチ・日付範囲クエリ）
CREATE INDEX IF NOT EXISTS idx_nl_perf_date
    ON nl_performance (year, monthday);

-- 競馬場 × 日付検索（競馬場別集計）
CREATE INDEX IF NOT EXISTS idx_nl_perf_jyo_date
    ON nl_performance (jyocd, year, monthday);

COMMENT ON TABLE nl_performance IS
    'タイム偏差テーブル: 各出走馬の走破タイムと過去5年同条件平均タイムの差（秒/ハロン換算）';
COMMENT ON COLUMN nl_performance.time_dev IS
    'タイム偏差 = (基準タイム - 走破タイム) / ハロン数。プラス=速い';
COMMENT ON COLUMN nl_performance.base_sec IS
    '基準タイム（秒）: 過去5年・同競馬場×同距離×同走路×同クラスの平均走破タイム';
COMMENT ON COLUMN nl_performance.interp IS
    'TRUE: サンプル不足（10件未満）のため距離±200mのハロン単価から補完';
