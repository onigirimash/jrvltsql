-- nl_race_prediction: レース別推定勝率テーブル（Step 9）
--
-- Softmax 変換式:
--   win_prob[i] = exp(adjusted_index[i] / T) / Σ exp(adjusted_index[j] / T)
--
-- 除外条件:
--   初出走馬（nl_horse_index に未登録）が3頭以上いるレースはスキップ
--
-- adjusted_index が NULL の馬は 0 として処理
-- 再計算時は ON CONFLICT DO UPDATE で上書き

CREATE TABLE IF NOT EXISTS nl_race_prediction (
    year           INTEGER      NOT NULL,
    monthday       INTEGER      NOT NULL,
    jyocd          TEXT         NOT NULL,
    racenum        INTEGER      NOT NULL,
    umaban         INTEGER      NOT NULL,
    kettonum       TEXT         NOT NULL,
    adjusted_index NUMERIC(6,3),
    win_prob       NUMERIC(8,6) NOT NULL,
    t_parameter    NUMERIC(5,3) NOT NULL,
    logic_version  TEXT         NOT NULL,
    created_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (year, monthday, jyocd, racenum, umaban)
);

CREATE INDEX IF NOT EXISTS idx_nl_race_prediction_date
    ON nl_race_prediction (year, monthday, jyocd, racenum);

COMMENT ON TABLE nl_race_prediction IS
    '推定勝率テーブル: adjusted_index を Softmax 変換した出走馬別勝率';
COMMENT ON COLUMN nl_race_prediction.adjusted_index IS
    'nl_horse_index.adjusted_index のスナップショット（NULL=初出走馬扱いで0処理）';
COMMENT ON COLUMN nl_race_prediction.win_prob       IS 'Softmax 推定勝率（0〜1）';
COMMENT ON COLUMN nl_race_prediction.t_parameter    IS 'Softmax 温度パラメータ（小さいほど勝者集中）';
COMMENT ON COLUMN nl_race_prediction.logic_version  IS '計算ロジックのバージョン文字列';
