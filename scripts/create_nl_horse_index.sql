-- nl_horse_index: 馬別実力指数テーブル（Step 5）
--
-- レース強度補正（イテレーション収束）後に正規化した指数を格納する。
-- 1馬×距離区分×走路 につき 1行。週次更新で UPSERT される。
--
-- 距離区分:
--   S = 短距離（〜1400m）
--   M = マイル（1500〜1800m）
--   I = 中距離（1900〜2200m）
--   L = 長距離（2300m〜）
--
-- 走路:
--   T = 芝
--   D = ダート
--   J = 障害

CREATE TABLE IF NOT EXISTS nl_horse_index (
    kettonum     TEXT    NOT NULL,
    distance_cat CHAR(1) NOT NULL,
    surface      CHAR(1) NOT NULL,
    norm_index   NUMERIC(6,2),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kettonum, distance_cat, surface)
);

COMMENT ON TABLE nl_horse_index IS
    '馬別実力指数（レース強度補正・イテレーション収束後、平均50・標準偏差10に正規化）';
COMMENT ON COLUMN nl_horse_index.kettonum     IS '血統登録番号（nl_se.kettonum）';
COMMENT ON COLUMN nl_horse_index.distance_cat IS '距離区分: S=短距離/M=マイル/I=中距離/L=長距離';
COMMENT ON COLUMN nl_horse_index.surface      IS '走路: T=芝/D=ダート/J=障害';
COMMENT ON COLUMN nl_horse_index.norm_index   IS '正規化後指数（平均50・標準偏差10）';
