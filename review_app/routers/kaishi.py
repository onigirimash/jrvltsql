import datetime
import json
import urllib.request
from urllib.error import URLError

from fastapi import APIRouter, HTTPException
from review_app.database import get_conn, to_dict, to_dicts
from review_app.models import (
    JYO_MAP,
    KaishiBulkCreate,
    KaishiCreate,
    KaishiUpdate,
    TrackBiasUpsert,
    TrackConditionUpsert,
    WeatherCreate,
)

router = APIRouter(prefix="/kaishi", tags=["kaishi"])

# ============================================================
# 競馬場コード → 最寄りアメダス観測所番号（全10場）
# ============================================================
_AMEDAS_STATION_MAP: dict[str, str] = {
    '01': '14163',  # 札幌競馬場  → 札幌
    '02': '14141',  # 函館競馬場  → 函館
    '03': '36127',  # 福島競馬場  → 福島
    '04': '54232',  # 新潟競馬場  → 新潟
    '05': '44132',  # 東京競馬場  → 東京（府中市）
    '06': '45147',  # 中山競馬場  → 千葉（船橋市）
    '07': '51106',  # 中京競馬場  → 名古屋（豊明市）
    '08': '61286',  # 京都競馬場  → 京都（伏見区淀）
    '09': '62078',  # 阪神競馬場  → 大阪（宝塚市）
    '10': '82182',  # 小倉競馬場  → 北九州
}

# AMeDAS API で取得可能な最大日数（気象庁仕様）
_AMEDAS_VALID_DAYS = 14

# 競馬場コード → 気象庁 過去の気象データ検索用 (prec_no, block_no, 地点名)
# 気象官署の日別値ページ: daily_s1.php を使用
_JMA_HIST_MAP: dict[str, tuple[str, str, str]] = {
    '01': ('14', '47412', '札幌'),    # 札幌競馬場
    '02': ('11', '47430', '函館'),    # 函館競馬場
    '03': ('36', '47570', '福島'),    # 福島競馬場
    '04': ('54', '47604', '新潟'),    # 新潟競馬場
    '05': ('44', '47662', '東京'),    # 東京競馬場（府中）
    '06': ('45', '47682', '千葉'),    # 中山競馬場（船橋）
    '07': ('51', '47636', '名古屋'),  # 中京競馬場（豊明）
    '08': ('61', '47759', '京都'),    # 京都競馬場（淀）
    '09': ('62', '47772', '大阪'),    # 阪神競馬場（宝塚）
    '10': ('82', '47813', '北九州'),  # 小倉競馬場
}

# trackcd 先頭文字 → トラック種別
_GRADECD_MAP: dict[str, str] = {
    'A': 'G1', 'B': 'G2', 'C': 'G3', 'L': 'L',
}
_SYUBETUCD_MAP: dict[str, str] = {
    '11': '新馬', '12': '未勝利',
    '13': '1勝', '14': '2勝', '15': '3勝',
}
# AMeDAS 16方位コード → 8方位
_WIND_DIR_MAP: dict[int, str] = {
    0: '無風',
    1: '北', 2: '北東', 3: '北東',
    4: '東', 5: '東', 6: '東',
    7: '南東', 8: '南東',
    9: '南',
    10: '南西', 11: '南西',
    12: '西', 13: '西', 14: '西',
    15: '北西', 16: '北西',
}

# 気象庁 天気テキスト → nl_weather の weather_code
_JMA_WEATHER_CODE_MAP: dict[str, str] = {
    '快晴': '晴', '晴': '晴',
    '薄曇': '曇', '曇': '曇', '霧': '曇', '煙霧': '曇', '煙': '曇',
    '霧雨': '小雨', 'みぞれ': '小雨',
    'にわか雨': '雨', '雨': '雨', '雷雨': '雨', 'あられ': '雨',
    'にわか雪': '雪', '雪': '雪', '暴風雪': '雪', '大雪': '雪',
}

# 気象庁 風向（16方位） → 8方位
_JMA_WIND_DIR_MAP: dict[str, str] = {
    '北': '北', '北北東': '北東', '北東': '北東', '東北東': '東',
    '東': '東', '東南東': '東', '南東': '南東', '南南東': '南東',
    '南': '南', '南南西': '南西', '南西': '南西', '西南西': '西',
    '西': '西', '西北西': '北西', '北西': '北西', '北北西': '北',
    '静穏': '無風',
}

# ============================================================
# 追い風・向かい風判定
# ============================================================

# 各競馬場の最終直線で「向かい風」となる風向き
# （馬が走る向き＝ゴール方向から吹いてくる風）
# 地形等の影響で体感と異なる場合があるため、必要に応じて調整すること。
_STRAIGHT_HEADWIND: dict[str, str] = {
    '01': '西',    # 札幌: 直線はおおむね東→西  ／右回り
    '02': '西',    # 函館: 直線はおおむね東→西  ／右回り
    '03': '西',    # 福島: 直線はおおむね東→西  ／右回り
    '04': '南',    # 新潟: 直線はおおむね北→南  ／左回り（内・外共通）
    '05': '南',    # 東京: 直線はおおむね北→南  ／左回り
    '06': '北西',  # 中山: 直線はおおむね南東→北西（急坂）／右回り
    '07': '南',    # 中京: 直線はおおむね北→南  ／左回り
    '08': '北',    # 京都: 直線はおおむね南→北  ／右回り
    '09': '北',    # 阪神: 直線はおおむね南→北（急坂）／右回り
    '10': '東',    # 小倉: 直線はおおむね西→東  ／右回り
}

# 直線コース（新潟1000m等）固有の向かい風方向
# trackcd[1] == '4' のときに使用する
_STRAIGHT_COURSE_HEADWIND: dict[str, str] = {
    '04': '西',    # 新潟1000m直線: おおむね東→西方向
}

# 各風向きの正反対方向
_OPPOSITE_WIND: dict[str, str] = {
    '北': '南', '南': '北', '東': '西', '西': '東',
    '北東': '南西', '南西': '北東', '北西': '南東', '南東': '北西',
}

# 風向き → 角度（度）
_WIND_DIR_DEG: dict[str, int] = {
    '北': 0, '北東': 45, '東': 90, '南東': 135,
    '南': 180, '南西': 225, '西': 270, '北西': 315,
}


def assess_wind(
    jyo_cd: str,
    wind_direction: str | None,
    wind_speed: float | None = None,
    trackcd: str | None = None,
) -> str | None:
    """向かい風・追い風・横風・無風を判定して返す。

    Args:
        jyo_cd:         競馬場コード
        wind_direction: 風向き（例: '南'）
        wind_speed:     風速 m/s（0 または None は無風扱い）
        trackcd:        JV-Link トラックコード（直線コース検出に使用）
    Returns:
        '向かい風' | '追い風' | '横風' | '無風' | None（判定不能）
    """
    if not wind_direction or wind_direction == '無風' or float(wind_speed or 0) == 0:
        return '無風'

    # 直線コース判定（trackcd[1] == '4' → 直線）
    if trackcd and len(trackcd) >= 2 and trackcd[1] == '4':
        headwind_dir = _STRAIGHT_COURSE_HEADWIND.get(jyo_cd) or _STRAIGHT_HEADWIND.get(jyo_cd)
    else:
        headwind_dir = _STRAIGHT_HEADWIND.get(jyo_cd)

    if not headwind_dir:
        return None

    head_deg = _WIND_DIR_DEG.get(headwind_dir)
    wind_deg = _WIND_DIR_DEG.get(wind_direction)
    if head_deg is None or wind_deg is None:
        return '横風'

    diff = abs(head_deg - wind_deg) % 360
    if diff > 180:
        diff = 360 - diff

    if diff <= 45:
        return '向かい風'
    if diff >= 135:
        return '追い風'
    return '横風'


# hourly_s1.php テーブルの固定カラム位置（気象官署・標準構成）
# 0:時, 1:現地気圧, 2:海面気圧, 3:降水量, 4:気温, 5:露点温度, 6:蒸気圧,
# 7:湿度, 8:風速, 9:風向, 10:日照時間, 11:全天日射量,
# 12:降雪, 13:積雪, 14:天気, 15:雲量, 16:視程
_JMA_HOURLY_COL: dict[str, int] = {
    'precipitation':  3,
    'temperature':    4,
    'humidity':       7,
    'wind_speed':     8,
    'wind_direction': 9,
    'weather':        14,
}


# ============================================================
# Kaishi CRUD
# ============================================================

@router.get("")
def list_kaishi():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT k.*,
                   COUNT(DISTINCT r.id) AS race_count
            FROM   review_kaishi k
            LEFT JOIN review_race r ON r.kaishi_id = k.id
            GROUP BY k.id
            ORDER BY k.race_date DESC, k.jyo_cd
        """)
        return to_dicts(cur)


@router.post("", status_code=201)
def create_kaishi(body: KaishiCreate):
    jyo_name = JYO_MAP.get(body.jyo_cd)
    if not jyo_name:
        raise HTTPException(400, f"不明な競馬場コード: {body.jyo_cd}")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO review_kaishi (race_date, jyo_cd, jyo_name, kaiji, nichiji, memo)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (str(body.race_date), body.jyo_cd, jyo_name, body.kaiji, body.nichiji, body.memo))
        return to_dict(cur)


@router.get("/venues-for-date")
def venues_for_date(date: str):
    """nl_ra から指定日の開催競馬場一覧を返す。"""
    try:
        race_date = datetime.date.fromisoformat(date)
    except (ValueError, TypeError):
        raise HTTPException(400, "日付形式が不正です (YYYY-MM-DD)")

    year = race_date.year
    monthday = race_date.month * 100 + race_date.day

    with get_conn() as conn:
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT DISTINCT jyocd,
                       MAX(kaiji)   AS kaiji,
                       MAX(nichiji) AS nichiji
                FROM nl_ra
                WHERE year = %s AND monthday = %s
                GROUP BY jyocd
                ORDER BY jyocd
            """, (year, monthday))
            rows = to_dicts(cur)
        except Exception:
            try:
                cur.execute("""
                    SELECT DISTINCT jyocd
                    FROM nl_ra
                    WHERE year = %s AND monthday = %s
                    ORDER BY jyocd
                """, (year, monthday))
                rows = [{'jyocd': r[0], 'kaiji': None, 'nichiji': None} for r in cur.fetchall()]
            except Exception:
                return []

    result = []
    for r in rows:
        jyo_cd = str(r.get('jyocd', '')).zfill(2)
        jyo_name = JYO_MAP.get(jyo_cd)
        if not jyo_name:
            continue
        result.append({
            'jyo_cd': jyo_cd,
            'jyo_name': jyo_name,
            'kaiji': _to_int(r.get('kaiji')),
            'nichiji': _to_int(r.get('nichiji')),
        })
    return result


def _to_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


@router.post("/bulk", status_code=201)
def bulk_create_kaishi(body: KaishiBulkCreate):
    """複数競馬場を一括で開催作成する。既存レコードはスキップ。"""
    results = []
    with get_conn() as conn:
        cur = conn.cursor()
        for venue in body.venues:
            jyo_name = JYO_MAP.get(venue.jyo_cd)
            if not jyo_name:
                continue
            cur.execute("""
                INSERT INTO review_kaishi (race_date, jyo_cd, jyo_name, kaiji, nichiji)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (race_date, jyo_cd) DO NOTHING
                RETURNING *
            """, (str(body.race_date), venue.jyo_cd, jyo_name, venue.kaiji, venue.nichiji))
            row = to_dict(cur)
            if row:
                results.append(row)
    return results


@router.get("/{kaishi_id}")
def get_kaishi(kaishi_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM review_kaishi WHERE id = %s", (kaishi_id,))
        row = to_dict(cur)
        if not row:
            raise HTTPException(404, "開催が見つかりません")

        cur.execute("SELECT * FROM review_track_condition WHERE kaishi_id = %s ORDER BY track_type", (kaishi_id,))
        row["track_conditions"] = to_dicts(cur)

        cur.execute(
            "SELECT * FROM nl_weather WHERE race_date = %s AND jyo_cd = %s ORDER BY measurement_time",
            (row["race_date"], row["jyo_cd"]),
        )
        weathers = to_dicts(cur)
        for w in weathers:
            w["wind_assessment"] = assess_wind(
                row["jyo_cd"],
                w.get("wind_direction"),
                w.get("wind_speed"),
            )
        row["weathers"] = weathers

        cur.execute("SELECT * FROM review_track_bias WHERE kaishi_id = %s ORDER BY track_type, distance_category NULLS FIRST", (kaishi_id,))
        row["track_biases"] = to_dicts(cur)

        cur.execute("""
            SELECT r.*,
                   COUNT(d.id) AS disadvantage_count
            FROM   review_race r
            LEFT JOIN review_disadvantage d ON d.race_id = r.id
            WHERE  r.kaishi_id = %s
            GROUP BY r.id
            ORDER BY r.race_num
        """, (kaishi_id,))
        row["races"] = to_dicts(cur)

        return row


@router.put("/{kaishi_id}")
def update_kaishi(kaishi_id: int, body: KaishiUpdate):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE review_kaishi
            SET kaiji = %s, nichiji = %s, memo = %s
            WHERE id = %s
            RETURNING *
        """, (body.kaiji, body.nichiji, body.memo, kaishi_id))
        row = to_dict(cur)
        if not row:
            raise HTTPException(404, "開催が見つかりません")
        return row


@router.delete("/{kaishi_id}", status_code=204)
def delete_kaishi(kaishi_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_kaishi WHERE id = %s", (kaishi_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "開催が見つかりません")


# ============================================================
# Track Condition
# ============================================================

@router.post("/{kaishi_id}/track-condition", status_code=201)
def upsert_track_condition(kaishi_id: int, body: TrackConditionUpsert):
    _require_kaishi(kaishi_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO review_track_condition
                (kaishi_id, track_type, cushion_value, moisture_rate, maintenance_status, going_description)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (kaishi_id, track_type) DO UPDATE SET
                cushion_value      = EXCLUDED.cushion_value,
                moisture_rate      = EXCLUDED.moisture_rate,
                maintenance_status = EXCLUDED.maintenance_status,
                going_description  = EXCLUDED.going_description
            RETURNING *
        """, (
            kaishi_id, body.track_type, body.cushion_value,
            body.moisture_rate, body.maintenance_status, body.going_description,
        ))
        return to_dict(cur)


@router.delete("/track-condition/{tc_id}", status_code=204)
def delete_track_condition(tc_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_track_condition WHERE id = %s", (tc_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "馬場情報が見つかりません")


# ============================================================
# Weather
# ============================================================

@router.post("/{kaishi_id}/weather", status_code=201)
def create_weather(kaishi_id: int, body: WeatherCreate):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_date, jyo_cd FROM review_kaishi WHERE id = %s", (kaishi_id,))
        k = cur.fetchone()
        if not k:
            raise HTTPException(404, "開催が見つかりません")
        race_date, jyo_cd = k
        cur.execute("""
            INSERT INTO nl_weather
                (race_date, jyo_cd, measurement_time, weather_code, wind_speed,
                 wind_direction, temperature, precipitation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (race_date, jyo_cd, measurement_time) DO UPDATE SET
                weather_code   = EXCLUDED.weather_code,
                wind_speed     = EXCLUDED.wind_speed,
                wind_direction = EXCLUDED.wind_direction,
                temperature    = EXCLUDED.temperature,
                precipitation  = EXCLUDED.precipitation
            RETURNING *
        """, (
            race_date, jyo_cd, body.measurement_time, body.weather_code,
            body.wind_speed, body.wind_direction, body.temperature, body.precipitation,
        ))
        return to_dict(cur)


@router.delete("/weather/{w_id}", status_code=204)
def delete_weather(w_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM nl_weather WHERE id = %s", (w_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "天候情報が見つかりません")


# ============================================================
# Track Bias
# ============================================================

@router.post("/{kaishi_id}/track-bias", status_code=201)
def upsert_track_bias(kaishi_id: int, body: TrackBiasUpsert):
    _require_kaishi(kaishi_id)
    with get_conn() as conn:
        cur = conn.cursor()
        # distance_category が NULL かどうかで条件を変える（NULL-safe upsert）
        if body.distance_category is None:
            cur.execute(
                "SELECT id FROM review_track_bias WHERE kaishi_id=%s AND track_type=%s AND distance_category IS NULL",
                (kaishi_id, body.track_type),
            )
        else:
            cur.execute(
                "SELECT id FROM review_track_bias WHERE kaishi_id=%s AND track_type=%s AND distance_category=%s",
                (kaishi_id, body.track_type, body.distance_category),
            )
        existing = cur.fetchone()

        if existing:
            cur.execute("""
                UPDATE review_track_bias SET
                    inside_outside_score     = %s,
                    inside_outside_label     = %s,
                    front_back_score         = %s,
                    front_back_label         = %s,
                    bias_detail              = %s,
                    notes                    = %s,
                    pace_comment             = %s,
                    benefited_running_style  = %s
                WHERE id = %s
                RETURNING *
            """, (
                body.inside_outside_score, body.inside_outside_label,
                body.front_back_score, body.front_back_label,
                body.bias_detail, body.notes,
                body.pace_comment, body.benefited_running_style,
                existing[0],
            ))
        else:
            cur.execute("""
                INSERT INTO review_track_bias
                    (kaishi_id, track_type, distance_category,
                     inside_outside_score, inside_outside_label,
                     front_back_score, front_back_label,
                     bias_detail, notes,
                     pace_comment, benefited_running_style)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (
                kaishi_id, body.track_type, body.distance_category,
                body.inside_outside_score, body.inside_outside_label,
                body.front_back_score, body.front_back_label,
                body.bias_detail, body.notes,
                body.pace_comment, body.benefited_running_style,
            ))
        return to_dict(cur)


@router.delete("/track-bias/{tb_id}", status_code=204)
def delete_track_bias(tb_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_track_bias WHERE id = %s", (tb_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "バイアス情報が見つかりません")


# ============================================================
# nl_ra からレース情報を自動取得
# ============================================================

@router.get("/{kaishi_id}/nl-races")
def fetch_nl_races(kaishi_id: int):
    """開催日・競馬場に対応する nl_ra のレース一覧を返す。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_date, jyo_cd FROM review_kaishi WHERE id = %s", (kaishi_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "開催が見つかりません")
        race_date, jyo_cd = row
        year     = race_date.year
        monthday = race_date.month * 100 + race_date.day

        cur.execute("""
            SELECT racenum, hondai, kyori, trackcd, gradecd, syubetucd
            FROM   nl_ra
            WHERE  year = %s AND monthday = %s AND jyocd = %s
            ORDER  BY racenum
        """, (year, monthday, jyo_cd))
        rows = to_dicts(cur)

    return [_map_nl_race(r) for r in rows]


def _min100_to_secs(t: float) -> float:
    """nl_se.time の min×100+sec 形式（例: 145.9）を秒（例: 105.9）に変換する。"""
    m = int(t / 100)
    return m * 60 + (t - m * 100)


@router.get("/{kaishi_id}/pace-stats")
def get_pace_stats(kaishi_id: int):
    """PCI ベースのペース判定を返す。

    データソース（優先順位）:
      前半Ave-3F : nl_target_race.mae3f   (通過3F = 前半600mタイム、直接使用)
      上がり3F   : nl_target_race.agari3f → nl_ra.haron3l
      PCI        : nl_target_race.race_pci (TARGET独自算法。PCI3≒race_pci)

    フォールバック（TARGET未取得時のみ）:
      前半Ave-3F = (走破タイム中央値[秒] - 上がり3F) × 600 ÷ (距離 - 600)
      PCI        = 上がり3F ÷ (通過3F + 上がり3F) × 100
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_date, jyo_cd FROM review_kaishi WHERE id = %s", (kaishi_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "開催が見つかりません")
        race_date, jyo_cd = row

        if isinstance(race_date, datetime.datetime):
            race_date = race_date.date()
        elif isinstance(race_date, str):
            race_date = datetime.date.fromisoformat(race_date)

        year     = race_date.year
        monthday = race_date.month * 100 + race_date.day

        try:
            cur.execute("""
                SELECT
                    ra.racenum,
                    ra.kyori,
                    ra.trackcd,
                    ra.haron3l,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY se.time) AS median_time,
                    tr.race_pci  AS target_pci,
                    tr.agari3f   AS target_agari3f,
                    tr.mae3f     AS target_mae3f
                FROM nl_ra ra
                LEFT JOIN nl_se se
                    ON  se.year     = ra.year
                    AND se.monthday = ra.monthday
                    AND se.jyocd    = ra.jyocd
                    AND se.kaiji    = ra.kaiji
                    AND se.nichiji  = ra.nichiji
                    AND se.racenum  = ra.racenum
                    AND se.time     > 0
                LEFT JOIN nl_target_race tr
                    ON  tr.race_date = ra.year::text || LPAD(ra.monthday::text, 4, '0')
                    AND tr.jyo_cd    = ra.jyocd
                    AND tr.racenum   = ra.racenum
                WHERE ra.year = %s AND ra.monthday = %s AND ra.jyocd = %s
                GROUP BY ra.racenum, ra.kyori, ra.trackcd, ra.haron3l,
                         tr.race_pci, tr.agari3f, tr.mae3f
                ORDER BY ra.racenum
            """, (year, monthday, jyo_cd))
            races = to_dicts(cur)
        except Exception:
            return []

        results = []
        for r in races:
            kyori   = r.get('kyori') or 0
            trackcd = str(r.get('trackcd') or '').strip()

            if not kyori or int(kyori) <= 600:
                continue

            # 上がり3F: nl_target_race.agari3f を優先、なければ nl_ra.haron3l
            target_agari3f = float(r.get('target_agari3f') or 0)
            haron3l        = float(r.get('haron3l') or 0)
            agari3f        = target_agari3f if target_agari3f > 0 else haron3l

            # 前半Ave-3F: TARGET mae3f (通過3F=前半600mタイム) を直接使用
            target_mae3f = float(r.get('target_mae3f') or 0)

            # 走破タイム中央値: フォールバック用（TARGET未取得時のみ使用）
            raw_median = r.get('median_time')
            median_time: float | None = None
            if raw_median:
                median_time = _min100_to_secs(float(raw_median))

            target_pci = float(r.get('target_pci') or 0) if r.get('target_pci') else None

            front_half_3f: float | None = None
            pci:           float | None = None
            pace_judge:    str | None   = None

            if target_mae3f > 0:
                # TARGET mae3f = 通過3F = 前半600mタイム（直接使用、計算不要）
                front_half_3f = round(target_mae3f, 1)
            elif median_time and agari3f > 0 and int(kyori) > 600:
                # フォールバック: 走破タイム中央値から推計（精度低）
                front_half_3f = round(
                    (median_time - agari3f) * 600 / (int(kyori) - 600), 1
                )

            # PCI: TARGET race_pci を直接使用（TARGET独自算法で計算済み）
            # フォールバック: 通過3F + 上がり3F から計算
            if target_pci and target_pci > 0:
                pci = round(target_pci, 1)
            elif front_half_3f is not None and agari3f > 0:
                denom = front_half_3f + agari3f
                if denom > 0:
                    pci = round(agari3f / denom * 100, 1)

            if pci is not None:
                if pci < 47:
                    pace_judge = 'ハイペース'
                elif pci > 53:
                    pace_judge = 'スローペース'
                else:
                    pace_judge = 'ミドルペース'

            avg_pci = _get_course_avg_pci(cur, jyo_cd, int(kyori), trackcd[:1], year)

            results.append({
                'race_num':      r['racenum'],
                'distance':      kyori,
                'track_cd':      trackcd,
                'median_time':   round(median_time, 2) if median_time else None,
                'last_3f':       round(agari3f, 2) if agari3f > 0 else None,
                'front_half_3f': front_half_3f,
                'pci':           pci,
                'pace_judge':    pace_judge,
                'avg_pci':       avg_pci['avg_pci']      if avg_pci else None,
                'sample_count':  avg_pci['sample_count'] if avg_pci else 0,
            })

        return results


def _get_course_avg_pci(cur, jyo_cd: str, kyori: int, track_prefix: str, cur_year: int) -> dict | None:
    """過去5年の同コース平均PCIを返す (nl_target_race.race_pci ベース)。"""
    if kyori <= 600:
        return None
    _surface_map = {'1': 'T', '2': 'D', '5': 'J'}
    surface = _surface_map.get(track_prefix)
    if not surface:
        return None
    try:
        cur.execute("""
            SELECT
                ROUND(AVG(race_pci)::numeric, 1) AS avg_pci,
                COUNT(*)                          AS sample_count
            FROM nl_target_race
            WHERE jyo_cd  = %s
              AND distance = %s
              AND surface  = %s
              AND race_pci > 0
              AND race_date >= %s
              AND race_date <= %s
        """, (
            jyo_cd, kyori, surface,
            f"{cur_year - 5}0101",
            f"{cur_year - 1}1231",
        ))
        row = cur.fetchone()
    except Exception:
        return None

    if not row:
        return None
    if isinstance(row, dict):
        avg_pci = row.get('avg_pci')
        n       = row.get('sample_count') or 0
    else:
        avg_pci, n = row[0], row[1]

    if not avg_pci or int(n) == 0:
        return None
    return {
        'avg_pci':      float(avg_pci),
        'sample_count': int(n),
    }


def _map_nl_race(r: dict) -> dict:
    trackcd   = r.get('trackcd') or ''
    gradecd   = r.get('gradecd') or ''
    syubetucd = r.get('syubetucd') or ''

    first = trackcd[:1]
    if first == '1':
        track_type = '芝'
    elif first == '2':
        track_type = 'ダート'
    elif first == '5':
        track_type = '障害'
    else:
        track_type = None

    if gradecd in _GRADECD_MAP:
        grade = _GRADECD_MAP[gradecd]
    else:
        grade = _SYUBETUCD_MAP.get(syubetucd, 'OP')

    raw_name  = r.get('hondai')
    race_name = _recover_jp(raw_name)

    return {
        'race_num':   r['racenum'],
        'race_name':  race_name,
        'distance':   r.get('kyori'),
        'track_type': track_type,
        'grade':      grade,
    }


def _recover_jp(val: str | None) -> str | None:
    """CP932 バイト列が latin-1 として誤デコードされた場合の復元を試みる。"""
    if not val:
        return None
    try:
        return val.encode('latin-1').decode('cp932')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return val


# ============================================================
# 気象庁アメダス API から天候データを取得
# ============================================================

@router.get("/{kaishi_id}/fetch-weather")
def fetch_amedas_weather(kaishi_id: int):
    """気象庁アメダス API から開催日の気象データを取得して返す。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_date, jyo_cd FROM review_kaishi WHERE id = %s", (kaishi_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "開催が見つかりません")
        race_date, jyo_cd = row

    station_id = _AMEDAS_STATION_MAP.get(jyo_cd)
    if not station_id:
        raise HTTPException(400, f"競馬場コード {jyo_cd} のアメダス観測所が未定義です")

    # psycopg2 は DATE を datetime.date で返すが念のため正規化
    if isinstance(race_date, datetime.datetime):
        race_date = race_date.date()
    elif isinstance(race_date, str):
        race_date = datetime.date.fromisoformat(race_date)

    # AMeDAS API は直近 _AMEDAS_VALID_DAYS 日分のみ公開
    cutoff = datetime.date.today() - datetime.timedelta(days=_AMEDAS_VALID_DAYS)
    if race_date < cutoff:
        raise HTTPException(422, detail=_build_too_old_detail(jyo_cd, race_date))

    date_str = race_date.strftime('%Y%m%d')
    results  = []

    for hour in [10, 12, 15]:
        data = _fetch_amedas_point(station_id, date_str, hour)
        if not data:
            continue
        for time_key in sorted(data.keys()):
            entry = _parse_amedas_obs(time_key, data[time_key])
            if entry:
                results.append(entry)
                break  # 各時間帯の最初の観測値のみ取得

    if not results:
        msg = (
            f"{race_date} のアメダスデータが見つかりませんでした。"
            "取得可能期間外か、データが未公開の可能性があります。"
            "気象庁の過去データから手動で確認してください。"
        )
        raise HTTPException(422, detail=_build_too_old_detail(jyo_cd, race_date, msg))

    return results


def _build_too_old_detail(jyo_cd: str, race_date: datetime.date, message: str | None = None) -> dict:
    hist = _JMA_HIST_MAP.get(jyo_cd)
    detail: dict = {
        "code": "DATA_TOO_OLD",
        "message": message or (
            f"アメダスAPIで取得できるのは直近{_AMEDAS_VALID_DAYS}日分のみです。"
            f"{race_date} のデータは気象庁の過去データから手動で確認してください。"
        ),
    }
    if hist:
        prec_no, block_no, station_name = hist
        url = (
            "https://www.data.jma.go.jp/obd/stats/etrn/view/daily_s1.php"
            f"?prec_no={prec_no}&block_no={block_no}"
            f"&year={race_date.year}&month={race_date.month}&day={race_date.day}&view=p1"
        )
        detail["url"] = url
        detail["station_name"] = station_name
    return detail


def _fetch_amedas_point(station_id: str, date_str: str, hour: int) -> dict | None:
    url = (
        f"https://www.jma.go.jp/bosai/amedas/data/point"
        f"/{station_id}/{date_str}_{hour:02d}0000.json"
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except (URLError, Exception):
        return None


def _parse_amedas_obs(time_key: str, obs: dict) -> dict | None:
    try:
        hh, mm = time_key[8:10], time_key[10:12]

        def val(key):
            arr = obs.get(key)
            return arr[0] if arr else None

        temperature   = val('temp')
        wind_speed    = val('wind')
        dir_code      = val('windDirection')
        precipitation = val('precipitation10m') or val('precipitation1h') or 0.0
        sunshine      = val('sun10m') or 0

        wind_direction = _WIND_DIR_MAP.get(int(dir_code)) if dir_code is not None else None

        if precipitation and float(precipitation) > 0:
            weather_code = '雨'
        elif sunshine and float(sunshine) > 0:
            weather_code = '晴'
        else:
            weather_code = '曇'

        return {
            'measurement_time': f"{hh}:{mm}",
            'weather_code':     weather_code,
            'temperature':      temperature,
            'wind_speed':       wind_speed,
            'wind_direction':   wind_direction,
            'precipitation':    float(precipitation) if precipitation else 0.0,
        }
    except Exception:
        return None


# ============================================================
# 気象庁 時別値ページ スクレイピング（日付制限なし）
# ============================================================

@router.get("/{kaishi_id}/fetch-weather-jma")
def fetch_weather_jma(kaishi_id: int):
    """気象庁の時別値ページをスクレイピングして10・12・15時のデータを返す。

    AMeDAS API（直近14日限定）と異なり過去データにもアクセス可能。
    1競馬場につき1回のHTTPアクセスで全時点を取得する。
    """
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT race_date, jyo_cd FROM review_kaishi WHERE id = %s", (kaishi_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "開催が見つかりません")
        race_date, jyo_cd = row

    if isinstance(race_date, datetime.datetime):
        race_date = race_date.date()
    elif isinstance(race_date, str):
        race_date = datetime.date.fromisoformat(race_date)

    results = fetch_jma_weather_data(jyo_cd, race_date)
    if not results:
        hist = _JMA_HIST_MAP.get(jyo_cd)
        detail: dict = {"code": "SCRAPE_FAILED", "message": "気象庁ページからデータを取得できませんでした。"}
        if hist:
            prec_no, block_no, station_name = hist
            detail["url"] = (
                "https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php"
                f"?prec_no={prec_no}&block_no={block_no}"
                f"&year={race_date.year}&month={race_date.month}&day={race_date.day}&view=p1"
            )
            detail["station_name"] = station_name
        raise HTTPException(422, detail=detail)

    return results


def fetch_jma_weather_data(jyo_cd: str, race_date: datetime.date) -> list[dict]:
    """気象庁 時別値ページを1回スクレイピングして10・12・15時のデータを返す。

    Returns:
        [{'measurement_time': 'HH:MM', 'weather_code': ..., 'temperature': ...,
          'wind_speed': ..., 'wind_direction': ..., 'precipitation': ...}, ...]
    """
    hist = _JMA_HIST_MAP.get(jyo_cd)
    if not hist:
        return []

    prec_no, block_no, _ = hist
    url = (
        "https://www.data.jma.go.jp/obd/stats/etrn/view/hourly_s1.php"
        f"?prec_no={prec_no}&block_no={block_no}"
        f"&year={race_date.year}&month={race_date.month}&day={race_date.day}&view=p1"
    )

    hour_data = _scrape_jma_hourly_page(url)

    results = []
    for hour in [10, 12, 15]:
        data = hour_data.get(hour)
        if data is None:
            continue

        weather_text = (data.get('weather') or '').strip()
        weather_code = _JMA_WEATHER_CODE_MAP.get(weather_text)
        if weather_code is None:
            precip = data.get('precipitation') or 0.0
            weather_code = '雨' if float(precip) > 0 else '曇'

        wind_dir_raw  = (data.get('wind_direction') or '').strip()
        wind_direction = _JMA_WIND_DIR_MAP.get(wind_dir_raw) or (wind_dir_raw or None)

        results.append({
            'measurement_time': f"{hour:02d}:00",
            'weather_code':     weather_code,
            'temperature':      data.get('temperature'),
            'wind_speed':       data.get('wind_speed'),
            'wind_direction':   wind_direction,
            'precipitation':    float(data['precipitation']) if data.get('precipitation') else 0.0,
        })

    return results


def _scrape_jma_hourly_page(url: str) -> dict[int, dict]:
    """気象庁 hourly_s1.php をスクレイピングして {時: {フィールド: 値}} を返す。

    beautifulsoup4 が未インストールの場合は空辞書を返す。
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return {}

    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (compatible; keiba-weather/1.0)'}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except (URLError, Exception):
        return {}

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', id='tablefix1')
    if not table:
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

        def _cell(idx: int) -> str | None:
            if idx >= len(tds):
                return None
            t = tds[idx].get_text(strip=True)
            return None if t in ('--', '×', '////', '', '///') else t

        def _float(idx: int) -> float | None:
            t = _cell(idx)
            try:
                return float(t) if t is not None else None
            except ValueError:
                return None

        col = _JMA_HOURLY_COL
        result[hour] = {
            'precipitation':  _float(col['precipitation']),
            'temperature':    _float(col['temperature']),
            'humidity':       _float(col['humidity']),
            'wind_speed':     _float(col['wind_speed']),
            'wind_direction': _cell(col['wind_direction']),
            'weather':        _cell(col['weather']),
        }

    return result


# ============================================================
# Helper
# ============================================================

def _require_kaishi(kaishi_id: int) -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM review_kaishi WHERE id = %s", (kaishi_id,))
        if not cur.fetchone():
            raise HTTPException(404, "開催が見つかりません")
