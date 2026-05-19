from fastapi import APIRouter, HTTPException, Query

from prediction_app.database import get_conn, to_dicts

router = APIRouter()

JYO_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

SURFACE_MAP = {"1": "芝", "2": "ダート", "3": "障害"}


def _parse_date(date_str: str) -> tuple[int, int]:
    """'YYYYMMDD' -> (year:int, monthday:int)  e.g. '20241228' -> (2024, 1228)"""
    if len(date_str) != 8 or not date_str.isdigit():
        raise HTTPException(400, f"date は YYYYMMDD 形式で指定してください: {date_str}")
    return int(date_str[:4]), int(date_str[4:])


# ── GET /api/dates ─────────────────────────────────────────────────────────
@router.get("/api/dates")
def get_dates():
    """期待値データがある直近120件のレース日一覧を返す。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT year, monthday
            FROM nl_race_prediction
            WHERE expected_value IS NOT NULL
            ORDER BY year DESC, monthday DESC
            LIMIT 120
        """)
        rows = to_dicts(cur)
    return [
        {
            "value": f"{r['year']}{r['monthday']:04d}",
            "label": f"{r['year']}/{r['monthday'] // 100:02d}/{r['monthday'] % 100:02d}",
        }
        for r in rows
    ]


# ── GET /api/venues ─────────────────────────────────────────────────────────
@router.get("/api/venues")
def get_venues(date: str = Query(..., min_length=8, max_length=8)):
    """指定日に期待値データがある競馬場一覧を返す。"""
    year, monthday = _parse_date(date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT jyocd
            FROM nl_race_prediction
            WHERE year = %s AND monthday = %s
              AND expected_value IS NOT NULL
            ORDER BY jyocd
        """, (year, monthday))
        rows = to_dicts(cur)
    return [
        {"code": r["jyocd"], "name": JYO_MAP.get((r["jyocd"] or "").strip(), r["jyocd"])}
        for r in rows
    ]


# ── GET /api/race_list ──────────────────────────────────────────────────────
@router.get("/api/race_list")
def get_race_list(
    date:  str = Query(..., min_length=8, max_length=8),
    venue: str = Query(...),
):
    """指定日・競馬場のレース番号一覧を返す。"""
    year, monthday = _parse_date(date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT racenum
            FROM nl_race_prediction
            WHERE year = %s AND monthday = %s AND jyocd = %s
              AND expected_value IS NOT NULL
            ORDER BY racenum
        """, (year, monthday, venue))
        rows = to_dicts(cur)
    return [r["racenum"] for r in rows]


# ── GET /api/prediction ─────────────────────────────────────────────────────
@router.get("/api/prediction")
def get_prediction(
    date:  str = Query(..., min_length=8, max_length=8),
    venue: str = Query(...),
    race:  int = Query(..., ge=1, le=12),
):
    """指定レースの予想結果を期待値降順で返す。"""
    year, monthday = _parse_date(date)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                rp.umaban,
                COALESCE(TRIM(se.bamei), '不明')          AS bamei,
                ROUND(rp.win_prob::numeric * 100, 1)       AS win_prob_pct,
                ROUND(rp.adjusted_index::numeric, 1)       AS adjusted_index,
                rp.odds,
                ROUND(rp.expected_value::numeric, 3)       AS expected_value,
                rp.is_recommended,
                ra.kyori,
                LEFT(COALESCE(ra.trackcd, ''), 1)          AS surface_cd,
                COALESCE(TRIM(ra.ryakusyo6), '')           AS race_name
            FROM nl_race_prediction rp
            LEFT JOIN nl_se se
                ON  se.year     = rp.year
                AND se.monthday = rp.monthday
                AND se.jyocd    = rp.jyocd
                AND se.racenum  = rp.racenum
                AND se.umaban   = rp.umaban
            LEFT JOIN nl_ra ra
                ON  ra.year     = rp.year
                AND ra.monthday = rp.monthday
                AND ra.jyocd    = rp.jyocd
                AND ra.racenum  = rp.racenum
            WHERE rp.year     = %s
              AND rp.monthday = %s
              AND rp.jyocd    = %s
              AND rp.racenum  = %s
            ORDER BY COALESCE(rp.expected_value, -999) DESC, rp.umaban
        """, (year, monthday, venue, race))
        rows = to_dicts(cur)

    if not rows:
        raise HTTPException(404, "該当するデータが見つかりません")

    first = rows[0]
    surface_cd = (first.get("surface_cd") or "").strip()
    date_label = f"{year}/{monthday // 100:02d}/{monthday % 100:02d}"

    horses = [
        {
            "umaban":         r["umaban"],
            "bamei":          r["bamei"],
            "win_prob_pct":   float(r["win_prob_pct"])   if r["win_prob_pct"]   is not None else None,
            "adjusted_index": float(r["adjusted_index"]) if r["adjusted_index"] is not None else None,
            "odds":           float(r["odds"])           if r["odds"]           is not None else None,
            "expected_value": float(r["expected_value"]) if r["expected_value"] is not None else None,
            "is_recommended": bool(r["is_recommended"])  if r["is_recommended"] is not None else False,
        }
        for r in rows
    ]

    return {
        "race_info": {
            "date":      date_label,
            "venue":     JYO_MAP.get(venue.strip(), venue),
            "race_num":  race,
            "race_name": first.get("race_name") or "",
            "surface":   SURFACE_MAP.get(surface_cd, ""),
            "distance":  first.get("kyori") or 0,
        },
        "horses": horses,
    }
