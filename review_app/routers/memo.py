from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from review_app.database import get_conn

router = APIRouter()

FLAG_OPTIONS = {'注目', '次走', '危険', '消し'}


class MemoUpsert(BaseModel):
    memo: str | None = None
    flag: str | None = None


@router.get("/memo/{kettonum}")
def get_memo(kettonum: str):
    kt = kettonum.strip()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT memo, flag, updated_at FROM nl_review_memo WHERE kettonum = %s",
            (kt,),
        )
        row = cur.fetchone()
    if not row:
        return {"kettonum": kt, "memo": None, "flag": None}
    return {"kettonum": kt, "memo": row[0], "flag": row[1],
            "updated_at": str(row[2]) if row[2] else None}


@router.put("/memo/{kettonum}")
def upsert_memo(kettonum: str, body: MemoUpsert):
    kt = kettonum.strip()
    if body.flag and body.flag not in FLAG_OPTIONS:
        raise HTTPException(400, f"flag は {sorted(FLAG_OPTIONS)} のいずれかを指定してください")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO nl_review_memo (kettonum, memo, flag)
            VALUES (%s, %s, %s)
            ON CONFLICT (kettonum) DO UPDATE
                SET memo       = EXCLUDED.memo,
                    flag       = EXCLUDED.flag,
                    updated_at = NOW()
        """, (kt, body.memo or None, body.flag or None))
    return {"kettonum": kt, "memo": body.memo, "flag": body.flag}


@router.delete("/memo/{kettonum}")
def delete_memo(kettonum: str):
    kt = kettonum.strip()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM nl_review_memo WHERE kettonum = %s", (kt,))
    return {"ok": True}
