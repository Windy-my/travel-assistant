"""LangGraph 六步工作流。

流程：地理编码 → 天气 → 景点(POI) → 酒店 → 行程规划(规则+可选 LLM 润色) → 预算。

设计取舍（骨架版）：
- 六步按序串行；景点与酒店、各路线段之间已用 asyncio.gather 并发，避免公共免费
  服务的速率压力；如需更强并发可改 StateGraph 分支结构。
- 行程的“排序/分段”由确定性规则完成（保证与路线 API 段一致、可离线）；LLM 仅在
  有 Key 时做增强：为空缺的 POI 生成一句简介、给出每日美食与整体建议（纯文本层）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from backend.config import settings
from backend.llm_factory import build_chat_model, resolve_llm
from backend.providers import geocode as geo_mod
from backend.providers import poi as poi_mod
from backend.providers import route as rt_mod
from backend.providers import weather as wx_mod
from backend.providers.base import CityNotSupported, DataSourceError, ProviderUnavailable
from backend.providers.demo import DEFAULT_FOOD_PER_DAY, DEMO_CITIES
from backend.schemas import (
    Budget,
    BudgetItem,
    DayPlan,
    GeoPoint,
    Hotel,
    LlmConfig,
    Poi,
    RouteSegment,
    StepLog,
    Stop,
    TravelRequest,
)

_SLOTS = ["上午", "下午"]


class WorkflowError(RuntimeError):
    """工作流执行失败（含面向用户的友好信息）。"""


class GraphState(TypedDict, total=False):
    req: dict[str, Any]
    mode: str
    amap_key: str
    fatal: str
    warnings: list[str]
    logs: list[dict[str, Any]]
    providers: dict[str, str]
    tip: str
    city: dict[str, Any]
    citycode: str
    weather: list[dict[str, Any]]
    pois: list[dict[str, Any]]
    hotels: list[dict[str, Any]]
    itinerary: list[dict[str, Any]]
    budget: dict[str, Any]


# ---------------- 小工具 ----------------

def _log_entry(step: str, provider: str, status: str = "ok", detail: str = "") -> dict[str, Any]:
    return StepLog(step=step, status=status, provider=provider, detail=detail).model_dump()


def _city_key(city_name: str) -> str:
    return city_name.strip().replace("市", "")


def _entry(city_name: str) -> dict | None:
    return DEMO_CITIES.get(_city_key(city_name))


def _request_date(req: dict[str, Any], key: str):
    return datetime.fromisoformat(req[key]).date()


def guard(step_label: str):
    """节点包装：fatal 短路 + 统一错误捕获为友好信息。"""

    def deco(fn):
        async def wrapped(state: GraphState) -> dict[str, Any]:
            if state.get("fatal"):
                return {}
            try:
                return (await fn(state)) or {}
            except (WorkflowError, DataSourceError, ProviderUnavailable, CityNotSupported, ValueError) as exc:
                return {
                    "fatal": f"【{step_label}】{exc}",
                    "logs": [*state.get("logs", []), _log_entry(step_label, "", "failed", str(exc))],
                }
            except Exception as exc:  # noqa: BLE001 —— 兜底，避免 500 无提示
                return {
                    "fatal": f"【{step_label}】发生未知错误：{exc!r}，请稍后重试",
                    "logs": [*state.get("logs", []), _log_entry(step_label, "", "failed", repr(exc))],
                }

        return wrapped

    return deco


# ---------------- 节点 1：地理编码 ----------------

async def _node_geocode(state: GraphState) -> dict[str, Any]:
    req = state["req"]
    mode = state["mode"]
    key = state["amap_key"]
    if mode == "amap" and not key:
        raise ValueError("已选择「高德」数据源但未提供 AMAP Key，请在界面填写或在 .env 配置 AMAP_KEY")
    point, provider, citycode = await geo_mod.geocode(mode, req["destination"], key)
    return {
        "city": point.model_dump(),
        "citycode": citycode,
        "providers": {**state.get("providers", {}), "geocode": provider},
        "logs": [*state.get("logs", []), _log_entry("定位城市", provider, detail=f"{point.city} ({point.lat:.3f},{point.lon:.3f})")],
    }


# ---------------- 节点 2：天气 ----------------

async def _node_weather(state: GraphState) -> dict[str, Any]:
    req = state["req"]
    start, end = _request_date(req, "start_date"), _request_date(req, "end_date")
    geo = GeoPoint.model_validate(state["city"])
    days, provider, warns = await wx_mod.get_weather(geo, start, end, settings.openweathermap_key)
    return {
        "weather": [w.model_dump() for w in days],
        "providers": {**state.get("providers", {}), "weather": provider},
        "warnings": [*state.get("warnings", []), *warns],
        "logs": [*state.get("logs", []), _log_entry("查询天气", provider, detail=f"{len(days)} 天")],
    }


# ---------------- 节点 3：景点 POI ----------------

async def _node_pois(state: GraphState) -> dict[str, Any]:
    req = state["req"]
    geo = GeoPoint.model_validate(state["city"])
    pois, provider, warns = await poi_mod.get_pois(
        state["mode"], req["destination"], geo, req["preferences"],
        amap_key=state["amap_key"], selected=req.get("selected_pois") or None,
    )
    if not pois:
        raise WorkflowError("没有找到符合偏好的候选景点，请更换偏好或数据源后重试")
    return {
        "pois": [p.model_dump() for p in pois],
        "providers": {**state.get("providers", {}), "pois": provider},
        "warnings": [*state.get("warnings", []), *warns],
        "logs": [*state.get("logs", []), _log_entry("搜索景点", provider, detail=f"{len(pois)} 个候选")],
    }


# ---------------- 节点 4：酒店 ----------------

async def _node_hotels(state: GraphState) -> dict[str, Any]:
    req = state["req"]
    geo = GeoPoint.model_validate(state["city"])
    hotels, provider, warns = await poi_mod.get_hotels(
        state["mode"], req["destination"], geo, req["hotel_level"], amap_key=state["amap_key"]
    )
    if not hotels:
        raise WorkflowError("没有找到酒店信息，请更换数据源后重试")
    return {
        "hotels": [h.model_dump() for h in hotels],
        "providers": {**state.get("providers", {}), "hotels": provider},
        "warnings": [*state.get("warnings", []), *warns],
        "logs": [*state.get("logs", []), _log_entry("推荐酒店", provider, detail=f"{len(hotels)} 家")],
    }


# ---------------- 行程规则引擎 ----------------

def _group_by_location(items: list[dict], k: int) -> list[list[dict]]:
    """按坐标做轻量聚类（k-means 一次迭代），尽力让每天的景点在地理上邻近。"""
    k = max(1, min(k, len(items)))
    have_pts = [(i, it) for i, it in enumerate(items) if it.get("lat") is not None and it.get("lon") is not None]
    # 坐标不全时退化为轮流分配（均衡优先）
    if len(have_pts) < len(items) * 0.6:
        groups: list[list[dict]] = [[] for _ in range(k)]
        for idx, it in enumerate(items):
            groups[idx % k].append(it)
        return groups

    # 初始质心：按经度均匀取 k 个样本
    have_pts.sort(key=lambda x: x[1]["lon"])
    step = max(1, len(have_pts) // k)
    cents = [(it["lat"], it["lon"]) for _, it in have_pts[::step][:k]]
    while len(cents) < k:
        cents.append(cents[-1])

    assign: list[int] = [0] * len(items)
    for _ in range(8):
        new_assign: list[int] = []
        for i, it in enumerate(items):
            lat, lon = it.get("lat"), it.get("lon")
            if lat is None or lon is None:
                new_assign.append(0)
                continue
            best, best_d = 0, float("inf")
            for ci, (clat, clon) in enumerate(cents):
                d = (lat - clat) ** 2 + (lon - clon) ** 2
                if d < best_d:
                    best_d, best = d, ci
            new_assign.append(best)
        if new_assign == assign:
            break
        assign = new_assign
        # 更新质心
        for ci in range(k):
            mem = [items[i] for i in range(len(items)) if assign[i] == ci and items[i].get("lat") is not None]
            if mem:
                cents[ci] = (sum(m["lat"] for m in mem) / len(mem), sum(m["lon"] for m in mem) / len(mem))

    groups: list[list[dict]] = [[] for _ in range(k)]
    for i, it in enumerate(items):
        groups[assign[i]].append(it)
    # 清空组由最近的非空组匀一点过来（保证每天都有安排）
    non_empty = [g for g in groups if g]
    for gi, g in enumerate(groups):
        if not g and non_empty:
            src = non_empty[gi % len(non_empty)]
            if len(src) > 1:
                g.append(src.pop())
    return groups


def _nearest_order(group: list[dict]) -> list[dict]:
    """组内最近邻排序：从首个点出发依次衔接最近未访问点。"""
    pts = [(it.get("lat"), it.get("lon")) for it in group]
    if all(p[0] is not None for p in pts):
        order = [0]
        rest = set(range(1, len(group)))
        while rest:
            lat, lon = pts[order[-1]]
            nxt = min(rest, key=lambda j: (pts[j][0] - lat) ** 2 + (pts[j][1] - lon) ** 2)
            order.append(nxt)
            rest.remove(nxt)
        return [group[i] for i in order]
    return group


async def _plan_days(state: GraphState) -> list[dict[str, Any]]:
    req = state["req"]
    pois = state["pois"]
    days_total = req["days"]
    mode = req["travel_mode"]
    city_name = req["destination"]
    geo = GeoPoint.model_validate(state["city"])

    # 候选上限：每天至多 4 个景点
    max_stops = min(len(pois), days_total * 4)
    candidates = pois[:max_stops]
    groups = _group_by_location(candidates, days_total)
    groups = [_nearest_order(g) for g in groups]

    itinerary: list[dict[str, Any]] = []
    foods = (_entry(city_name) or {}).get("foods") or []
    rainy_days = {w["date"] for w in state["weather"] if w["precipitation_prob"] >= 60}

    for day_i in range(days_total):
        group = groups[day_i] if day_i < len(groups) else []
        stops: list[dict[str, Any]] = []
        segments = []
        for pos, poi in enumerate(group):
            slot = _SLOTS[pos % len(_SLOTS)]
            segment: dict[str, Any] | None = None
            if pos > 0:
                prev = group[pos - 1]
                segments.append(
                    rt_mod.get_segment(
                        mode,
                        prev["lat"], prev["lon"], poi["lat"], poi["lon"],
                        amap_key=state["amap_key"], citycode1=state["citycode"],
                    )
                )
            stops.append({"time_slot": slot, "poi_name": poi["name"], "poi_index": pois.index(poi), "note": ""})

        seg_results: list[RouteSegment] = await asyncio.gather(*segments) if segments else []
        for pos in range(1, len(stops)):
            stops[pos]["route_from_prev"] = seg_results[pos - 1].model_dump()

        d = (datetime.fromisoformat(req["start_date"]) + timedelta(days=day_i)).date()
        rainy = d.isoformat() in rainy_days
        dinner = ""
        if foods:
            dinner = f"{foods[day_i % len(foods)]}（{city_name}当地人气选择）"
        elif "美食" in req["preferences"]:
            dinner = f"当地特色美食街区（{city_name}口碑榜）"
        summary_parts = [s["poi_name"] for s in stops[:3]]
        summary = " → ".join(summary_parts) if summary_parts else "自由活动 / 休整"
        if rainy:
            summary += "；☔ 今日降水概率高，建议优先室内场馆并备伞"
        itinerary.append(
            DayPlan(
                day=day_i + 1,
                date=d.isoformat(),
                stops=[Stop.model_validate(s) for s in stops],
                dinner_recommendation=dinner,
                summary=summary,
            ).model_dump()
        )
    return itinerary


# ---------------- 节点 5：行程规划（规则 + 可选 LLM 润色） ----------------

async def _llm_enhance(state: GraphState) -> dict[str, Any]:
    """可选 LLM 增强：为缺失简介的 POI 补一句话 + 每日美食 + 总建议。失败不影响主流程。"""
    from langchain_core.messages import HumanMessage

    llm_cfg = resolve_llm(LlmConfig.model_validate(state["req"]["llm"]))
    if not llm_cfg:
        return {}
    req = state["req"]
    city_name = req["destination"]
    pois = state["pois"]
    weather_lines = [f"{w['date']} {w['condition']} {w['temp_min']:.0f}~{w['temp_max']:.0f}℃ 降水{w['precipitation_prob']:.0f}%" for w in state["weather"]]
    poi_names = [p["name"] for p in pois]
    hotels = [h["name"] for h in state["hotels"]]
    prompt = f"""你是旅行规划助手。请基于以下信息输出 JSON（不要输出其它内容）：
目的地：{city_name}；天数：{req['days']}；偏好：{'、'.join(req['preferences'])}；
天气：{'；'.join(weather_lines)}；
候选景点：{'、'.join(poi_names)}；
酒店候选：{'、'.join(hotels)}。

JSON 结构：
{{"poi_intros": {{"景点名": "一句话简介（30字内，仅针对你认为值得介绍的景点，最多5个）"}},
  "dinners": ["第1天晚餐建议（菜系或餐厅类型，一句）", ...共{req['days']}条],
  "travel_tip": "整体出行建议（80字内，结合天气/偏好）"}}

要求：poi_intros 的键必须严格取自候选景点名列表；dinners 长度必须为 {req['days']}。"""
    try:
        model = build_chat_model(llm_cfg)
        resp = await model.ainvoke([HumanMessage(content=prompt)])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)

        updated_pois = list(pois)
        for i, p in enumerate(updated_pois):
            if not p.get("intro") and p["name"] in (data.get("poi_intros") or {}):
                updated_pois[i] = {**p, "intro": f"{data['poi_intros'][p['name']]}（AI 生成，仅供参考）"}

        itinerary = list(state["itinerary"])
        dinners = data.get("dinners") or []
        for i, day in enumerate(itinerary):
            if i < len(dinners) and dinners[i]:
                day = dict(day)
                day["dinner_recommendation"] = dinners[i]
                itinerary[i] = day

        from backend.llm_factory import provider_display

        return {
            "pois": updated_pois,
            "itinerary": itinerary,
            "tip": data.get("travel_tip", ""),
            "providers": {**state.get("providers", {}), "llm": provider_display(llm_cfg)},
            "logs": [*state.get("logs", []), _log_entry("AI 润色", provider_display(llm_cfg), detail=f"模型 {llm_cfg.model or ''}")],
        }
    except Exception as exc:  # noqa: BLE001 —— LLM 失败仅告警，不阻塞
        return {
            "warnings": [*state.get("warnings", []), f"LLM 润色未生效（{exc}），已使用规则行程"],
            "logs": [*state.get("logs", []), _log_entry("AI 润色", "", "degraded", str(exc))],
        }


async def _node_itinerary(state: GraphState) -> dict[str, Any]:
    itinerary = await _plan_days(state)
    return {
        "itinerary": itinerary,
        "logs": [*state.get("logs", []), _log_entry("规划每日行程", "rule", detail=f"{len(itinerary)} 天")],
    }


# ---------------- 节点 6：预算 ----------------

async def _node_budget(state: GraphState) -> dict[str, Any]:
    req = state["req"]
    days_total = req["days"]
    travelers = req["travelers"]
    entry = _entry(req["destination"])

    # 酒店：按所选档位（酒店列表没有该档时取第一家）
    hotels = [Hotel.model_validate(h) for h in state["hotels"]]
    chosen = next((h for h in hotels if h.level == req["hotel_level"]), hotels[0])
    hotel_total = round(chosen.price_per_night * days_total, 0)
    hotel_item = BudgetItem(name="酒店住宿", amount=hotel_total, kind="hotel",
                            detail=f"{chosen.name}（{chosen.level} 档）{chosen.price_per_night:.0f} 元/晚 × {days_total} 晚（按 1 间计）")

    # 门票：仅计算行程实际安排的景点 × 人数
    planned_idx = {s["poi_index"] for day in state["itinerary"] for s in day["stops"]}
    pois = [Poi.model_validate(p) for p in state["pois"]]
    ticket_total = round(sum(pois[i].ticket for i in planned_idx if i < len(pois)) * travelers, 0)
    ticket_item = BudgetItem(name="景点门票", amount=ticket_total, kind="ticket",
                             detail=f"行程内 {len(planned_idx)} 个收费景点 × {travelers} 人")

    # 餐饮：手动每人每天餐标，否则城市基准
    per_day = req.get("daily_food_budget") or (entry or {}).get("food_per_day") or DEFAULT_FOOD_PER_DAY
    food_total = round(per_day * travelers * days_total, 0)
    food_item = BudgetItem(name="餐饮", amount=food_total, kind="food",
                           detail=f"{per_day:.0f} 元/人/天 × {travelers} 人 × {days_total} 天")

    # 交通：手动总预算覆盖，否则按模式估算
    if req.get("transport_budget"):
        transport_total = float(req["transport_budget"])
        transport_item = BudgetItem(name="市内交通", amount=transport_total, kind="transport",
                                    detail="用户手动设定")
    else:
        mode = req["travel_mode"]
        if mode == "walking":
            transport_total = 0.0
            transport_item = BudgetItem(name="市内交通", amount=0.0, kind="transport",
                                        detail="全程步行（如需打车/骑行请手动调整预算）")
        else:
            segs = [RouteSegment.model_validate(s["route_from_prev"])
                    for day in state["itinerary"] for s in day["stops"] if s.get("route_from_prev")]
            total_km = sum(s.distance_km for s in segs)
            if mode == "driving":
                per_km = 0.9
                transport_total = round(total_km * per_km + 6 * days_total, 0)  # 油费+停车
                transport_item = BudgetItem(name="市内交通", amount=transport_total, kind="transport",
                                            detail=f"自驾约 {total_km:.1f} km × {per_km} 元/km + 停车均摊")
            else:  # transit
                trips = max(days_total, len(segs))
                transport_total = round(3.5 * trips * travelers, 0)
                transport_item = BudgetItem(name="市内交通", amount=transport_total, kind="transport",
                                            detail=f"公交/地铁约 {trips} 段 × 3.5 元 × {travelers} 人")

    items = [hotel_item, ticket_item, food_item, transport_item]
    budget = Budget(items=items, total=round(sum(i.amount for i in items), 0),
                    note="预算为估算值：酒店为参考价（真实房价以预订为准），餐饮/交通可按消费水平调整。")
    return {
        "budget": budget.model_dump(),
        "logs": [*state.get("logs", []), _log_entry("汇总预算", "rule", detail=f"合计 ¥{budget.total:,.0f}")],
    }


# ---------------- 组装 ----------------

def _make_graph():
    g = StateGraph(GraphState)
    g.add_node("geocode", guard("定位城市")(_node_geocode))
    g.add_node("weather", guard("查询天气")(_node_weather))
    g.add_node("pois", guard("搜索景点")(_node_pois))
    g.add_node("hotels", guard("推荐酒店")(_node_hotels))
    g.add_node("itinerary", guard("规划行程")(_node_itinerary))
    g.add_node("llm_enhance", guard("AI 润色")(_llm_enhance))
    g.add_node("budget", guard("汇总预算")(_node_budget))

    g.set_entry_point("geocode")
    g.add_edge("geocode", "weather")
    g.add_edge("weather", "pois")
    g.add_edge("pois", "hotels")
    g.add_edge("hotels", "itinerary")
    g.add_edge("itinerary", "llm_enhance")
    g.add_edge("llm_enhance", "budget")
    g.add_edge("budget", END)
    return g.compile()


_APP = None


def get_graph():
    global _APP
    if _APP is None:
        _APP = _make_graph()
    return _APP


def resolve_mode(request: TravelRequest, amap_key: str) -> str:
    m = request.data_provider
    if m == "auto":
        return "amap" if amap_key else "openstreetmap"
    return m


async def run_workflow(request: TravelRequest) -> dict[str, Any]:
    """执行完整工作流，返回 GraphState（含天气/景点/酒店/行程/预算）。"""
    amap_key = request.amap_api_key or settings.amap_key
    mode = resolve_mode(request, amap_key)
    req = request.model_dump(mode="json")
    req["days"] = request.days  # days 是 property，model_dump 不含，需补上
    initial: GraphState = {
        "req": req,
        "mode": mode,
        "amap_key": amap_key,
        "warnings": [],
        "logs": [],
        "providers": {"data_mode": mode},
    }
    result = await get_graph().ainvoke(initial)
    if result.get("fatal"):
        raise WorkflowError(result["fatal"])
    return result
