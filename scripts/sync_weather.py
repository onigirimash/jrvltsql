#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
先週土日の全開催競馬場の気象データを気象庁からスクレイピングして
nl_weather テーブルへ保存する。

weekly_thursday_sync.ps1 から呼び出されることを想定。
nl_ra にレースが存在する競馬場を対象とし、review_kaishi レコードが
なければ自動作成する。

Usage:
    py -3.12-32 scripts/sync_weather.py [options]

    --dates YYYYMMDD [YYYYMMDD ...]  対象日付（省略時は先週土日）
    --pg-host / --pg-port / ...      DB接続設定（省略時は環境変数）

Requires:
    pip install beautifulsoup4
"""

import argparse
import datetime
import os
import sys
import time
import urllib.request
from urllib.error import URLError

import pg8000.dbapi as pg

# ============================================================
# 定数（review_app/routers/kaishi.py と同期すること）
# ============================================================

JYO_MAP: dict[str, str] = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟',
    '05': '東京', '06': '中山', '07': '中京', '08': '京都',
    '09': '阪神', '10': '小倉',
}

_JMA_HIST_MAP: dict[str, tuple[str, str, str]] = {
    '01': ('14', '47412', '札幌'),
    '02': ('11', '47430', '函館'),
    '03': ('36', '47570', '福島'),
    '04': ('54', '47604', '新潟'),
    '05': ('44', '47662', '東京'),
    '06': ('45', '47682', '千葉'),
    '07': ('51', '47636', '名古屋'),
    '08': ('61', '47759', '京都'),
    '09': ('62', '47772', '大阪'),
    '10': ('82', '47813', '北九州'),
}

_JMA_WEATHER_CODE_MAP: dict[str, str] = {
    '快晴': '晴', '晴': '晴',
    '薄曇': '曇', '曇': '曇', '霧': '曇', '煙霧': '曇', '煙': '曇',
    '霧雨': '小雨', 'みぞれ': '小雨',
    'にわか雨': '雨', '雨': '雨', '雷雨': '雨', 'あられ': '雨',
    'にわか雪': '雪', '雪': '雪', '暴風雪': '雪', '大雪': '雪',
}

_JMA_WIND_DIR_MAP: dict[str, str] = {
    '北': '北', '北北東': '北東', '北東': '北東', '東北東': '東',
    '東': '東', '東南東': '東', '南東': '南東', '南南東': '南東',
    '南': '南', '南南西': '南西', '南西': '南西', '西南西': '西',
    '西': '西', '西北西': '北西', '北西': '北西', '北北西': '北',
    '静穏': '無風',
}

# hourly_s1.php 標準カラム位置（気象官署）
# 0:時, 1:現地気圧, 2:海面気圧, 3:降水量, 4:気温, 5:露点温度, 6:蒸気圧,
# 7:湿度, 8:風速, 9:風向, 10:日照, 11:全天日射, 12:降雪, 13:積雪, 14:天気
_COL_PRECIPITATION  = 3
_COL_TEMPERATURE    = 4
_COL_HUMIDITY       = 7
_COL_WIND_SPEED     = 8
_COL_WIND_DIRECTION = 9
_COL_WEATHER        = 14

_TARGET_HOURS = [10, 12, 15]

# 競馬場間のアクセス間隔（秒）
_ACCESS_INTERVAL_SEC = 2


# ============================================================
# 日付ユーティリティ
# ============================================================

def get_last_weekend_dates() -> list[datetime.date]:
    """先週の土曜・日曜を返す（任意の曜日から呼び出し可能）。

    weekday(): Monday=0, ..., Saturday=5, Sunday=6
    直近の「日曜の翌日」が月曜なので、today - (weekday+1) が直近の日曜。
    """
    today = datetime.date.today()
    last_sunday   = today - datetime.timedelta(days=today.weekday() + 1)
    last_saturday = last_sunday - datetime.timedelta(days=1)
    return [last_saturday, last_sunday]


# ============================================================
# スクレイピング
# ============================================================

def scrape_jma_hourly(url: str) -> dict[int, dict]:
    """気象庁 hourly_s1.php を1回取得して {時: {フィールド: 値}} を返す。"""
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        print(
            "[ERROR] beautifulsoup4 が見つかりません。\n"
            "        py -3.12-32 -m pip install beautifulsoup4 を実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    req = urllib.request.Request(
        url, headers={'User-Agent': 'Mozilla/5.0 (compatible; keiba-weather-sync/1.0)'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except URLError as exc:
        print(f"[WARN] HTTP取得失敗: {exc}", file=sys.stderr)
        return {}
    except Exception as exc:
        print(f"[WARN] 予期しないエラー: {exc}", file=sys.stderr)
        return {}

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id='tablefix1')
    if not table:
        print("[WARN] テーブル(id=tablefix1)が見つかりません", file=sys.stderr)
        return {}

    result: dict[int, dict] = {}
    for row in table.find_all('tr'):
        tds = row.find_all('td')
        if not tds:
            continue
        try:
            hour = int(tds[0].get_text(strip=True))
        except ValueError:
            continue

        def cell(idx: int) -> 'str | None':
            if idx >= len(tds):
                return None
            t = tds[idx].get_text(strip=True)
            return None if t in ('--', '×', '////', '', '///') else t

        def to_float(idx: int) -> 'float | None':
            t = cell(idx)
            try:
                return float(t) if t is not None else None
            except ValueError:
                return None

        result[hour] = {
            'precipitation':  to_float(_COL_PRECIPITATION),
            'temperature':    to_float(_COL_TEMPERATURE),
            'humidity':       to_float(_COL_HUMIDITY),
            'wind_speed':     to_float(_COL_WIND_SPEED),
            'wind_direction': cell(_COL_WIND_DIRECTION),
            'weather':        cell(_COL_WEATHER),
        }

    return result


def fetch_weather_for_venue(jyo_cd: str, race_date: datetime.date) -> list[dict]:
    """1競馬場につき1回のHTTPアクセスで10・12・15時のデータを返す。"""
    hist = _JMA_HIST_MAP.get(jyo_cd)
    if not hist:
        print(f"[WARN] 競馬場コード {jyo_cd} の観測所が未定義", file=sys.stderr)
        return []

    prec_no, block_no, station_name = hist
    url = (
        "https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php"
        f"?prec_no={prec_no}&block_no={block_no}"
        f"&year={race_date.year}&month={race_date.month}&day={race_date.day}&view=p1"
    )
    print(f"    アクセス: {station_name} ({url})")

    hour_data = scrape_jma_hourly(url)
    if not hour_data:
        return []

    records = []
    for hour in _TARGET_HOURS:
        data = hour_data.get(hour)
        if data is None:
            print(f"    [WARN] {hour}時のデータなし")
            continue

        weather_text = (data.get('weather') or '').strip()
        weather_code = _JMA_WEATHER_CODE_MAP.get(weather_text)
        if weather_code is None:
            precip = data.get('precipitation') or 0.0
            weather_code = '雨' if float(precip) > 0 else '曇'

        wind_dir_raw  = (data.get('wind_direction') or '').strip()
        wind_direction = _JMA_WIND_DIR_MAP.get(wind_dir_raw) or (wind_dir_raw or None)

        records.append({
            'measurement_time': f"{hour:02d}:00",
            'weather_code':     weather_code,
            'temperature':      data.get('temperature'),
            'wind_speed':       data.get('wind_speed'),
            'wind_direction':   wind_direction,
            'precipitation':    data.get('precipitation') or 0.0,
        })

    return records


# ============================================================
# DB 操作
# ============================================================

def find_venues_for_date(cur, race_date: datetime.date) -> list[str]:
    """nl_ra からその日に開催があった競馬場コード一覧を返す。"""
    year     = race_date.year
    monthday = race_date.month * 100 + race_date.day
    try:
        cur.execute(
            "SELECT DISTINCT jyocd FROM nl_ra WHERE year = %s AND monthday = %s ORDER BY jyocd",
            (year, monthday),
        )
        return [row[0] for row in cur.fetchall()]
    except Exception as exc:
        print(f"[WARN] nl_ra クエリ失敗 ({exc}) - review_kaishi を直接参照します", file=sys.stderr)
        return []


def ensure_kaishi(conn, race_date: datetime.date, jyo_cd: str) -> 'int | None':
    """review_kaishi レコードを取得または作成してIDを返す。"""
    cur = conn.cursor()
    jyo_name = JYO_MAP.get(jyo_cd, jyo_cd)
    date_str = str(race_date)

    cur.execute(
        "INSERT INTO review_kaishi (race_date, jyo_cd, jyo_name) VALUES (%s, %s, %s)"
        " ON CONFLICT (race_date, jyo_cd) DO NOTHING RETURNING id",
        (date_str, jyo_cd, jyo_name),
    )
    row = cur.fetchone()
    if row:
        conn.commit()
        return row[0]

    cur.execute(
        "SELECT id FROM review_kaishi WHERE race_date = %s AND jyo_cd = %s",
        (date_str, jyo_cd),
    )
    row = cur.fetchone()
    return row[0] if row else None


def save_weather_records(conn, race_date: datetime.date, jyo_cd: str, records: list[dict]) -> int:
    """nl_weather へ保存し、挿入件数を返す（重複時はスキップ）。"""
    cur   = conn.cursor()
    saved = 0
    for rec in records:
        cur.execute("""
            INSERT INTO nl_weather
                (race_date, jyo_cd, measurement_time, weather_code, wind_speed,
                 wind_direction, temperature, precipitation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (race_date, jyo_cd, measurement_time) DO NOTHING
        """, (
            str(race_date), jyo_cd,
            rec['measurement_time'],
            rec['weather_code'],
            rec.get('wind_speed'),
            rec.get('wind_direction'),
            rec.get('temperature'),
            rec.get('precipitation', 0.0),
        ))
        if cur.rowcount > 0:
            saved += 1
        else:
            print(f"    [SKIP] {rec['measurement_time']} は既存")

    conn.commit()
    return saved


# ============================================================
# メイン
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description='先週土日の気象データを気象庁からスクレイピングして DB へ保存'
    )
    parser.add_argument(
        '--dates', nargs='+', metavar='YYYYMMDD',
        help='対象日付（省略時は先週土日）',
    )
    parser.add_argument('--pg-host',     default=os.environ.get('POSTGRES_HOST',     'localhost'))
    parser.add_argument('--pg-port',     type=int, default=int(os.environ.get('POSTGRES_PORT', 5432)))
    parser.add_argument('--pg-database', default=os.environ.get('POSTGRES_DATABASE', 'keiba'))
    parser.add_argument('--pg-user',     default=os.environ.get('POSTGRES_USER',     'postgres'))
    parser.add_argument('--pg-password', default=os.environ.get('POSTGRES_PASSWORD',
                        os.environ.get('PGPASSWORD', '')))
    args = parser.parse_args()

    # 対象日付を確定
    if args.dates:
        target_dates = [
            datetime.date(int(d[:4]), int(d[4:6]), int(d[6:8])) for d in args.dates
        ]
    else:
        target_dates = get_last_weekend_dates()

    print(f"対象日付: {[str(d) for d in target_dates]}")

    # DB接続
    conn = pg.connect(
        host=args.pg_host, port=args.pg_port,
        database=args.pg_database, user=args.pg_user, password=args.pg_password,
    )

    total_saved = 0

    for race_date in target_dates:
        print(f"\n=== {race_date} ===")

        # nl_ra から開催競馬場を取得
        cur = conn.cursor()
        jyo_cds = find_venues_for_date(cur, race_date)

        # nl_ra に該当なければ review_kaishi を直接参照
        if not jyo_cds:
            cur.execute(
                "SELECT jyo_cd FROM review_kaishi WHERE race_date = %s ORDER BY jyo_cd",
                (str(race_date),),
            )
            jyo_cds = [row[0] for row in cur.fetchall()]

        if not jyo_cds:
            print(f"  {race_date} の開催データなし（nl_ra / review_kaishi どちらも0件）")
            continue

        print(f"  開催競馬場: {[JYO_MAP.get(c, c) for c in jyo_cds]}")

        for idx, jyo_cd in enumerate(jyo_cds):
            jyo_name = JYO_MAP.get(jyo_cd, jyo_cd)
            print(f"\n  [{jyo_name}]")

            # review_kaishi も作成して回顧 UI から参照できるようにする
            ensure_kaishi(conn, race_date, jyo_cd)

            weather_records = fetch_weather_for_venue(jyo_cd, race_date)
            if not weather_records:
                print(f"    [WARN] 気象データ取得失敗 - スキップ")
            else:
                saved = save_weather_records(conn, race_date, jyo_cd, weather_records)
                print(f"    => {saved}件 保存")
                total_saved += saved

            # 次の競馬場へのアクセス前に待機（過度なアクセスを避ける）
            if idx < len(jyo_cds) - 1:
                time.sleep(_ACCESS_INTERVAL_SEC)

    conn.close()
    print(f"\n完了: 合計 {total_saved} 件保存")


if __name__ == '__main__':
    main()
