"""点对点路线（距离/耗时）：OSRM（免费）→ 高德 v5（有 Key，含公交）→ 直线估算兜底。

坐标约定：
- OSRM / OSM 数据用 WGS-84；
- 高德用 GCJ-02（火星坐标）。
同一数据源内坐标自洽即可；跨数据源混用时坐标需转换，本项目不做混用。
"""
from __future__ import annotations

from backend.providers.base import DataSourceError, ProviderUnavailable, haversine_km, http_get_json
from backend.schemas import RouteSegment

OSRM_BASE = "https://router.project-osrm.org"  # 公共实例，有速率限制；可替换自建实例
AMAP_V5_DIRECTION = "https://restapi.amap.com/v5/direction"

PROFILE_MAP = {"driving": "driving", "walking": "walking", "transit": None, "cycling": "cycling"}

# 估算系数：直线距离 × 绕路系数；平均速度 km/h
_DETOUR = {"driving": 1.4, "walking": 1.3, "transit": 1.5, "cycling": 1.35}
_SPEED = {"driving": 26.0, "walking": 4.5, "transit": 20.0, "cycling": 13.0}


def estimate_segment(lat1, lon1, lat2, lon2, mode: str) -> RouteSegment:
    """无 API 时的直线距离估算（确定性、可离线）。"""
    d = haversine_km(lat1, lon1, lat2, lon2) * _DETOUR.get(mode, 1.4)
    duration = d / _SPEED.get(mode, 20.0) * 60
    return RouteSegment(mode=mode, distance_km=round(d, 1), duration_min=round(duration), estimated=True)


async def _osrm_segment(lat1, lon1, lat2, lon2, mode: str) -> RouteSegment:
    profile = PROFILE_MAP.get(mode)
    if not profile:
        raise ProviderUnavailable("OSRM 公共实例不支持公交(transit)，请使用高德数据源或接受估算")
    data = await http_get_json(
        f"{OSRM_BASE}/route/v1/{profile}/{lon1},{lat1};{lon2},{lat2}",
        params={"overview": "false", "steps": "false", "alternatives": "false"},
    )
    route = (data.get("routes") or [None])[0]
    if not route:
        raise DataSourceError("OSRM 未返回路线")
    return RouteSegment(
        mode=mode,
        distance_km=round(route["distance"] / 1000, 1),
        duration_min=round(route["duration"] / 60),
    )


async def _amap_segment(
    lat1, lon1, lat2, lon2, mode: str, key: str, citycode1: str, citycode2: str
) -> RouteSegment:
    if mode == "transit":
        url = f"{AMAP_V5_DIRECTION}/transit/integrated"
        params = {
            "key": key,
            "origin": f"{lon1},{lat1}",
            "destination": f"{lon2},{lat2}",
            "city1": citycode1 or "010",
            "city2": citycode2 or citycode1 or "010",
        }
    else:
        url = f"{AMAP_V5_DIRECTION}/{mode}"
        params = {"key": key, "origin": f"{lon1},{lat1}", "destination": f"{lon2},{lat2}"}
    data = await http_get_json(url, params=params)
    if data.get("status") != "1":
        raise DataSourceError(f"高德路线规划失败：{data.get('info', '未知错误')}")
    path = (data.get("route", {}).get("paths") or [None])[0]
    if not path:
        raise DataSourceError("高德未返回可行路线")
    return RouteSegment(
        mode=mode,
        distance_km=round(float(path["distance"]) / 1000, 1),
        duration_min=round(float(path["duration"]) / 60),
    )


async def get_segment(
    mode: str,
    lat1: float, lon1: float, lat2: float, lon2: float,
    amap_key: str = "", citycode1: str = "", citycode2: str = "",
) -> RouteSegment:
    """计算两点间一段行程。返回数据可能标记 estimated=True。

    优先级：高德（有 Key，三种出行方式）→ OSRM（driving/walking/cycling）→ 直线估算。
    """
    # 同一坐标/极近 → 视作步行短途
    if abs(lat1 - lat2) < 0.001 and abs(lon1 - lon2) < 0.001:
        return RouteSegment(mode=mode, distance_km=0.1, duration_min=2, estimated=True)

    if amap_key:
        try:
            return await _amap_segment(lat1, lon1, lat2, lon2, mode, amap_key, citycode1, citycode2)
        except (DataSourceError, ProviderUnavailable):
            pass  # 降级到 OSRM / 估算

    if mode in ("driving", "walking"):
        try:
            return await _osrm_segment(lat1, lon1, lat2, lon2, mode)
        except (DataSourceError, ProviderUnavailable):
            pass

    return estimate_segment(lat1, lon1, lat2, lon2, mode)
