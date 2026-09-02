"""天气获取：Open-Meteo（默认，免费无 Key）→ OpenWeatherMap（可选 Key）→ 估算兜底。

Open-Meteo 返回 WMO weather code，映射为中文天气描述并给出穿衣/带伞建议。
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta

from backend.providers.base import DataSourceError, http_get_json
from backend.schemas import DailyWeather, GeoPoint

OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OWM_FORECAST = "https://api.openweathermap.org/data/2.5/forecast"

# WMO weather code → (中文, 是否降水类)
WMO_CODES: dict[int, tuple[str, bool]] = {
    0: ("晴", False), 1: ("基本晴", False), 2: ("多云", False), 3: ("阴", False),
    45: ("雾", False), 48: ("冻雾", False),
    51: ("毛毛雨", True), 53: ("毛毛雨", True), 55: ("强毛毛雨", True),
    56: ("冻毛毛雨", True), 57: ("强冻毛毛雨", True),
    61: ("小雨", True), 63: ("中雨", True), 65: ("大雨", True),
    66: ("冻雨", True), 67: ("强冻雨", True),
    71: ("小雪", True), 73: ("中雪", True), 75: ("大雪", True), 77: ("雪粒", True),
    80: ("阵雨", True), 81: ("强阵雨", True), 82: ("暴雨", True),
    85: ("阵雪", True), 86: ("强阵雪", True),
    95: ("雷阵雨", True), 96: ("雷阵雨伴冰雹", True), 99: ("强雷暴伴冰雹", True),
}


def _advice(condition: str, precip: float) -> str:
    rainy = any(k in condition for k in ("雨", "雷", "雪"))
    if precip >= 60 and rainy:
        return "降水概率高，建议备伞、优先安排室内景点"
    if rainy:
        return "可能有降水，建议随身带伞"
    return ""


def build_daily(row: dict, units: dict | None) -> DailyWeather:
    code = int(row.get("weather_code", 0) or 0)
    cond, _ = WMO_CODES.get(code, ("未知", False))
    precip = float(row.get("precipitation_probability_max", 0) or 0)
    return DailyWeather(
        date=str(row["time"]),
        condition=cond,
        temp_min=float(row.get("temperature_2m_min", 0) or 0),
        temp_max=float(row.get("temperature_2m_max", 0) or 0),
        precipitation_prob=precip,
        wind_kmh=float(row.get("wind_speed_10m_max", 0) or 0),
        advice=_advice(cond, precip),
    )


async def fetch_openmeteo(geo: GeoPoint, start: date, end: date) -> list[DailyWeather]:
    data = await http_get_json(
        OPEN_METEO_FORECAST,
        params={
            "latitude": geo.lat,
            "longitude": geo.lon,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
            "timezone": geo.timezone or "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    )
    days = data.get("daily") or {}
    times = days.get("time") or []
    rows = []
    # 逐条安全取值：API 偶发返回某序列较短的 partial 响应时，用 .get(key, []) 避免 IndexError
    for i in range(len(times)):
        rows.append({k: (days.get(k) or [])[i] if i < len(days.get(k) or []) else None for k in days})
    return [build_daily(row, data.get("daily_units")) for row in rows if row.get("time") is not None]


async def fetch_openweathermap(geo: GeoPoint, start: date, end: date, key: str) -> list[DailyWeather]:
    """OpenWeatherMap 5day/3hour 预报聚合为每日（仅覆盖最近 5 天）。"""
    data = await http_get_json(
        OWM_FORECAST,
        params={"lat": geo.lat, "lon": geo.lon, "appid": key, "units": "metric", "lang": "zh_cn"},
    )
    by_day: dict[str, list[dict]] = {}
    for item in data.get("list", []):
        d = item["dt_txt"][:10]
        if start.isoformat() <= d <= end.isoformat():
            by_day.setdefault(d, []).append(item)
    out: list[DailyWeather] = []
    for d in sorted(by_day):
        items = by_day[d]
        desc = items[0]["weather"][0]["description"]
        rainy = "rain" in items[0]["weather"][0].get("main", "").lower() or "雨" in desc
        out.append(
            DailyWeather(
                date=d,
                condition=desc,
                temp_min=min(float(x["main"]["temp_min"]) for x in items),
                temp_max=max(float(x["main"]["temp_max"]) for x in items),
                precipitation_prob=max(
                    float(x.get("pop", 0) or 0) * 100 for x in items
                ),
                wind_kmh=max(float(x["wind"]["speed"]) * 3.6 for x in items),
                advice="降水概率高，建议备伞" if rainy and max(float(x.get("pop", 0) or 0) for x in items) > 0.5 else "",
            )
        )
    return out


def _single_demo_day(geo: GeoPoint, d: date) -> DailyWeather:
    """确定性单日估算天气（真实 API 不可用/日期不可预报时的兜底，会由上层标注）。"""
    lat = geo.lat
    seed = int(hashlib.sha1(f"{geo.city}-{d}".encode()).hexdigest(), 16)
    month = d.month
    annual = 27 - abs(lat) * 0.42  # 年均温近似（纬度越高越冷）
    seasonal = 11 * math.cos(2 * math.pi * (month - 7) / 12)  # 7 月最热
    base = annual + seasonal
    temp_min = round(base - 4 + (seed % 30) / 10 - 1, 1)
    temp_max = round(base + 5 + ((seed >> 4) % 30) / 10, 1)
    precip = max(0.0, min(90.0, 25 + 35 * math.cos(2 * math.pi * (month - 7) / 12) + (seed % 21)))
    rainy = precip >= 55
    condition = "阵雨" if rainy else ("多云" if precip >= 30 else "晴")
    return DailyWeather(
        date=d.isoformat(),
        condition=condition,
        temp_min=temp_min,
        temp_max=temp_max,
        precipitation_prob=round(precip),
        advice=_advice(condition, precip),
    )


def _demo_weather(geo: GeoPoint, start: date, end: date) -> list[DailyWeather]:
    out: list[DailyWeather] = []
    for i in range((end - start).days + 1):
        out.append(_single_demo_day(geo, start + timedelta(days=i)))
    return out


async def get_weather(
    geo: GeoPoint, start: date, end: date, owm_key: str = ""
) -> tuple[list[DailyWeather], str, list[str]]:
    """获取日期范围内逐日天气。返回 (days, provider_name, warnings)。

    优先级：Open-Meteo（无 Key、无限量、覆盖约 16 天）→ OpenWeatherMap（如有 Key）→ 估算。
    实时源未覆盖的日期（过去日期/超出预报范围）会以估算补齐，保证逐日返回。
    """
    warnings: list[str] = []
    days: list[DailyWeather] = []
    provider = ""
    try:
        days = await fetch_openmeteo(geo, start, end)
        provider = "open-meteo"
        if not days:
            warnings.append("Open-Meteo 未返回该日期范围数据（可能超出预报范围）")
    except DataSourceError as exc:
        warnings.append(f"Open-Meteo 天气获取失败：{exc}")

    if not days and owm_key:
        try:
            days = await fetch_openweathermap(geo, start, end, owm_key)
            provider = "openweathermap"
        except DataSourceError as exc:
            warnings.append(f"OpenWeatherMap 天气获取失败：{exc}")

    if not days:
        provider = "demo-estimate"

    # 与期望日期逐日对齐，缺失项用估算补齐
    expected = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)}
    have = {w.date for w in days}
    for iso in sorted(expected - have):
        days.append(_single_demo_day(geo, date.fromisoformat(iso)))
    days.sort(key=lambda w: w.date)
    if provider != "demo-estimate" and have != expected:
        warnings.append("部分日期超出实时预报可获取范围，已按估算补齐")

    if provider == "demo-estimate":
        warnings.append("天气为演示估算值（未能连接实时天气服务），仅供参考")
    return days, provider, warnings
