import datetime

from fastapi import APIRouter, HTTPException
from review_app.database import get_conn, to_dict, to_dicts
from review_app.models import DisadvantageCreate, RaceUpsert

router = APIRouter(tags=["race"])


# ============================================================
# Race
# ============================================================

@router.post("/kaishi/{kaishi_id}/races", status_code=201)
def upsert_race(kaishi_id: int, body: RaceUpsert):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM review_kaishi WHERE id = %s", (kaishi_id,))
        if not cur.fetchone():
            raise HTTPException(404, "開催が見つかりません")
        cur.execute("""
            INSERT INTO review_race
                (kaishi_id, race_num, race_name, track_type, distance, grade, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (kaishi_id, race_num) DO UPDATE SET
                race_name  = EXCLUDED.race_name,
                track_type = EXCLUDED.track_type,
                distance   = EXCLUDED.distance,
                grade      = EXCLUDED.grade,
                notes      = EXCLUDED.notes
            RETURNING *
        """, (
            kaishi_id, body.race_num, body.race_name,
            body.track_type, body.distance, body.grade, body.notes,
        ))
        return to_dict(cur)


@router.get("/kaishi/{kaishi_id}/races")
def list_races(kaishi_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.*,
                   COUNT(d.id) AS disadvantage_count
            FROM   review_race r
            LEFT JOIN review_disadvantage d ON d.race_id = r.id
            WHERE  r.kaishi_id = %s
            GROUP BY r.id
            ORDER BY r.race_num
        """, (kaishi_id,))
        return to_dicts(cur)


@router.get("/races/{race_id}")
def get_race(race_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM review_race WHERE id = %s", (race_id,))
        row = to_dict(cur)
        if not row:
            raise HTTPException(404, "レースが見つかりません")
        cur.execute("""
            SELECT * FROM review_disadvantage
            WHERE  race_id = %s
            ORDER BY horse_num NULLS LAST, horse_name
        """, (race_id,))
        row["disadvantages"] = to_dicts(cur)
        return row


@router.delete("/races/{race_id}", status_code=204)
def delete_race(race_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_race WHERE id = %s", (race_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "レースが見つかりません")


# ============================================================
# Disadvantage
# ============================================================

@router.post("/races/{race_id}/disadvantages", status_code=201)
def create_disadvantage(race_id: int, body: DisadvantageCreate):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM review_race WHERE id = %s", (race_id,))
        if not cur.fetchone():
            raise HTTPException(404, "レースが見つかりません")
        cur.execute("""
            INSERT INTO review_disadvantage
                (race_id, horse_name, horse_num, disadvantage_type,
                 timing, severity, estimated_loss, memo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            race_id, body.horse_name, body.horse_num, body.disadvantage_type,
            body.timing, body.severity, body.estimated_loss, body.memo,
        ))
        return to_dict(cur)


@router.delete("/disadvantages/{d_id}", status_code=204)
def delete_disadvantage(d_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM review_disadvantage WHERE id = %s", (d_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "不利情報が見つかりません")


# ============================================================
# nl_se から出走馬一覧を取得
# ============================================================

@router.get("/races/{race_id}/horses")
def get_race_horses(race_id: int):
    """nl_se から該当レースの出走馬一覧（馬番・馬名）を返す。"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT r.race_num, k.race_date, k.jyo_cd
            FROM   review_race r
            JOIN   review_kaishi k ON k.id = r.kaishi_id
            WHERE  r.id = %s
        """, (race_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "レースが見つかりません")
        race_num, race_date, jyo_cd = row

        if isinstance(race_date, datetime.datetime):
            race_date = race_date.date()
        elif isinstance(race_date, str):
            race_date = datetime.date.fromisoformat(race_date)

        year     = race_date.year
        monthday = race_date.month * 100 + race_date.day

        try:
            cur.execute("""
                SELECT umaban, wakuban, bamei
                FROM   nl_se
                WHERE  year = %s AND monthday = %s AND jyocd = %s AND racenum = %s
                ORDER  BY umaban
            """, (year, monthday, jyo_cd, race_num))
            rows = to_dicts(cur)
        except Exception:
            return []

    return [
        {
            'umaban':  r['umaban'],
            'wakuban': r.get('wakuban'),
            'bamei':   r.get('bamei') or '',
        }
        for r in rows
    ]
