"""TARGET CSV 脚質・クラスデータを nl_se へインポートする。

ファイル形式（cp932, カンマ区切り, 14カラム）:
  [0] 年(2桁)  [1] 月  [2] 日  [3] 場所  [4] レース番号  [5] 芝ダート
  [6] 距離     [7] 馬名 [8] 馬番 [9] 血統登録番号 [10] 脚質
  [11] クラス名 [12] クラスコード [13] グレードコード

結合キー: year + monthday + jyo_cd + racenum + kettonum
脚質値: 逃げ / 先行 / 中団 / 差し / 後方 / 追込 / まくり

クラスコード → クラス区分マッピング（参考）:
  15       → 新馬
  7        → 未勝利
  23       → 1勝クラス
  43       → 2勝クラス
  67       → 3勝クラス
  115/131  → オープン
  163      → G3 / JG3
  179      → G2 / JG2
  195      → G1 / JG1

Usage:
  py -3.12-32 scripts/import_kyakushitsu.py FILE [FILE ...]
               [--pg-password PW]
"""

import argparse
import csv
import io
import os
import sys

import pg8000.native

JYO_MAP = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10",
}

# 半角カナのまくり → 全角
_KYAKU_NORM = {"ﾏｸﾘ": "まくり"}

_SQL_ADD_COLS = """
ALTER TABLE nl_se
  ADD COLUMN IF NOT EXISTS target_kyakushitsu VARCHAR(4),
  ADD COLUMN IF NOT EXISTS class_name         VARCHAR(50),
  ADD COLUMN IF NOT EXISTS class_code         VARCHAR(10),
  ADD COLUMN IF NOT EXISTS grade_code         VARCHAR(5)
"""

_SQL_UPDATE = """
UPDATE nl_se
SET target_kyakushitsu = :kyaku,
    class_name         = :class_name,
    class_code         = :class_code,
    grade_code         = :grade_code
WHERE year     = :year
  AND monthday = :monthday
  AND jyocd    = :jyo_cd
  AND racenum  = :racenum
  AND kettonum = :kettonum
"""


def _parse_row(row: list[str]) -> dict | None:
    if len(row) < 11:
        return None

    year_2 = row[0].strip()
    month  = row[1].strip().zfill(2)
    day    = row[2].strip().zfill(2)
    venue  = row[3].strip()
    rnum   = row[4].strip()
    ketto  = row[9].strip()
    kyaku  = row[10].strip()

    class_name  = row[11].strip() if len(row) > 11 else ""
    class_code  = row[12].strip() if len(row) > 12 else ""
    grade_code  = row[13].strip() if len(row) > 13 else ""

    if not (year_2.isdigit() and month.isdigit() and day.isdigit()):
        return None
    jyo_cd = JYO_MAP.get(venue)
    if jyo_cd is None or not rnum.isdigit() or not ketto:
        return None

    year     = 2000 + int(year_2)
    monthday = int(month) * 100 + int(day)
    kyaku    = _KYAKU_NORM.get(kyaku, kyaku) or None

    return {
        "year":       year,
        "monthday":   monthday,
        "jyo_cd":     jyo_cd,
        "racenum":    int(rnum),
        "kettonum":   ketto,
        "kyaku":      kyaku,
        "class_name": class_name or None,
        "class_code": class_code or None,
        "grade_code": grade_code or None,
    }


def import_file(conn: pg8000.native.Connection, path: str) -> tuple[int, int]:
    with open(path, "rb") as f:
        text = f.read().decode("cp932")

    rows = list(csv.reader(io.StringIO(text)))
    updated = skipped = 0
    batch: list[dict] = []

    for row in rows:
        rec = _parse_row(row)
        if rec is None:
            skipped += 1
            continue
        batch.append(rec)
        if len(batch) >= 1000:
            for r in batch:
                conn.run(_SQL_UPDATE, **r)
            updated += len(batch)
            batch = []
            print(".", end="", flush=True)

    if batch:
        for r in batch:
            conn.run(_SQL_UPDATE, **r)
        updated += len(batch)

    return updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="TARGET 脚質・クラスCSV → nl_se 更新")
    parser.add_argument("files", nargs="+", metavar="FILE")
    parser.add_argument("--pg-host",     default=os.environ.get("POSTGRES_HOST",     "localhost"))
    parser.add_argument("--pg-port",     default=os.environ.get("POSTGRES_PORT",     "5432"))
    parser.add_argument("--pg-database", default=os.environ.get("POSTGRES_DATABASE", "keiba"))
    parser.add_argument("--pg-user",     default=os.environ.get("POSTGRES_USER",     "postgres"))
    parser.add_argument("--pg-password", default=os.environ.get("POSTGRES_PASSWORD", ""))
    args = parser.parse_args()

    conn = pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host, port=int(args.pg_port),
        database=args.pg_database, password=args.pg_password,
    )

    conn.run(_SQL_ADD_COLS)
    print("カラム確認/追加完了")

    total_upd = total_skp = 0
    for path in args.files:
        if not os.path.exists(path):
            print(f"  [SKIP] {path}")
            continue
        print(f"  更新中: {path} ...", end=" ", flush=True)
        upd, skp = import_file(conn, path)
        print(f"\n  → {upd:,} 件更新, {skp:,} 件スキップ")
        total_upd += upd
        total_skp += skp

    print(f"\n完了: 合計 {total_upd:,} 件更新, {total_skp:,} 件スキップ")
    conn.close()


if __name__ == "__main__":
    main()
