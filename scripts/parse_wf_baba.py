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
    [+6]     セッションフラグ  '0'=開催前(朝確認) / '1'=開催中(レース時)
    [+7]     馬場状態コード    '0'=良 '1'=稍重 '2'=重 ('9'=未計測)
  同一開催で複数ブロックがある場合、セッション='1' の最後のブロックを採用する。
  WF7は芝・ダートを一本化した統合コードのため、sibababacd・dirtbabacd に同値を設定。

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


def parse_wf7_file(filepath: Path) -> Dict[Tuple[str, int, int], str]:
    """
    WF7 ファイルを解析して {(場コード, 回, 日): 馬場状態コード} を返す。

    8文字ブロックのフィールド:
      pos6 = セッションフラグ: '0'=開催前(朝確認) '1'=開催中(レース時)
      pos7 = 馬場状態コード:   '0'=良 '1'=稍重 '2'=重 ('9'=未計測)

    セッション='1' かつ 有効コード('0'-'3')の最後のブロックを採用する。
    WF7 は芝・ダートを一本化した統合コードのため返値は1値。
    """
    last_cond: Dict[Tuple[str, int, int], str] = {}

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

                    # セッション='1'（開催中）かつ有効な馬場コードのみ採用
                    if blk[6] == "1" and blk[7] in "0123":
                        last_cond[(jyo, kaiji, nichiji)] = blk[7]

                    pos += 8
    except OSError as e:
        print(f"  [WARN] {filepath.name}: {e}", file=sys.stderr)

    return last_cond


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

            # baba: {(jyo, kaiji, nichiji): cond_code}  (芝・ダート統合の1値)
            for (jyo, kaiji, nichiji), cond in baba.items():
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
                        " SET sibababacd = :cond, dirtbabacd = :cond"
                        " WHERE year = :yr AND monthday = :md"
                        " AND jyocd = :jyo AND kaiji = :kai AND nichiji = :nichi",
                        cond=cond,
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
