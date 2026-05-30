"""TARGET CSV出力データを nl_target_race テーブルへインポートする。

カラム構造（1始まり）:
  1=年  2=月  3=日  4=場所  5=R  6=レース名  7=芝ダート  8=距離
  9=天候  10=馬場状態  11=頭数
  12=通過3F  13=通過4F  14=通過5F  15=上り5F  16=上り4F  17=上り3F
  18=前後3F差  19=前後4F差  20=前後5F差
  21=1着タイム  22=全馬平均  23=1-5着平均  24=2-5着平均  25=25%平均
  26=PCI3  27=レースPCI  28=基準タイム  29=最速上3F
  30=通過ラップ表記  31=上りラップ表記
  32=Lap01 ... 56=Lap25
  57=1コーナー  58=2コーナー  59=3コーナー  60=4コーナー

Usage:
  py -3.12-32 scripts/import_target_csv.py FILE [FILE ...] [options]

  --pg-host      PostgreSQL host (default: localhost)
  --pg-port      PostgreSQL port (default: 5432)
  --pg-database  database name  (default: keiba)
  --pg-user      user name      (default: postgres)
  --pg-password  password
"""

import argparse
import csv
import io
import os
import sys

import pg8000.native

# 場所名 → jyo_cd
JYO_MAP = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10",
}

# 芝ダート表記 → surface コード
SURFACE_MAP = {
    "芝":     "T",
    "ダート":  "D",
    "ダ":     "D",
    "障害":   "J",
    "障":     "J",
}

_SQL_CREATE = """\
CREATE TABLE IF NOT EXISTS nl_target_race (
  race_date   CHAR(8)       NOT NULL,
  jyo_cd      CHAR(2)       NOT NULL,
  racenum     INT           NOT NULL,
  surface     CHAR(1),
  distance    INT,
  baba_state  VARCHAR(4),
  race_pci    NUMERIC(5,1),
  agari3f     NUMERIC(5,1),
  lap01  NUMERIC(4,1), lap02  NUMERIC(4,1), lap03  NUMERIC(4,1),
  lap04  NUMERIC(4,1), lap05  NUMERIC(4,1), lap06  NUMERIC(4,1),
  lap07  NUMERIC(4,1), lap08  NUMERIC(4,1), lap09  NUMERIC(4,1),
  lap10  NUMERIC(4,1), lap11  NUMERIC(4,1), lap12  NUMERIC(4,1),
  lap13  NUMERIC(4,1), lap14  NUMERIC(4,1), lap15  NUMERIC(4,1),
  corner1  VARCHAR(100),
  corner2  VARCHAR(100),
  corner3  VARCHAR(100),
  corner4  VARCHAR(100),
  created_at  TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (race_date, jyo_cd, racenum)
)
"""

_SQL_INSERT = """\
INSERT INTO nl_target_race (
  race_date, jyo_cd, racenum,
  surface, distance, baba_state,
  race_pci, agari3f,
  lap01, lap02, lap03, lap04, lap05, lap06, lap07,
  lap08, lap09, lap10, lap11, lap12, lap13, lap14, lap15,
  corner1, corner2, corner3, corner4
) VALUES (
  :race_date, :jyo_cd, :racenum,
  :surface, :distance, :baba_state,
  :race_pci, :agari3f,
  :lap01, :lap02, :lap03, :lap04, :lap05, :lap06, :lap07,
  :lap08, :lap09, :lap10, :lap11, :lap12, :lap13, :lap14, :lap15,
  :corner1, :corner2, :corner3, :corner4
)
ON CONFLICT (race_date, jyo_cd, racenum) DO NOTHING
"""


def _to_num(val: str):
    """空文字 / 変換不可 → None、それ以外 → float。"""
    s = val.strip().lstrip("+")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_row(row: list[str]) -> dict | None:
    """1行を辞書に変換。パース不可なら None を返す。"""
    if len(row) < 31:
        return None

    # 年月日 → race_date YYYYMMDD
    year  = row[0].strip()
    month = row[1].strip().zfill(2)
    day   = row[2].strip().zfill(2)
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    race_date = f"{year}{month}{day}"

    # 場所 → jyo_cd
    jyo_name = row[3].strip()
    jyo_cd = JYO_MAP.get(jyo_name)
    if jyo_cd is None:
        return None  # 対象外（NAR・海外等）

    # レース番号
    rnum_str = row[4].strip()
    if not rnum_str.isdigit():
        return None
    racenum = int(rnum_str)

    # surface（「障害(内回り)」等のバリアントもJに統一）
    surf_raw = row[6].strip()
    surface = SURFACE_MAP.get(surf_raw)
    if surface is None and surf_raw.startswith("障"):
        surface = "J"

    # distance
    dist_str = row[7].strip()
    distance = int(dist_str) if dist_str.isdigit() else None

    # 馬場状態
    baba_state = row[9].strip() or None

    # レースPCI (col27, index26)
    race_pci = _to_num(row[26]) if len(row) > 26 else None

    # 上り3F (col17, index16)
    agari3f = _to_num(row[16]) if len(row) > 16 else None

    # Lap01〜Lap15 (col32〜46, index31〜45)
    laps = {}
    for i in range(15):
        idx = 31 + i
        laps[f"lap{i+1:02d}"] = _to_num(row[idx]) if len(row) > idx else None

    # コーナー通過 (col57〜60, index56〜59)
    def _corner(idx: int) -> str | None:
        if len(row) > idx:
            v = row[idx].strip()
            return v or None
        return None

    return {
        "race_date":  race_date,
        "jyo_cd":     jyo_cd,
        "racenum":    racenum,
        "surface":    surface,
        "distance":   distance,
        "baba_state": baba_state,
        "race_pci":   race_pci,
        "agari3f":    agari3f,
        **laps,
        "corner1":    _corner(56),
        "corner2":    _corner(57),
        "corner3":    _corner(58),
        "corner4":    _corner(59),
    }


def import_file(conn: pg8000.native.Connection, path: str) -> tuple[int, int]:
    """1ファイルをインポート。(inserted, skipped) を返す。"""
    with open(path, "rb") as f:
        text = f.read().decode("cp932")

    rows = list(csv.reader(io.StringIO(text)))
    inserted = 0
    skipped  = 0

    batch: list[dict] = []

    for row in rows:
        rec = _parse_row(row)
        if rec is None:
            skipped += 1
            continue
        batch.append(rec)
        if len(batch) >= 500:
            _flush(conn, batch)
            inserted += len(batch)
            batch = []

    if batch:
        _flush(conn, batch)
        inserted += len(batch)

    return inserted, skipped


def _flush(conn: pg8000.native.Connection, batch: list[dict]) -> None:
    for rec in batch:
        conn.run(_SQL_INSERT, **rec)


def main() -> None:
    parser = argparse.ArgumentParser(description="TARGET CSV → nl_target_race インポート")
    parser.add_argument("files", nargs="+", metavar="FILE", help="TARGETが出力したCSVファイル")
    parser.add_argument("--pg-host",     default=os.environ.get("POSTGRES_HOST",     "localhost"))
    parser.add_argument("--pg-port",     default=os.environ.get("POSTGRES_PORT",     "5432"))
    parser.add_argument("--pg-database", default=os.environ.get("POSTGRES_DATABASE", "keiba"))
    parser.add_argument("--pg-user",     default=os.environ.get("POSTGRES_USER",     "postgres"))
    parser.add_argument("--pg-password", default=os.environ.get("POSTGRES_PASSWORD", ""))
    args = parser.parse_args()

    conn = pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )

    # テーブル作成（存在しない場合のみ）
    conn.run(_SQL_CREATE)
    print("テーブル nl_target_race を確認/作成しました。")

    total_inserted = 0
    total_skipped  = 0

    for path in args.files:
        if not os.path.exists(path):
            print(f"  [SKIP] ファイルが存在しません: {path}")
            continue
        print(f"  インポート中: {path} ...", end=" ", flush=True)
        ins, skp = import_file(conn, path)
        print(f"{ins:,} 件挿入, {skp:,} 件スキップ")
        total_inserted += ins
        total_skipped  += skp

    print(f"\n完了: 合計 {total_inserted:,} 件挿入, {total_skipped:,} 件スキップ")
    conn.close()


if __name__ == "__main__":
    main()
