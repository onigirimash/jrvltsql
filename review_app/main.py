from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from review_app.database import get_conn
from review_app.models import (
    DISADVANTAGE_TYPES,
    DISTANCE_CATEGORIES,
    GRADE_OPTIONS,
    JYO_MAP,
    TIMING_OPTIONS,
    TRACK_TYPES,
    WEATHER_OPTIONS,
    WIND_DIRECTIONS,
)
from review_app.routers import kaishi, race

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    with get_conn() as conn:
        cur = conn.cursor()
        for migration in ["001_create_review_tables.sql", "002_create_nl_weather.sql", "003_add_pace_fields.sql"]:
            sql = (BASE_DIR / "migrations" / migration).read_text(encoding="utf-8")
            cur.execute(sql)
    yield


app = FastAPI(title="競馬回顧ツール", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(kaishi.router, prefix="/api")
app.include_router(race.router, prefix="/api")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/constants")
def constants():
    return {
        "jyo_map": JYO_MAP,
        "jyo_list": [{"code": k, "name": v} for k, v in JYO_MAP.items()],
        "disadvantage_types": DISADVANTAGE_TYPES,
        "timing_options": TIMING_OPTIONS,
        "weather_options": WEATHER_OPTIONS,
        "wind_directions": WIND_DIRECTIONS,
        "track_types": TRACK_TYPES,
        "distance_categories": DISTANCE_CATEGORIES,
        "grade_options": GRADE_OPTIONS,
    }


@app.get("/api/baba_info")
def get_baba_info(
    date:  str = Query(..., description="YYYYMMDD"),
    venue: str = Query(..., description="競馬場コード (例: 05)"),
):
    """nl_baba_moisture から指定日・競馬場の含水率・クッション値を返す。"""
    if len(date) != 8 or not date.isdigit():
        raise HTTPException(400, "date は YYYYMMDD 形式で指定してください")
    venue = venue.zfill(2)

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                cushion_value,
                turf_moisture_goal,
                turf_moisture_4corner,
                dirt_moisture_goal,
                dirt_moisture_4corner,
                ROUND((turf_moisture_goal + turf_moisture_4corner) / 2, 1) AS turf_moisture,
                ROUND((dirt_moisture_goal + dirt_moisture_4corner) / 2, 1) AS dirt_moisture
            FROM nl_baba_moisture
            WHERE race_date = %s AND jyo_cd = %s
        """, (date, venue))
        row = cur.fetchone()

    if not row:
        raise HTTPException(404, f"{date} / jyo_cd={venue} の馬場情報がありません")

    cols = [
        "cushion_value",
        "turf_moisture_goal", "turf_moisture_4corner",
        "dirt_moisture_goal", "dirt_moisture_4corner",
        "turf_moisture", "dirt_moisture",
    ]
    result = dict(zip(cols, row))
    return {k: (float(v) if v is not None else None) for k, v in result.items()}
