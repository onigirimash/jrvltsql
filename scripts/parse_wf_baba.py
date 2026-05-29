#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TARGET frontier JV の WF7レコード（C:\\TFJV\\W5_DATA）から馬場状態を抽出し、
PostgreSQL nl_ra の sibababacd・dirtbabacd を更新する。

WF7フォーマット（TARGET frontier JV 独自形式）:
  ファイル名 : WF{YY}{MMDD}.DAT  (例: WF260524.DAT = 2026/05/24)
  レコード   : ASCII 固定長 1 行
    [  0- 2] "WF7"         レコード種別
    [  3-10] 作成日         YYYYMMDD
    [ 11-18] 開催日         YYYYMMDD
    [ 19-20] 予備(00)
  以降、8 文字ブロックが並ぶ（"00000000" で終端）:
    [+0-1]   場コード       01=札幌 .. 10=小倉
    [+2-3]   回             01 ..
    [+4-5]   日             01 ..
    [+6]     芝馬場状態     0=良 1=稍重 2=重 3=不良
    [+7]     ダ馬場状態     0=良 1=稍重 2=重 3=不良
  同一開催で複数ブロックある場合は最後（最終状態）を採用する。

Usage:
    py -3.12-32 scripts/parse_wf_baba.py [--start-year 2021] [--end-year 2026] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import pg8000.native as pg8000
except ImportError:
    raise SystemExit("pg8000 が必要です: pip install pg8000")

WF_DATA_ROOT = Path(r"C:\TFJV\W5_DATA")

# JRA 場コード (01-10 のみ対象)
VALID_JYO = {f"{i:02d}" for i in range(1, 11)}


def parse_wf7_file(filepath: Path) -> Dict[Tuple[str, int, int], Tuple[str, str]]:
    """
    WF7 ファイルを解析して {(場コード, 回, 日): (芝状態, ダート状態)} を返す。

    WF7 ブロックの並びの意味:
      - 各開催について複数ブロックが存在する（時刻経過での更新）
      - 第1ブロック: 開始時点（sib が '0'=良 で dirt='9'=未確認 の場合あり）
      - 後続ブロック: レース進行に伴う更新（sib が '1' に悪化するケースあり）
      → 開始時の芝状態を最初に採用し、ダートは最初の有効コード(0-3)を使う。

    状態コード: 0=良 1=稍重 2=重 3=不良 (WF7 ネイティブ値をそのまま格納)
    dirt='9': 未計測/未設定（開幕直後に多い）→ その後の有効コードで補完
    """
    first_sib:  Dict[Tuple[str, int, int], str] = {}  # 最初に記録された芝状態
    first_dirt: Dict[Tuple[str, int, int], str] = {}  # 最初の有効(0-3)ダート状態

    try:
        with open(filepath, encoding="ascii", errors="ignore") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line.startswith("WF7") or len(line) < 29:
                    continue
                pos = 21
                while pos + 8 <= len(line):
                    blk = line[pos: pos + 8]
                    if blk == "00000000":
                        break
                    jyo = blk[0:2]
                    if jyo not in VALID_JYO:
                        break
                    try:
                        kaiji   = int(blk[2:4])
                        nichiji = int(blk[4:6])
                    except ValueError:
                        break
                    if kaiji == 0 or nichiji == 0:
                        break

                    key   = (jyo, kaiji, nichiji)
                    s_raw = blk[6]
                    d_raw = blk[7]

                    # 芝状態: 最初のブロックを採用（dirt='9' でも sib は有効）
                    if key not in first_sib and s_raw in "0123":
                        first_sib[key] = s_raw

                    # ダート状態: 最初の有効コード(0-3)を採用
                    if key not in first_dirt and d_raw in "0123":
                        first_dirt[key] = d_raw

                    pos += 8
    except OSError as e:
        print(f"  [WARN] {filepath.name}: {e}", file=sys.stderr)

    # 両方揃ったキーのみ返す（dirtが取れない場合は '0'=良 にフォールバック）
    result: Dict[Tuple[str, int, int], Tuple[str, str]] = {}
    for key, sib in first_sib.items():
        result[key] = (sib, first_dirt.get(key, "0"))
    return result


def connect_db(password: str):
    return pg8000.Connection(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ.get("POSTGRES_DATABASE", "keiba"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=password,
    )


def main():
    ap = argparse.ArgumentParser(description="WF7 馬場状態 → nl_ra 更新")
    ap.add_argument("--start-year", type=int, default=2021)
    ap.add_argument("--end-year",   type=int, default=2026)
    ap.add_argument("--dry-run", action="store_true",
                    help="DB を更新せず対象レース数のみ確認")
    ap.add_argument("--password", default=os.environ.get("POSTGRES_PASSWORD", ""),
                    help="PostgreSQL パスワード (未指定時は POSTGRES_PASSWORD 環境変数)")
    args = ap.parse_args()

    conn = connect_db(args.password)

    grand_files   = 0
    grand_updated = 0

    for year in range(args.start_year, args.end_year + 1):
        year_dir = WF_DATA_ROOT / str(year)
        if not year_dir.exists():
            print(f"[SKIP] {year_dir} が見つかりません")
            continue

        yy = str(year)[2:]
        wf_files = sorted(year_dir.glob(f"WF{yy}*.DAT"))
        year_total = 0

        for fpath in wf_files:
            m = re.match(r"WF(\d{2})(\d{4})\.DAT", fpath.name, re.IGNORECASE)
            if not m:
                continue

            full_year = 2000 + int(m.group(1))
            monthday  = int(m.group(2))      # "0524" → 524 (INTEGER として格納)

            baba = parse_wf7_file(fpath)
            if not baba:
                continue

            grand_files += 1
            file_count = 0

            for (jyo, kaiji, nichiji), (shiba, dirt) in baba.items():
                if args.dry_run:
                    rows = conn.run(
                        "SELECT COUNT(*) FROM nl_ra"
                        " WHERE year = :yr AND monthday = :md"
                        " AND jyocd = :jyo AND kaiji = :kai AND nichiji = :nichi",
                        yr=full_year, md=monthday, jyo=jyo, kai=kaiji, nichi=nichiji,
                    )
                    file_count += rows[0][0]
                else:
                    conn.run(
                        "UPDATE nl_ra"
                        " SET sibababacd = :shiba, dirtbabacd = :dirt"
                        " WHERE year = :yr AND monthday = :md"
                        " AND jyocd = :jyo AND kaiji = :kai AND nichiji = :nichi",
                        shiba=shiba, dirt=dirt,
                        yr=full_year, md=monthday, jyo=jyo, kai=kaiji, nichi=nichiji,
                    )
                    file_count += conn.row_count

            year_total += file_count

        if not args.dry_run:
            conn.run("COMMIT")

        print(f"  {year}: {len(wf_files)} ファイル処理 → "
              f"{'確認' if args.dry_run else '更新'} {year_total} レース")
        grand_updated += year_total

    print(f"\n=== 完了 ===")
    print(f"処理ファイル: {grand_files}")
    print(f"{'確認' if args.dry_run else '更新'} レース計: {grand_updated}")

    conn.close()


if __name__ == "__main__":
    main()
