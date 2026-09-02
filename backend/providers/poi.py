"""景点与酒店搜索。

- demo          ：内置 curated 数据按偏好过滤（离线、结果完整）；
- openstreetmap ：Overpass API 免费真实数据（真实名称/坐标/部分开放时间，无评分门票，字段留空并标注）；
- amap          ：高德 /v5/place/text（有 Key，中文 POI 最全，仍不返回评分/价格，价格一律按档位估算）。

酒店「参考价」任何免费/常规 Web API 都不提供真实房价，因此统一按
酒店名分档（economy/comfort/luxury）× 城市基准价估算，并明确标注 price_is_estimated。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from backend.providers.base import CityNotSupported, DataSourceError, http_get_json
from backend.providers.demo import DEMO_CITIES, PREFERENCE_ALIAS
from backend.schemas import GeoPoint, Hotel, Poi

AMAP_V5_PLACE = "https://restapi.amap.com/v5/place/text"
OVERPASS = "https://overpass-api.de/api/interpreter"

# 偏好 → 高德关键词候选（取第一个用于文本搜索，减少计费调用）
PREF_KEYWORDS: dict[str, list[str]] = {
    "历史文化": ["历史古迹", "博物馆", "名胜古迹"],
    "博物馆": ["博物馆", "展览馆"],
    "自然风光": ["公园", "风景区", "自然"],
    "美食": ["美食街", "小吃", "夜市"],
    "购物": ["购物中心", "步行街", "商圈"],
    "亲子": ["主题乐园", "游乐园", "动物园"],
    "休闲": ["公园", "茶馆", "创意园"],
    "地标": ["地标", "广场", "观景台"],
    "夜生活": ["酒吧街", "夜景", "夜市"],
}

_HOTEL_LEVEL_WORDS = {
    "luxury": ["希尔顿", "万豪", "香格里拉", "洲际", "凯宾斯基", "四季", "威斯汀", "丽思卡尔顿",
               "君悦", "柏悦", "瑰丽", "瑞吉", "丽晶", "康莱德", "万达文华", "索菲特", "费尔蒙",
               "Hilton", "Marriott", "Hyatt", "Sheraton", "Westin", "Ritz", "InterContinental"],
    "economy": ["如家", "汉庭", "7天", "七天", "速8", "速八", "格林豪泰", "城市便捷", "莫泰",
                "锦江之星", "布丁", "尚客优", "宜必思", "iu酒店", "海友", "99优选", "青旅", "OYO"],
}


def _norm_word(w: str) -> str:
    w = w.strip().lower()
    for key, aliases in PREFERENCE_ALIAS.items():
        if w == key.lower() or w in aliases:
            return key
    return w


def classify_hotel_level(name: str) -> str:
    for level, words in _HOTEL_LEVEL_WORDS.items():
        if any(w.lower() in name.lower() for w in words):
            return level
    return "comfort"


def _amap_raw_pois(data: dict[str, Any]) -> list[dict[str, Any]]:
    # 兼容 v5 两种返回包装
    if isinstance(data.get("pois"), list):
        return data["pois"]
    inner = data.get("data")
    if isinstance(inner, dict) and isinstance(inner.get("pois"), list):
        return inner["pois"]
    return []


def _wgs_from_amap(geo: GeoPoint, loc: str | None) -> tuple[float, float] | None:
    """高德 GCJ-02 坐标原样保留（同数据源内自洽）；没有转换库时不混用。"""
    if not loc:
        return None
    try:
        lon, lat = loc.split(",")
        return float(lat), float(lon)
    except (ValueError, TypeError):
        return None


# ---------------- 演示数据源 ----------------

def search_demo_pois(city_key: str, preferences: list[str], selected: list[str] | None = None) -> list[Poi]:
    entry = DEMO_CITIES[city_key]
    prefs = {_norm_word(p) for p in preferences}
    pois: list[Poi] = []
    for p in entry["pois"]:
        tags = set(p["tags"])
        # 偏好匹配：无偏好评分全过；否则需要至少命中一个标准化偏好
        if prefs and not tags & prefs:
            continue
        if selected and p["name"] not in selected:
            continue
        pois.append(Poi(name=p["name"], tags=p["tags"], intro=p.get("intro", ""),
                        address=p.get("address", ""), rating=p.get("rating"),
                        opening_hours=p.get("opening_hours", ""), ticket=p.get("ticket", 0),
                        lat=p.get("lat"), lon=p.get("lon"), source="demo"))
    # 演示下选不满 5 个时放宽偏好限制，保证结果可用
    if len(pois) < 5 and not selected:
        for p in entry["pois"]:
            if all(q.name != p["name"] for q in pois):
                pois.append(Poi(name=p["name"], tags=p["tags"], intro=p.get("intro", ""),
                                address=p.get("address", ""), rating=p.get("rating"),
                                opening_hours=p.get("opening_hours", ""), ticket=p.get("ticket", 0),
                                lat=p.get("lat"), lon=p.get("lon"), source="demo"))
    return pois[:8]


def demo_hotels(city_key: str, level: str) -> list[Hotel]:
    """演示酒店：三档各取一家，优先包含用户所选档位。"""
    all_h = demo_hotels_all(city_key)
    if not all_h:
        return []
    by_level: dict[str, Hotel] = {h.level: h for h in all_h}
    chosen: list[Hotel] = []
    for lv in (level, "economy", "comfort", "luxury"):
        h = by_level.get(lv)
        if h and h.name not in {c.name for c in chosen}:
            chosen.append(h)
    for h in all_h:  # 仍不足时补充，至多 5 家
        if h.name not in {c.name for c in chosen}:
            chosen.append(h)
    return chosen[:5]


def demo_hotels_all(city_key: str) -> list[Hotel]:
    entry = DEMO_CITIES[city_key]
    return [Hotel(name=h["name"], level=h["level"], price_per_night=h["price"],
                  rating=h.get("rating"), address=h.get("address", ""),
                  distance_to_center_km=h.get("dist_km"), source="demo") for h in entry["hotels"]]


# ---------------- 高德数据源 ----------------

async def _amap_text_search(key: str, keywords: str, city: str, offset: int = 20) -> list[dict[str, Any]]:
    data = await http_get_json(
        AMAP_V5_PLACE,
        params={"key": key, "keywords": keywords, "city": city, "citylimit": "true",
                "offset": offset, "page": "1"},
    )
    if data.get("status") != "1":
        raise DataSourceError(f"高德 POI 搜索失败：{data.get('info', '未知错误')}")
    return _amap_raw_pois(data)


async def _amap_attractions(key: str, city: str, preferences: list[str], city_point: GeoPoint) -> list[Poi]:
    # 多个偏好并行各搜一次（如无偏好映射词则退化为“景点”）
    words: list[str] = []
    for p in preferences:
        kws = PREF_KEYWORDS.get(_norm_word(p))
        if kws:
            words.append(kws[0])
    words = list(dict.fromkeys(words)) or ["景点"]
    raw_lists = await asyncio.gather(*(_amap_text_search(key, w, city) for w in words))
    seen: set[str] = set()
    pois: list[Poi] = []
    for raw in raw_lists:
        for it in raw:
            name = (it.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            loc = _wgs_from_amap(city_point, it.get("location"))
            pois.append(Poi(name=name, category=it.get("type", "景点").split(";")[-1],
                            address=it.get("address", ""), lat=loc[0] if loc else None,
                            lon=loc[1] if loc else None, source="amap"))
            if len(pois) >= 12:
                break
        if len(pois) >= 12:
            break
    return pois[:8]


async def _amap_hotels(key: str, city: str, city_point: GeoPoint, level: str) -> list[Hotel]:
    raw = await _amap_text_search(key, "酒店", city, offset=25)
    out: list[Hotel] = []
    seen: set[str] = set()
    for it in raw:
        name = (it.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lv = classify_hotel_level(name)
        out.append(Hotel(name=name, level=lv, price_per_night=0.0,  # 占位，稍后按档位估算
                         address=it.get("address", ""), source="amap"))
    if not out:
        raise DataSourceError(f"高德在「{city}」未搜到酒店")
    # 三档各保留 1 家（不足 3 档则保留已有的）
    by_level: dict[str, list[Hotel]] = {"economy": [], "comfort": [], "luxury": []}
    for h in out:
        by_level[h.level].append(h)
    ordered = [by_level[l][0] for l in ("economy", "comfort", "luxury") if by_level[l]]
    return ordered


# ---------------- OpenStreetMap（Overpass）数据源 ----------------

async def _overpass(data_query: str) -> list[dict[str, Any]]:
    data = await http_get_json(OVERPASS, params={"data": data_query})
    return data.get("elements", [])


async def _osm_attractions(city_point: GeoPoint) -> list[Poi]:
    lat, lon, r = city_point.lat, city_point.lon, 15000
    query = (
        "[out:json][timeout:25];("
        f'node["tourism"~"^(attraction|museum|viewpoint|zoo|theme_park)$"](around:{r},{lat},{lon});'
        f'way["tourism"~"^(attraction|museum|viewpoint|zoo|theme_park)$"](around:{r},{lat},{lon});'
        f'node["historic"~"^(monument|memorial|castle|archaeological_site)$"](around:{r},{lat},{lon});'
        f'way["historic"~"^(monument|memorial|castle|archaeological_site)$"](around:{r},{lat},{lon});'
        f'node["leisure"~"^(park|garden)$"]["name"](around:{r},{lat},{lon});'
        f'way["leisure"~"^(park|garden)$"]["name"](around:{r},{lat},{lon});'
        ");out center tags 60;"
    )
    elements = await _overpass(query)
    pois: list[Poi] = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:zh") or tags.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        if el["type"] == "node":
            latc, lonc = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            latc, lonc = c.get("lat"), c.get("lon")
        cat = "博物馆" if tags.get("tourism") == "museum" else (
            "公园" if tags.get("leisure") in ("park", "garden") else (
                "历史古迹" if tags.get("historic") else "景点"))
        pois.append(Poi(name=name, category=cat, tags=[cat],
                        opening_hours=tags.get("opening_hours", ""), lat=latc, lon=lonc,
                        address="", source="openstreetmap"))
    return pois[:10]


async def _osm_hotels(city_point: GeoPoint) -> list[Hotel]:
    lat, lon, r = city_point.lat, city_point.lon, 12000
    query = (
        "[out:json][timeout:25];("
        f'node["tourism"="hotel"]["name"](around:{r},{lat},{lon});'
        f'way["tourism"="hotel"]["name"](around:{r},{lat},{lon});'
        f'node["tourism"="guest_house"]["name"](around:{r},{lat},{lon});'
        ");out center tags 40;"
    )
    elements = await _overpass(query)
    by_level: dict[str, list[Hotel]] = {"economy": [], "comfort": [], "luxury": []}
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:zh") or tags.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        lv = classify_hotel_level(name)
        by_level[lv].append(Hotel(name=name, level=lv, price_per_night=0.0,
                                  address="", source="openstreetmap"))
    return [by_level[l][0] for l in ("economy", "comfort", "luxury") if by_level[l]]


# ---------------- 统一入口 ----------------

def _estimate_prices(hotels: list[Hotel], city_key: str | None, level_budget: str) -> None:
    """按档位 × 城市基准价填充参考价（真实房价不公开，全部标注估算）。"""
    entry = DEMO_CITIES.get(city_key or "")
    base = (entry or {}).get("level_price") or {"economy": 280, "comfort": 480, "luxury": 950}
    for h in hotels:
        seed = sum(ord(c) for c in h.name) % 10  # 确定性抖动，避免每次启动价格漂移
        h.price_per_night = round(float(base.get(h.level, 480)) * (0.95 + 0.1 * seed / 10), 0)
        h.price_is_estimated = True


async def get_pois(mode: str, city_name: str, city_point: GeoPoint, preferences: list[str],
                   amap_key: str = "", selected: list[str] | None = None) -> tuple[list[Poi], str, list[str]]:
    """按数据源模式搜索候选景点。返回 (pois, provider, warnings)。"""
    warnings: list[str] = []
    city_key = city_name.strip().replace("市", "")
    demo_ok = city_key in DEMO_CITIES

    if mode == "demo":
        pois = search_demo_pois(city_key, preferences, selected)
        if not pois and selected:
            pois = search_demo_pois(city_key, preferences, None)
            warnings.append("所选景点不在演示库中，已忽略勾选并返回全部匹配景点")
        return pois, "demo", warnings

    if mode == "amap":
        if not amap_key:
            raise DataSourceError("高德数据源需要 AMAP_KEY")
        try:
            pois = await _amap_attractions(amap_key, city_name, preferences, city_point)
            if selected:
                pois = [p for p in pois if p.name in selected]
            if pois:
                warnings.append("高德 POI 不提供评分/门票，价格与评分由行程模块估算或留空")
                return pois, "amap", warnings
        except DataSourceError as exc:
            warnings.append(f"高德景点搜索失败：{exc}")
        if demo_ok:
            warnings.append("已降级到演示景点数据")
            return search_demo_pois(city_key, preferences, selected), "demo", warnings
        raise

    # openstreetmap / auto
    try:
        pois = await _osm_attractions(city_point)
    except DataSourceError as exc:
        warnings.append(f"Overpass 景点搜索失败：{exc}")
        pois = []
    if selected:
        pois = [p for p in pois if p.name in selected]
    if len(pois) >= 5:
        warnings.append("OSM 数据源不提供评分/门票/地址等富信息；如需完整数据请接入高德或使用演示数据源")
        return pois, "openstreetmap", warnings
    if demo_ok:
        warnings.append("OSM 结果不足，已降级到演示景点数据")
        return search_demo_pois(city_key, preferences, selected), "demo", warnings
    if not pois:
        raise CityNotSupported("OSM 在目的地附近未找到足够景点，请改用「高德」或内置演示城市")
    return pois, "openstreetmap", warnings


async def get_hotels(mode: str, city_name: str, city_point: GeoPoint, level: str,
                     amap_key: str = "") -> tuple[list[Hotel], str, list[str]]:
    warnings: list[str] = []
    city_key = city_name.strip().replace("市", "")
    demo_ok = city_key in DEMO_CITIES

    if mode == "demo":
        return demo_hotels(city_key, level), "demo", []

    if mode == "amap":
        if not amap_key:
            raise DataSourceError("高德数据源需要 AMAP_KEY")
        try:
            hotels = await _amap_hotels(amap_key, city_name, city_point, level)
            _estimate_prices(hotels, city_key if demo_ok else None, level)
            return hotels, "amap", ["酒店参考价为按档位估算，非实时房价"]
        except DataSourceError as exc:
            warnings.append(f"高德酒店搜索失败：{exc}")
        if demo_ok:
            warnings.append("已降级到演示酒店数据")
            return demo_hotels(city_key, level), "demo", warnings
        raise

    try:
        hotels = await _osm_hotels(city_point)
    except DataSourceError as exc:
        warnings.append(f"Overpass 酒店搜索失败：{exc}")
        hotels = []
    if len(hotels) >= 2:
        _estimate_prices(hotels, city_key if demo_ok else None, level)
        warnings.append("酒店参考价为按档位估算，非实时房价")
        return hotels, "openstreetmap", warnings
    if demo_ok:
        warnings.append("OSM 酒店结果不足，已降级到演示数据")
        return demo_hotels(city_key, level), "demo", warnings
    raise CityNotSupported("OSM 在目的地附近未找到足够酒店，请改用「高德」或内置演示城市")
