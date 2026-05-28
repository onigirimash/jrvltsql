#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
馬場状態（WE/WH レコード）バックフィルスクリプト

nl_we (天候馬場状態通知) と nl_wh (馬場状態変更報) は RACE データ仕様に含まれるが、
daily_update.py が option=2 (今週データ) しか取得しないため、過去分が空のまま。

JVLink option=1 は from_date 以降の全レコードを1ストリームで返す。
月次チャンクに分けると各呼び出しで全データがストリームされるため非効率。
→ 全期間を1回の呼び出しで処理する。

処理後の nl_we / nl_wh 件数を表示して確認する。

Usage:
    py -3.12-32 scripts/backfill_track_condition.py [options]

    --from-date  YYYYMMDD  開始日（デフォルト: 20210101）
    --to-date    YYYYMMDD  終了日（デフォルト: 今日）
    --config               config.yaml のパス
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database import create_database_from_config
from src.importer.batch import BatchProcessor
from src.utils.config import load_config


def main() -> int:
    today = date.today().strftime('%Y%m%d')
    parser = argparse.ArgumentParser(
        description='nl_we / nl_wh バックフィル（馬場状態コード）',
    )
    parser.add_argument('--from-date', default='20210101', metavar='YYYYMMDD')
    parser.add_argument('--to-date',   default=today,      metavar='YYYYMMDD')
    parser.add_argument('--config',    default=None,        help='config.yaml のパス')
    args = parser.parse_args()

    config_path = args.config or str(PROJECT_ROOT / 'config' / 'config.yaml')
    config = load_config(config_path)
    database = create_database_from_config(config)

    print(f"[backfill] RACE option=1  {args.from_date}..{args.to_date}")
    print("  JVLink から全期間の RACE データを取得します（WE/WH レコードを含む）")
    print("  ※ レコード数によっては数十分かかる場合があります")

    with database:
        processor = BatchProcessor(
            database=database,
            sid=config.get('jvlink.sid', 'JLTSQL'),
            batch_size=1000,
            service_key=config.get('jvlink.service_key'),
            show_progress=False,
        )
        stats = processor.process_date_range(
            data_spec='RACE',
            from_date=args.from_date,
            to_date=args.to_date,
            option=1,
            ensure_tables=False,
        )

    print(f"\n[backfill] 完了")
    print(f"  fetched={stats.get('records_fetched',0)}  "
          f"parsed={stats.get('records_parsed',0)}  "
          f"imported={stats.get('records_imported',0)}  "
          f"failed={stats.get('records_failed',0)}")

    # 取り込み後の件数確認
    import pg8000.native
    conn = pg8000.native.Connection(
        os.environ.get('POSTGRES_USER', 'postgres'),
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=int(os.environ.get('POSTGRES_PORT', 5432)),
        database=os.environ.get('POSTGRES_DATABASE', 'keiba'),
        password=os.environ.get('POSTGRES_PASSWORD', ''),
    )
    we_count = conn.run('SELECT COUNT(*) FROM nl_we')[0][0]
    wh_count = conn.run('SELECT COUNT(*) FROM nl_wh')[0][0]
    conn.close()

    print(f"\n  nl_we: {we_count} 件")
    print(f"  nl_wh: {wh_count} 件")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
