-- nl_baba_moisture: JRA馬場情報PDF（含水率・クッション値）
-- JRA公式サイト https://www.jra.go.jp/keiba/baba/archive/ から取得
-- 開催日 × 競馬場 単位で格納。金/土/日それぞれ個別レコード。
--
-- 含水率: 2018年7月27日開催分より
-- クッション値: 2020年9月11日開催分より（それ以前はNULL）

CREATE TABLE IF NOT EXISTS nl_baba_moisture (
  race_date              CHAR(8)      NOT NULL,   -- YYYYMMDD
  jyo_cd                 CHAR(2)      NOT NULL,   -- 競馬場コード 01-10
  cushion_value          NUMERIC(4,1),             -- 芝クッション値
  turf_moisture_goal     NUMERIC(4,1),             -- 芝含水率 ゴール前 (%)
  turf_moisture_4corner  NUMERIC(4,1),             -- 芝含水率 4コーナー (%)
  dirt_moisture_goal     NUMERIC(4,1),             -- ダート含水率 ゴール前 (%)
  dirt_moisture_4corner  NUMERIC(4,1),             -- ダート含水率 4コーナー (%)
  created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  PRIMARY KEY (race_date, jyo_cd)
);

COMMENT ON TABLE nl_baba_moisture IS
  'JRA馬場情報PDF: 開催日x競馬場の含水率・クッション値';
COMMENT ON COLUMN nl_baba_moisture.cushion_value IS
  '芝クッション値 (2020年以降のみ)';
COMMENT ON COLUMN nl_baba_moisture.turf_moisture_goal IS
  '芝含水率 ゴール前 (%)';
COMMENT ON COLUMN nl_baba_moisture.turf_moisture_4corner IS
  '芝含水率 4コーナー (%)';
COMMENT ON COLUMN nl_baba_moisture.dirt_moisture_goal IS
  'ダート含水率 ゴール前 (%)';
COMMENT ON COLUMN nl_baba_moisture.dirt_moisture_4corner IS
  'ダート含水率 4コーナー (%)';

CREATE INDEX IF NOT EXISTS idx_nl_baba_moisture_date
  ON nl_baba_moisture (race_date);
CREATE INDEX IF NOT EXISTS idx_nl_baba_moisture_jyo_date
  ON nl_baba_moisture (jyo_cd, race_date);
