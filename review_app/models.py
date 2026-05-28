from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

JYO_MAP: dict[str, str] = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

DISADVANTAGE_TYPES = ["外回しロス", "出遅れ", "前が壁", "掛かり"]
TIMING_OPTIONS     = ["スタート", "道中", "コーナー", "直線"]
WEATHER_OPTIONS    = ["晴", "曇", "小雨", "雨", "雪", "小雪"]
WIND_DIRECTIONS    = ["北", "北東", "東", "南東", "南", "南西", "西", "北西", "無風"]
TRACK_TYPES        = ["芝", "ダート", "障害"]
DISTANCE_CATEGORIES = ["短距離", "マイル", "中距離", "長距離"]
GRADE_OPTIONS      = ["G1", "G2", "G3", "L", "OP", "3勝", "2勝", "1勝", "新馬", "未勝利"]


# ---- Kaishi ----------------------------------------------------------------

class KaishiCreate(BaseModel):
    race_date: date
    jyo_cd: str
    kaiji: Optional[int] = None
    nichiji: Optional[int] = None
    memo: Optional[str] = None


class KaishiUpdate(BaseModel):
    kaiji: Optional[int] = None
    nichiji: Optional[int] = None
    memo: Optional[str] = None


# ---- Track Condition -------------------------------------------------------

class TrackConditionUpsert(BaseModel):
    track_type: Literal["芝", "ダート", "障害"]
    cushion_value: Optional[float] = Field(None, ge=0, le=20)
    moisture_rate: Optional[float] = Field(None, ge=0, le=100)
    maintenance_status: Optional[str] = None
    going_description: Optional[str] = None


# ---- Weather ---------------------------------------------------------------

class WeatherCreate(BaseModel):
    measurement_time: Optional[str] = None          # "HH:MM"
    weather_code: Literal["晴", "曇", "小雨", "雨", "雪", "小雪"]
    wind_speed: Optional[float] = Field(None, ge=0, le=50)
    wind_direction: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=-30, le=50)
    precipitation: Optional[float] = Field(None, ge=0)


# ---- Track Bias ------------------------------------------------------------

class TrackBiasUpsert(BaseModel):
    track_type: Literal["芝", "ダート", "障害"]
    distance_category: Optional[Literal["短距離", "マイル", "中距離", "長距離"]] = None
    inside_outside_score: Optional[int] = Field(None, ge=-3, le=3)
    inside_outside_label: Optional[str] = None
    front_back_score: Optional[int] = Field(None, ge=-3, le=3)
    front_back_label: Optional[str] = None
    bias_detail: Optional[str] = None
    notes: Optional[str] = None
    pace_comment: Optional[str] = None
    benefited_running_style: Optional[str] = None


class VenueInfo(BaseModel):
    jyo_cd: str
    kaiji: Optional[int] = None
    nichiji: Optional[int] = None


class KaishiBulkCreate(BaseModel):
    race_date: date
    venues: list[VenueInfo]


# ---- Race ------------------------------------------------------------------

class RaceUpsert(BaseModel):
    race_num: int = Field(..., ge=1, le=12)
    race_name: Optional[str] = None
    track_type: Optional[Literal["芝", "ダート", "障害"]] = None
    distance: Optional[int] = Field(None, ge=800, le=4000)
    grade: Optional[str] = None
    notes: Optional[str] = None


# ---- Disadvantage ----------------------------------------------------------

class DisadvantageCreate(BaseModel):
    horse_name: str = Field(..., min_length=1, max_length=36)
    horse_num: Optional[int] = Field(None, ge=1, le=18)
    disadvantage_type: Literal["外回しロス", "出遅れ", "前が壁", "掛かり"]
    timing: Literal["スタート", "道中", "コーナー", "直線"]
    severity: Optional[int] = Field(None, ge=1, le=5)
    estimated_loss: Optional[float] = Field(None, ge=0, le=20)
    memo: Optional[str] = None
