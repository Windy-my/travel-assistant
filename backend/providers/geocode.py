"""城市 → 地理坐标（GeoPoint）。三种来源：
- 演示库内置（demo 城市）
- Open-Meteo geocoding API（免费、无 Key，支持中文，已实测可用）
- 高德 /v3/geocode/geo（有 AMAP_KEY 时，返回 GCJ-02 坐标 + adcode + citycode）
"""
from __future__ import annotations

from backend.providers.base import CityNotSupported, DataSourceError, ProviderUnavailable, http_get_json
from backend.providers.demo import DEMO_CITIES
from backend.schemas import GeoPoint

OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
AMAP_GEOCODE = "https://restapi.amap.com/v3/geocode/geo"


def _normalize_city(city: str) -> str:
    return city.strip().replace("市", "").replace("省", "").replace("特别行政区", "")


def city_in_demo(city: str) -> bool:
    return _normalize_city(city) in DEMO_CITIES


async def geocode_demo(city: str) -> GeoPoint:
    key = _normalize_city(city)
    entry = DEMO_CITIES.get(key)
    if not entry:
        raise CityNotSupported(
            f"演示数据源仅内置 {list(DEMO_CITIES)}，请选择其中之一；或改用「真实数据/高德」数据源。"
        )
    return GeoPoint(city=key, lat=entry["lat"], lon=entry["lon"], admin1=key, country="中国")


async def geocode_openmeteo(city: str) -> GeoPoint:
    data = await http_get_json(
        OPEN_METEO_GEOCODE,
        params={"name": city.strip(), "count": 5, "language": "zh", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        raise CityNotSupported(f"地理编码未命中城市「{city}」")
    # 优先中国境内且名称一致/人口最多的结果
    best = None
    norm = _normalize_city(city)
    for r in results:
        if r.get("country_code") != "CN":
            continue
        if best is None or (r.get("name") in (city, norm)) and r.get("population", 0) > best.get("population", 0):
            best = r
    best = best or results[0]
    return GeoPoint(
        city=best.get("name") or city,
        lat=float(best["latitude"]),
        lon=float(best["longitude"]),
        timezone=best.get("timezone", "Asia/Shanghai"),
        admin1=best.get("admin1", ""),
        country=best.get("country", ""),
    )


async def geocode_amap(city: str, key: str) -> tuple[GeoPoint, str]:
    """高德地理编码。返回 (GeoPoint, citycode)，citycode 供公交路线使用。"""
    data = await http_get_json(
        AMAP_GEOCODE, params={"key": key, "address": city.strip(), "city": city.strip()}
    )
    geocodes = data.get("geocodes") or []
    if data.get("status") != "1" or not geocodes:
        info = data.get("info", "未知错误")
        raise DataSourceError(f"高德地理编码失败: {info}")
    g = geocodes[0]
    lon, lat = g["location"].split(",")
    return GeoPoint(
        city=g.get("city", city),
        lat=float(lat),
        lon=float(lon),
        admin1=g.get("province", ""),
        country="中国",
    ), g.get("citycode", "")


async def geocode(mode: str, city: str, amap_key: str = "") -> tuple[GeoPoint, str, str]:
    """按数据源模式解析城市坐标。

    返回 (GeoPoint, provider_name, citycode_or_empty)
    """
    if mode == "demo":
        return await geocode_demo(city), "demo", "010"
    if mode == "amap":
        if not amap_key:
            raise ProviderUnavailable("高德数据源需要 AMAP_KEY（服务端 .env 或请求中配置）")
        point, citycode = await geocode_amap(city, amap_key)
        return point, "amap", citycode
    # openstreetmap / auto：Open-Meteo 免费地理编码
    try:
        point = await geocode_openmeteo(city)
        return point, "openstreetmap", ""
    except DataSourceError:
        # 兜底：命中内置演示城市库则降级，否则把错误抛给上层处理
        if city_in_demo(city):
            return await geocode_demo(city), "demo", "010"
        raise
