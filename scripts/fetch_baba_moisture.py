#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
JRA馬場情報PDF スクレイパー

JRA公式サイトの含水率・クッション値PDFを取得し nl_baba_moisture テーブルへ保存する。

URL パターン:
  https://www.jra.go.jp/keiba/baba/archive/{year}pdf/{venue}{nn:02d}.pdf

取得データ:
  - 芝クッション値         (2020年以降)
  - 芝含水率  ゴール前     (%)
  - 芝含水率  4コーナー    (%)
  - ダート含水率 ゴール前  (%)
  - ダート含水率 4コーナー (%)

Usage:
    py -3.14 scripts/fetch_baba_moisture.py [options]

    --year  YYYY              特定年のみ（省略時: 2018〜今年）
    --venue tokyo             特定競馬場のみ
    --dry-run                 DB保存せず結果だけ表示
    --pg-host / --pg-port / --pg-database / --pg-user / --pg-password
"""

import argparse
import io
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal

import pdfplumber
import pg8000.native

# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────

BASE_URL = "https://www.jra.go.jp/keiba/baba/archive/{year}pdf/{venue}{nn:02d}.pdf"

# venue スラッグ → JyoCD
VENUE_MAP = {
    "sapporo":   "01",
    "hakodate":  "02",
    "fukushima": "03",
    "niigata":   "04",
    "tokyo":     "05",
    "nakayama":  "06",
    "chukyo":    "07",
    "kyoto":     "08",
    "hanshin":   "09",
    "kokura":    "10",
}

VENUES = list(VENUE_MAP.keys())

# PDFが存在しない場合の最大試行連番
MAX_NN = 7

# 含水率開始年・クッション値開始年
MOISTURE_START_YEAR  = 2018
CUSHION_START_YEAR   = 2020
ARCHIVE_START_YEAR   = 2018

# Format A: セクションヘッダー "第１日・第２日（2024年1月26日～28日）"
_RE_HEADER = re.compile(
    r"第\d+日[・・]\s*第\d+日.*?[（(]"
    r"(\d{4})年(\d+)月(\d+)日[～〜](\d+)日[）)]"
)

# Format B: 含水率のみ旧形式 "2019年1月25日から27日の含水率"
_RE_HEADER_B = re.compile(
    r"(\d{4})年(\d+)月(\d+)日から(?:\d+月)?(\d+)日の含水率"
)

# Format C: 新形式フラットテーブル 日付列 "1月31日" / "2月 1日"
_RE_DATE_C = re.compile(r"(\d+)月\s*(\d+)日")

# 曜日名 → 金曜日からのオフセット
_WEEKDAY_OFFSET = {
    "金曜日": 0, "土曜日": 1, "日曜日": 2,
    "月曜日": 3, "火曜日": 4, "水曜日": 5,
}

# ──────────────────────────────────────────────
# PDF 解析
# ──────────────────────────────────────────────

def _to_decimal(val: str | None) -> Decimal | None:
    """文字列 → Decimal（変換不能なら None）"""
    if val is None:
        return None
    s = val.strip()
    if not s or s == "-" or s == "－":
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def _extract_sections(page_text: str) -> list[dict]:
    """
    ページ全文を行単位に分割し、各セクションの
    (start_date, day_offset→0/1/2) を返す。

    Returns:
        [{"fri": date, "positions": (line_start, line_end)}, ...]
    """
    lines = page_text.splitlines()
    sections = []
    for i, line in enumerate(lines):
        m = _RE_HEADER.search(line)
        if m:
            year, month, day_start, day_end = (int(x) for x in m.groups())
            try:
                fri = date(year, month, day_start)
            except ValueError:
                continue
            sections.append({"fri": fri, "line_idx": i})
    return sections


def _make_rec(race_dt: date, jyo_cd: str, cushion, tg, t4, dg, d4) -> dict | None:
    rec = {
        "race_date":             race_dt.strftime("%Y%m%d"),
        "jyo_cd":                jyo_cd,
        "cushion_value":         cushion,
        "turf_moisture_goal":    tg,
        "turf_moisture_4corner": t4,
        "dirt_moisture_goal":    dg,
        "dirt_moisture_4corner": d4,
    }
    if all(v is None for v in (tg, t4, dg, d4)):
        return None
    return rec


def _parse_format_a(raw_text: str, tables: list, jyo_cd: str, year: int) -> list[dict]:
    """Format A (2020後半〜2024): クッション値テーブル+含水率テーブルのペア形式"""
    sections = _extract_sections(raw_text)
    if not sections:
        return []

    records = []
    for sec_idx, sec in enumerate(sections):
        fri_date = sec["fri"]
        tbl_cushion  = sec_idx * 2
        tbl_moisture = sec_idx * 2 + 1
        if tbl_moisture >= len(tables):
            break

        t_cushion  = tables[tbl_cushion]
        t_moisture = tables[tbl_moisture]

        cushion_row = t_cushion[1] if len(t_cushion) > 1 else []
        cushion_vals = [_to_decimal(cushion_row[i]) if i < len(cushion_row) else None
                        for i in (1, 2, 3)]

        def _mrow(t, row_idx):
            if row_idx >= len(t):
                return [None, None, None]
            r = t[row_idx]
            return [_to_decimal(r[i]) if i < len(r) else None for i in (2, 3, 4)]

        tg_vals = _mrow(t_moisture, 1)
        t4_vals = _mrow(t_moisture, 2)
        dg_vals = _mrow(t_moisture, 3)
        d4_vals = _mrow(t_moisture, 4)

        for day_offset in range(3):
            race_dt = fri_date + timedelta(days=day_offset)
            cv = cushion_vals[day_offset] if year >= CUSHION_START_YEAR else None
            rec = _make_rec(race_dt, jyo_cd, cv,
                            tg_vals[day_offset], t4_vals[day_offset],
                            dg_vals[day_offset], d4_vals[day_offset])
            if rec:
                records.append(rec)

    return records


def _parse_format_b(raw_text: str, tables: list, jyo_cd: str) -> list[dict]:
    """Format B (2018〜2020前半): 含水率のみ、'から'形式日付ヘッダー"""
    lines = raw_text.splitlines()
    fri_dates = []
    for line in lines:
        m = _RE_HEADER_B.search(line)
        if m:
            try:
                fri_dates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                continue

    records = []
    for sec_idx, fri in enumerate(fri_dates):
        if sec_idx >= len(tables):
            break
        t = tables[sec_idx]
        if len(t) < 5 or len(t[0]) < 5:
            continue

        # row[0] = ['', '場所', '金曜日', '土曜日', '日曜日'] → 曜日名→オフセット
        for col_pos in range(3):
            col_i = col_pos + 2
            day_name = t[0][col_i] if col_i < len(t[0]) else None
            offset = _WEEKDAY_OFFSET.get(day_name, col_pos) if day_name else col_pos
            race_dt = fri + timedelta(days=offset)

            def _v(row_idx):
                if row_idx >= len(t):
                    return None
                r = t[row_idx]
                return _to_decimal(r[col_i] if col_i < len(r) else None)

            rec = _make_rec(race_dt, jyo_cd, None, _v(1), _v(2), _v(3), _v(4))
            if rec:
                records.append(rec)

    return records


def _parse_format_c(tables: list, jyo_cd: str, year: int) -> list[dict]:
    """Format C (2025〜): 全日程フラット1テーブル、11列構成"""
    data_table = next((t for t in tables if t and len(t[0]) == 11), None)
    if not data_table:
        return []

    records = []
    cur_year = year
    prev_month = None

    for row in data_table[3:]:  # 先頭3行はヘッダー
        if not row or not row[1]:
            continue
        m = _RE_DATE_C.search(str(row[1]))
        if not m:
            continue
        month, day = int(m.group(1)), int(m.group(2))

        if prev_month is not None and month < prev_month:
            cur_year += 1
        prev_month = month

        try:
            race_dt = date(cur_year, month, day)
        except ValueError:
            continue

        rec = _make_rec(
            race_dt, jyo_cd,
            _to_decimal(row[5]),   # クッション値
            _to_decimal(row[7]),   # 芝ゴール前
            _to_decimal(row[8]),   # 芝4コーナー
            _to_decimal(row[9]),   # ダートゴール前
            _to_decimal(row[10]),  # ダート4コーナー
        )
        if rec:
            records.append(rec)

    return records


def parse_pdf(pdf_bytes: bytes, jyo_cd: str, year: int) -> list[dict]:
    """PDFバイト列を解析して 1開催日 1レコードのリストを返す。"""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        raw_text = page.extract_text() or ""
        tables   = page.extract_tables()

    # Format C: フラット11列テーブル (2025〜)
    if any(t and len(t[0]) == 11 for t in tables):
        return _parse_format_c(tables, jyo_cd, year)

    # Format A: クッション値+含水率ペアテーブル (2020後半〜2024)
    if _RE_HEADER.search(raw_text):
        return _parse_format_a(raw_text, tables, jyo_cd, year)

    # Format B: 含水率のみ旧形式 (2018〜2020前半)
    return _parse_format_b(raw_text, tables, jyo_cd)


# ──────────────────────────────────────────────
# HTTP フェッチ
# ──────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 15) -> bytes | None:
    """URLからバイト列を取得。404/エラーは None を返す。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  HTTP {e.code}: {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────

_SQL_UPSERT = """
INSERT INTO nl_baba_moisture
  (race_date, jyo_cd, cushion_value,
   turf_moisture_goal, turf_moisture_4corner,
   dirt_moisture_goal, dirt_moisture_4corner)
VALUES
  (:race_date, :jyo_cd, :cushion_value,
   :turf_moisture_goal, :turf_moisture_4corner,
   :dirt_moisture_goal, :dirt_moisture_4corner)
ON CONFLICT (race_date, jyo_cd) DO NOTHING
"""

_SQL_CHECK_EXISTS = """
SELECT COUNT(*) FROM nl_baba_moisture
WHERE race_date >= :from_date AND race_date <= :to_date AND jyo_cd = :jyo_cd
"""


def _connect(args) -> pg8000.native.Connection:
    return pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=int(args.pg_port),
        database=args.pg_database,
        password=args.pg_password,
    )


def _upsert_records(conn, records: list[dict]) -> int:
    inserted = 0
    for rec in records:
        conn.run(_SQL_UPSERT, **rec)
        inserted += 1
    return inserted


# ──────────────────────────────────────────────
# メイン処理
# ──────────────────────────────────────────────

def run(args):
    conn = None if args.dry_run else _connect(args)

    current_year = datetime.now().year
    years  = [args.year]  if args.year  else list(range(ARCHIVE_START_YEAR, current_year + 1))
    venues = [args.venue] if args.venue else VENUES

    total_pdfs     = 0
    total_records  = 0
    total_inserted = 0
    total_skipped  = 0

    for year in years:
        if year < ARCHIVE_START_YEAR:
            continue
        for venue in venues:
            jyo_cd = VENUE_MAP[venue]
            for nn in range(1, MAX_NN + 1):
                url = BASE_URL.format(year=year, venue=venue, nn=nn)
                pdf_bytes = _fetch_url(url)
                if pdf_bytes is None:
                    # 404 = この開催番号は存在しない → 次のvenueへ
                    break

                total_pdfs += 1
                try:
                    records = parse_pdf(pdf_bytes, jyo_cd, year)
                except Exception as e:
                    print(f"  PARSE ERROR {url}: {e}", file=sys.stderr)
                    continue

                if not records:
                    print(f"  {year} {venue}{nn:02d}: no records")
                    continue

                total_records += len(records)

                if args.dry_run:
                    print(f"  [DRY] {year} {venue}{nn:02d}: {len(records)} records")
                    for r in records[:3]:
                        print(f"    {r}")
                    if len(records) > 3:
                        print(f"    ... ({len(records)-3} more)")
                    total_inserted += len(records)
                else:
                    inserted = _upsert_records(conn, records)
                    skipped  = len(records) - inserted
                    total_inserted += inserted
                    total_skipped  += skipped
                    print(f"  {year} {venue}{nn:02d}: {inserted} inserted, {skipped} skipped  ({url})")

                # サーバー負荷軽減
                time.sleep(0.3)

    if conn:
        conn.close()

    print(f"\n完了: PDFs={total_pdfs}, records={total_records}, "
          f"inserted={total_inserted}, skipped={total_skipped}")


# ──────────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JRA馬場情報PDF → nl_baba_moisture")
    parser.add_argument("--year",    type=int, help="取得年 (例: 2024)")
    parser.add_argument("--venue",   choices=VENUES, help="競馬場スラッグ")
    parser.add_argument("--dry-run", action="store_true", help="DB保存せず結果表示のみ")
    parser.add_argument("--pg-host",     default=os.environ.get("POSTGRES_HOST",     "localhost"))
    parser.add_argument("--pg-port",     default=os.environ.get("POSTGRES_PORT",     "5432"))
    parser.add_argument("--pg-database", default=os.environ.get("POSTGRES_DATABASE", "keiba"))
    parser.add_argument("--pg-user",     default=os.environ.get("POSTGRES_USER",     "postgres"))
    parser.add_argument("--pg-password", default=os.environ.get("PGPASSWORD",        ""))
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    # Windows stdout を UTF-8 に強制
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
