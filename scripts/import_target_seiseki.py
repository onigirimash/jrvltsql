"""
TARGET 成績基本データ+単勝オッズ CSV を nl_se テーブルに UPDATE インポートする。

カラムマッピング（0始まりインデックス）:
  row[0]  = 年(2桁)          → year = 2000 + int
  row[1]  = 月               → monthday = month*100 + day
  row[2]  = 日
  row[4]  = 場所名           → jyocd (JRA 01-10のみ処理)
  row[6]  = レース番号       → racenum
  row[8]  = クラスコード     → class_code
  row[20] = 確定着順         → kakuteijyuni
  row[24] = 人気順           → ninki
  row[25] = 走破タイム(秒)   → time (min*100+sec 形式に変換)
  row[28] = 通過順1角        → jyuni1c
  row[29] = 通過順2角        → jyuni2c
  row[30] = 通過順3角        → jyuni3c
  row[31] = 通過順4角        → jyuni4c
  row[32] = 上がり3Fタイム   → harontimel3
  row[37] = 血統登録番号(8桁) → kettonum ('20' prefix で 10桁化)
  row[41] = 単勝オッズ       → odds  (ファイル実列: col42, 仕様col49とは異なる)

JOIN条件: kettonum + year + monthday + jyocd + racenum

Usage:
  py -3.12-32 scripts/import_target_seiseki.py --file C:\\TFJV\\TXT\\seiseki_2026.txt --pg-password kousuke0809
"""

import argparse
import csv
import sys

import pg8000.native


JYO_MAP = {
    '札幌': '01', '函館': '02', '福島': '03', '新潟': '04', '東京': '05',
    '中山': '06', '中京': '07', '京都': '08', '阪神': '09', '小倉': '10',
}

_SQL_UPDATE = """\
UPDATE nl_se
   SET kakuteijyuni = :kakuteijyuni,
       harontimel3  = :harontimel3,
       jyuni1c      = :jyuni1c,
       jyuni2c      = :jyuni2c,
       jyuni3c      = :jyuni3c,
       jyuni4c      = :jyuni4c,
       odds         = :odds,
       ninki        = :ninki,
       time         = :time,
       class_code   = :class_code
 WHERE kettonum = :kettonum
   AND year     = :year
   AND monthday = :monthday
   AND jyocd    = :jyocd
   AND racenum  = :racenum
RETURNING 1
"""


def _int(s: str):
    v = s.strip()
    if not v or not v.lstrip('-').isdigit():
        return None
    n = int(v)
    return n if n != 0 else None


def _float(s: str):
    v = s.strip()
    if not v:
        return None
    try:
        f = float(v)
        return f if f != 0.0 else None
    except ValueError:
        return None


def _to_time(s: str):
    """走破タイム(秒, e.g. '106.7') → nl_se time 形式 (min*100+sec, e.g. 146.7)"""
    t = _float(s)
    if t is None:
        return None
    m = int(t / 60)
    sec = round(t - m * 60, 1)
    return round(m * 100 + sec, 1)


def _kettonum(s: str):
    """8桁 → 10桁変換 (21世紀馬: '23xxxxxx' → '2023xxxxxx')"""
    v = s.strip()
    if len(v) == 8 and v.isdigit():
        return '20' + v
    if len(v) == 10 and v.isdigit():
        return v
    return None


def parse_row(row: list[str]) -> dict | None:
    if len(row) < 42:
        return None

    jyo_cd = JYO_MAP.get(row[4].strip())
    if jyo_cd is None:
        return None  # NAR・海外等はスキップ

    year_2d = row[0].strip()
    if not year_2d.isdigit():
        return None
    year     = 2000 + int(year_2d)
    monthday = int(row[1].strip()) * 100 + int(row[2].strip())
    racenum  = _int(row[6])
    kettonum = _kettonum(row[37])

    if not kettonum or racenum is None:
        return None

    return {
        'kakuteijyuni': _int(row[20]),
        'harontimel3':  _float(row[32]),
        'jyuni1c':      _int(row[28]),
        'jyuni2c':      _int(row[29]),
        'jyuni3c':      _int(row[30]),
        'jyuni4c':      _int(row[31]),
        'odds':         _float(row[41]),
        'ninki':        _int(row[24]),
        'time':         _to_time(row[25]),
        'class_code':   row[8].strip() or None,
        'kettonum':     kettonum,
        'year':         year,
        'monthday':     monthday,
        'jyocd':        jyo_cd,
        'racenum':      racenum,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description='TARGET成績基本データをnl_seに更新インポート')
    parser.add_argument('--file',        default=r'C:\TFJV\TXT\seiseki_2026.txt',
                        help='入力CSVファイルパス')
    parser.add_argument('--pg-host',     default='localhost')
    parser.add_argument('--pg-port',     type=int, default=5432)
    parser.add_argument('--pg-database', default='keiba')
    parser.add_argument('--pg-user',     default='postgres')
    parser.add_argument('--pg-password', required=True)
    args = parser.parse_args()

    conn = pg8000.native.Connection(
        args.pg_user,
        host=args.pg_host,
        port=args.pg_port,
        database=args.pg_database,
        password=args.pg_password,
    )

    total = skipped = matched = unmatched = 0

    conn.run('BEGIN')
    try:
        with open(args.file, encoding='cp932', errors='replace') as f:
            for raw in csv.reader(f):
                total += 1
                params = parse_row(raw)
                if params is None:
                    skipped += 1
                    continue

                result = conn.run(_SQL_UPDATE, **params)
                if result:
                    matched += len(result)
                else:
                    unmatched += 1

                if total % 2000 == 0:
                    print(f'  {total:,}行処理済み  マッチ={matched:,}  不一致={unmatched:,}',
                          flush=True)

        conn.run('COMMIT')

    except Exception as e:
        conn.run('ROLLBACK')
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    jra_rows = total - skipped
    match_rate = matched / jra_rows * 100 if jra_rows > 0 else 0.0

    print()
    print('=== インポート完了 ===')
    print(f'  総行数       : {total:,}')
    print(f'  JRA対象      : {jra_rows:,}')
    print(f'  更新件数     : {matched:,}')
    print(f'   不一致(未登録): {unmatched:,}')
    print(f'  スキップ(NAR): {skipped:,}')
    print(f'  マッチ率     : {match_rate:.1f}%')


if __name__ == '__main__':
    main()
