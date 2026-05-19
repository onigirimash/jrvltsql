from fastapi import APIRouter, HTTPException, Query

from prediction_app.database import get_conn, to_dicts

router = APIRouter()

JYO_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

# trackcd 先頭文字 → 馬場
def _surface(cd: str) -> str:
    c = (cd or "").strip()[:1]
    if c == "1": return "芝"
    if c == "2": return "ダート"
    if c in ("3", "4", "5"): return "障害"
    return ""


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
    """指定日・競馬場のレース番号・馬場・距離一覧を返す。"""
    year, monthday = _parse_date(date)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                rp.racenum,
                ra.kyori,
                LEFT(COALESCE(ra.trackcd, ''), 1) AS surface_cd
            FROM (
                SELECT DISTINCT racenum
                FROM nl_race_prediction
                WHERE year = %s AND monthday = %s AND jyocd = %s
                  AND expected_value IS NOT NULL
            ) rp
            LEFT JOIN nl_ra ra
                ON  ra.year     = %s
                AND ra.monthday = %s
                AND ra.jyocd    = %s
                AND ra.racenum  = rp.racenum
            ORDER BY rp.racenum
        """, (year, monthday, venue, year, monthday, venue))
        rows = to_dicts(cur)
    return [
        {
            "racenum":  r["racenum"],
            "surface":  _surface(r.get("surface_cd") or ""),
            "distance": int(r["kyori"]) if r.get("kyori") else None,
        }
        for r in rows
    ]


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
                TRIM(rp.kettonum)                          AS kettonum,
                COALESCE(TRIM(se.bamei), '不明')           AS bamei,
                ROUND(rp.win_prob::numeric * 100, 1)       AS win_prob_pct,
                ROUND(rp.place_prob::numeric * 100, 1)     AS place_prob_pct,
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

        # 直近10走の平均コーナー順位（脚質判定用）
        kettonums = list({(r.get("kettonum") or "").strip() for r in rows if r.get("kettonum")})
        corner_map: dict[str, float] = {}
        if kettonums:
            placeholders = ",".join(["%s"] * len(kettonums))
            cur.execute(f"""
                WITH ranked AS (
                    SELECT
                        TRIM(kettonum) AS kt,
                        jyuni1c,
                        ROW_NUMBER() OVER (
                            PARTITION BY TRIM(kettonum)
                            ORDER BY year DESC, monthday DESC
                        ) AS rn
                    FROM nl_se
                    WHERE TRIM(kettonum) IN ({placeholders})
                      AND kakuteijyuni >= 1
                      AND jyuni1c > 0
                )
                SELECT kt, ROUND(AVG(jyuni1c)::numeric, 1) AS avg_corner_pos
                FROM ranked
                WHERE rn <= 10
                GROUP BY kt
            """, kettonums)
            for cr in to_dicts(cur):
                corner_map[cr["kt"]] = float(cr["avg_corner_pos"])

    first = rows[0]
    surface_cd = (first.get("surface_cd") or "").strip()
    date_label = f"{year}/{monthday // 100:02d}/{monthday % 100:02d}"

    horses = [
        {
            "umaban":         r["umaban"],
            "kettonum":       (r.get("kettonum") or "").strip(),
            "bamei":          r["bamei"],
            "win_prob_pct":   float(r["win_prob_pct"])   if r["win_prob_pct"]   is not None else None,
            "place_prob_pct": float(r["place_prob_pct"]) if r["place_prob_pct"] is not None else None,
            "adjusted_index": float(r["adjusted_index"]) if r["adjusted_index"] is not None else None,
            "odds":           float(r["odds"])           if r["odds"]           is not None else None,
            "expected_value": float(r["expected_value"]) if r["expected_value"] is not None else None,
            "is_recommended": bool(r["is_recommended"])  if r["is_recommended"] is not None else False,
            "avg_corner_pos": corner_map.get((r.get("kettonum") or "").strip()),
        }
        for r in rows
    ]

    return {
        "race_info": {
            "date":      date_label,
            "venue":     JYO_MAP.get(venue.strip(), venue),
            "race_num":  race,
            "race_name": first.get("race_name") or "",
            "surface":   _surface(surface_cd),
            "distance":  int(first["kyori"]) if first.get("kyori") else 0,
        },
        "horses": horses,
    }


# ── GET /api/horse_history ──────────────────────────────────────────────────
@router.get("/api/horse_history")
def get_horse_history(kettonum: str = Query(...)):
    """馬の過去10走を返す（perf_index・norm_index含む）。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                se.year,
                se.monthday,
                se.jyocd,
                se.racenum,
                se.kakuteijyuni                             AS finish_pos,
                COALESCE(TRIM(se.bamei), '')               AS bamei,
                ra.kyori,
                LEFT(COALESCE(ra.trackcd, ''), 1)          AS surface_cd,
                COALESCE(TRIM(ra.ryakusyo6), '')           AS race_name,
                ROUND(p.perf_index::numeric, 4)            AS perf_index,
                ROUND(hi.norm_index::numeric, 1)           AS norm_index
            FROM nl_se se
            LEFT JOIN nl_ra ra
                ON  ra.year     = se.year
                AND ra.monthday = se.monthday
                AND ra.jyocd    = se.jyocd
                AND ra.racenum  = se.racenum
            LEFT JOIN nl_performance p
                ON  p.year        = se.year
                AND p.monthday    = se.monthday
                AND p.jyocd       = se.jyocd
                AND p.racenum::int = se.racenum
                AND p.umaban::int  = se.umaban
            LEFT JOIN nl_horse_index hi
                ON  hi.kettonum     = TRIM(se.kettonum)
                AND hi.distance_cat = CASE
                        WHEN ra.kyori <= 1400 THEN 'S'
                        WHEN ra.kyori <= 1800 THEN 'M'
                        WHEN ra.kyori <= 2200 THEN 'I'
                        ELSE 'L' END
                AND hi.surface      = CASE LEFT(COALESCE(ra.trackcd,''), 1)
                        WHEN '1' THEN 'T'
                        WHEN '2' THEN 'D'
                        ELSE 'J' END
            WHERE TRIM(se.kettonum) = %s
              AND se.jyocd BETWEEN '01' AND '10'
              AND se.kakuteijyuni >= 1
            ORDER BY se.year DESC, se.monthday DESC
            LIMIT 10
        """, (kettonum.strip(),))
        rows = to_dicts(cur)

    races = [
        {
            "date":       f"{r['year']}/{r['monthday'] // 100:02d}/{r['monthday'] % 100:02d}",
            "venue":      JYO_MAP.get((r["jyocd"] or "").strip(), r["jyocd"]),
            "race_num":   r["racenum"],
            "race_name":  r["race_name"],
            "surface":    _surface(r.get("surface_cd") or ""),
            "distance":   int(r["kyori"]) if r.get("kyori") else None,
            "finish_pos": r["finish_pos"],
            "perf_index": float(r["perf_index"]) if r.get("perf_index") is not None else None,
            "norm_index": float(r["norm_index"]) if r.get("norm_index") is not None else None,
        }
        for r in rows
    ]

    bamei = rows[0]["bamei"] if rows else kettonum
    return {"kettonum": kettonum, "bamei": bamei, "races": races}
