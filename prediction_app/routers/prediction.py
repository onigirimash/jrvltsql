import json
import os
from typing import AsyncGenerator

import anthropic as _anthropic_mod
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from prediction_app.database import get_conn, to_dicts

router = APIRouter()

# ── AI分析キャッシュ（インメモリ）──────────────────────────
_ai_cache: dict[str, str] = {}

# ── Pydanticモデル（AI分析リクエスト）────────────────────
class HorseForAI(BaseModel):
    umaban: int
    bamei: str
    avg_corner_pos: float | None = None
    adjusted_index: float | None = None
    win_prob_pct: float | None = None
    place_prob_pct: float | None = None
    odds: float | None = None
    expected_value: float | None = None
    adjusted_ev: float | None = None
    bias_score: float = 0.0

class BiasForAI(BaseModel):
    cushion: str = ''
    inner: str = 'flat'
    front: str = 'flat'

class RaceInfoForAI(BaseModel):
    date: str
    venue: str
    race_num: int
    race_name: str
    surface: str
    distance: int

class AIAnalysisRequest(BaseModel):
    race_info: RaceInfoForAI
    horses: list[HorseForAI]
    bias: BiasForAI

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
            WHERE win_prob IS NOT NULL
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
              AND win_prob IS NOT NULL
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
                  AND win_prob IS NOT NULL
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


# ── AI分析ヘルパー ──────────────────────────────────────

def _ai_cache_key(req: AIAnalysisRequest) -> str:
    ri = req.race_info
    b  = req.bias
    return f"{ri.date}:{ri.venue}:{ri.race_num}:{b.inner}:{b.front}:{b.cushion}"


def _rs_label(avg_corner_pos: float | None) -> str:
    if avg_corner_pos is None:
        return "不明"
    if avg_corner_pos <= 3.0:
        return "逃げ"
    if avg_corner_pos <= 5.5:
        return "先行"
    if avg_corner_pos <= 8.0:
        return "差し"
    return "追込"


_INNER_LABEL = {"flat": "フラット", "inner": "内有利", "outer": "外有利"}
_FRONT_LABEL = {"flat": "フラット", "front": "前有利", "rear": "後有利"}


def _build_prompt(req: AIAnalysisRequest) -> str:
    ri = req.race_info
    b  = req.bias
    n  = len(req.horses)

    race_name_part = f"【{ri.race_name}】" if ri.race_name else ""
    cushion_str    = ri.surface if not b.cushion else f"{ri.surface}（クッション値 {b.cushion}）"

    header = (
        f"あなたは日本競馬の専門アナリストです。"
        f"以下のデータをもとに日本語で分析してください。\n\n"
        f"## レース情報\n"
        f"- 開催: {ri.date} {ri.venue} 第{ri.race_num}R {race_name_part}\n"
        f"- コース: {cushion_str} {ri.distance}m {n}頭立て\n\n"
        f"## 馬場バイアス\n"
        f"- 内外: {_INNER_LABEL.get(b.inner, b.inner)}\n"
        f"- 前後: {_FRONT_LABEL.get(b.front, b.front)}\n\n"
        f"## 出走馬データ（調整後EV降順）\n"
        f"| 馬番 | 馬名 | 脚質 | 実力指数 | 勝率% | 複勝率% | オッズ | 期待値 | 調整後EV |\n"
        f"|------|------|------|---------|-------|---------|--------|--------|----------|\n"
    )

    sorted_horses = sorted(
        req.horses,
        key=lambda h: (h.adjusted_ev if h.adjusted_ev is not None else -999),
        reverse=True,
    )
    rows_txt = ""
    for h in sorted_horses:
        rows_txt += (
            f"| {h.umaban} | {h.bamei} | {_rs_label(h.avg_corner_pos)} "
            f"| {h.adjusted_index:.1f} "
            f"| {h.win_prob_pct:.1f} "
            f"| {h.place_prob_pct:.1f} "
            f"| {h.odds:.1f} "
            f"| {h.expected_value:+.3f} "
            f"| {h.adjusted_ev:+.3f} |\n"
        ) if all(v is not None for v in [h.adjusted_index, h.win_prob_pct,
                                          h.place_prob_pct, h.odds,
                                          h.expected_value, h.adjusted_ev]) else (
            f"| {h.umaban} | {h.bamei} | {_rs_label(h.avg_corner_pos)} "
            f"| ― | ― | ― | ― | ― | ― |\n"
        )

    footer = (
        "\n以下の順番で分析してください。各セクションは ## で始めること。\n\n"
        "## コース特徴\n"
        "競馬場・距離・芝ダートの特徴（外回り/内回り、直線長さ、先行有利・差し有利の傾向など）を2〜3文で。\n\n"
        "## 展開予想\n"
        "逃げ馬の特定、想定ペース（ハイ/ミドル/スロー）、バイアスを踏まえた展開の方向性を2〜3文で。\n\n"
        "## 推し馬\n"
        "◎（本命）、○（対抗）、▲（単穴）の3頭を馬名付きで挙げ、それぞれ1〜2文の理由を書くこと。\n\n"
        "## 注意馬\n"
        "人気だが危険な馬（もしあれば）を1〜2頭、理由とともに。なければ「特になし」と記載。\n\n"
        "## 総合コメント\n"
        "レース全体の見どころを1〜2文で締めること。\n"
    )

    return header + rows_txt + footer


# ── POST /api/ai_analysis ────────────────────────────────
@router.post("/api/ai_analysis")
async def ai_analysis(body: AIAnalysisRequest):
    """Claude APIを呼び出して展開予想・推し馬をSSEストリーミングで返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY が設定されていません")

    cache_key = _ai_cache_key(body)

    # ── キャッシュヒット ──
    if cache_key in _ai_cache:
        cached = _ai_cache[cache_key]

        async def _from_cache() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'text': cached}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"

        return StreamingResponse(_from_cache(), media_type="text/event-stream",
                                 headers={"X-Cache": "HIT"})

    # ── Claude APIストリーミング ──
    prompt = _build_prompt(body)
    client = _anthropic_mod.Anthropic(api_key=api_key)

    async def _stream() -> AsyncGenerator[str, None]:
        full_text = ""
        try:
            with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                for chunk in stream.text_stream:
                    full_text += chunk
                    yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
            _ai_cache[cache_key] = full_text
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"X-Cache": "MISS"})
