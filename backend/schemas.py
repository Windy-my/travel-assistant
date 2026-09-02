"""API 请求 / 响应的 Pydantic 模型。"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------- 常量 ----------------

LLM_PROVIDERS = ("openai", "anthropic", "azure_openai", "zhipu", "qianfan")
DATA_PROVIDERS = ("auto", "demo", "openstreetmap", "amap")
TRAVEL_MODES = ("driving", "walking", "transit")
HOTEL_LEVELS = ("economy", "comfort", "luxury")
PREFERENCE_POOL = ["历史文化", "博物馆", "自然风光", "美食", "购物", "亲子", "休闲", "地标", "夜生活"]

# ---------------- 请求 ----------------

class LlmConfig(BaseModel):
    """LLM 动态配置。Key 仅用于本次请求，服务端不持久化。"""

    provider: Literal["openai", "anthropic", "azure_openai", "zhipu", "qianfan"] = "openai"
    api_key: str = ""
    base_url: str | None = None  # OpenAI 兼容网关地址（智谱/百度千帆/自部署等）
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=64, le=8192)
    # Azure OpenAI 专用
    azure_endpoint: str | None = None
    azure_api_version: str = "2024-06-01"
    azure_deployment: str | None = None

    @field_validator("temperature")
    @classmethod
    def _clamp_temperature(cls, v: float) -> float:
        return max(0.0, min(v, 1.5))


class TravelRequest(BaseModel):
    """/api/generate 请求体。"""

    destination: str = Field(..., min_length=1, max_length=40, description="目的地城市，如「北京」")
    start_date: date
    end_date: date
    preferences: list[str] = Field(default_factory=lambda: ["历史文化"])
    travel_mode: Literal["driving", "walking", "transit"] = "driving"
    travelers: int = Field(default=2, ge=1, le=20, description="同行人数（影响餐饮预算）")
    data_provider: Literal["auto", "demo", "openstreetmap", "amap"] = "auto"
    # 按次传入的高德 Web 服务 Key（可选；服务端也可通过 .env 的 AMAP_KEY 配置，仅本次请求使用不落盘）
    amap_api_key: str = ""
    hotel_level: Literal["economy", "comfort", "luxury"] = "comfort"
    selected_pois: list[str] = Field(default_factory=list, description="用户指定景点名（可选）")
    daily_food_budget: int | None = Field(default=None, ge=0, description="手动餐饮预算（每人每天，元）")
    transport_budget: int | None = Field(default=None, ge=0, description="手动交通总预算（元）")
    llm: LlmConfig = Field(default_factory=LlmConfig)

    @field_validator("end_date")
    @classmethod
    def _check_date_range(cls, v: date, info: Any) -> date:
        start = info.data.get("start_date")
        if start and v < start:
            raise ValueError("end_date 不能早于 start_date")
        if start and (v - start).days > 14:
            raise ValueError("行程最长支持 15 天")
        return v

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1


# ---------------- 响应 ----------------

class GeoPoint(BaseModel):
    city: str
    lat: float
    lon: float
    timezone: str = "Asia/Shanghai"
    admin1: str = ""
    country: str = ""


class DailyWeather(BaseModel):
    date: str
    condition: str
    temp_min: float
    temp_max: float
    precipitation_prob: float = 0
    wind_kmh: float = 0
    advice: str = ""


class Poi(BaseModel):
    name: str
    category: str = "景点"
    tags: list[str] = Field(default_factory=list)
    intro: str = ""
    address: str = ""
    rating: float | None = None
    opening_hours: str = ""
    ticket: float = 0  # 门票（元），0 表示免费/未知
    lat: float | None = None
    lon: float | None = None
    source: str = ""


class Hotel(BaseModel):
    name: str
    level: Literal["economy", "comfort", "luxury"] = "comfort"
    price_per_night: float
    rating: float | None = None
    address: str = ""
    distance_to_center_km: float | None = None
    source: str = ""
    price_is_estimated: bool = True


class RouteSegment(BaseModel):
    """相邻景点之间的交通段落。"""

    from_name: str = ""
    to_name: str = ""
    mode: Literal["driving", "walking", "transit", "cycling"] = "driving"
    distance_km: float
    duration_min: float
    estimated: bool = False  # True = 估算值（API 不可用/公交无数据源）


class Stop(BaseModel):
    time_slot: Literal["上午", "下午", "晚上"] = "上午"
    poi_name: str
    poi_index: int = 0
    route_from_prev: RouteSegment | None = None
    note: str = ""


class DayPlan(BaseModel):
    day: int
    date: str
    stops: list[Stop] = Field(default_factory=list)
    dinner_recommendation: str = ""
    summary: str = ""


class BudgetItem(BaseModel):
    name: str
    amount: float
    detail: str = ""
    kind: Literal["hotel", "ticket", "food", "transport"] = "food"


class Budget(BaseModel):
    currency: str = "¥"
    items: list[BudgetItem] = Field(default_factory=list)
    total: float = 0
    note: str = ""


class StepLog(BaseModel):
    step: str
    status: Literal["ok", "degraded", "failed"] = "ok"
    provider: str = ""
    detail: str = ""


class TravelMeta(BaseModel):
    destination: str
    start_date: str
    end_date: str
    days: int
    travel_mode: str
    data_provider: str
    actual_providers: dict[str, str] = Field(default_factory=dict)  # 各步骤实际使用的数据源
    warnings: list[str] = Field(default_factory=list)


class TravelResponse(BaseModel):
    meta: TravelMeta
    city: GeoPoint
    weather: list[DailyWeather]
    pois: list[Poi]
    hotels: list[Hotel]
    itinerary: list[DayPlan]
    budget: Budget
    logs: list[StepLog] = Field(default_factory=list)
    markdown: str = ""

    @property
    def generated_at(self) -> str:
        import datetime

        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
