"""把完整旅行计划渲染为结构清晰的 Markdown（供下载/导出）。"""
from __future__ import annotations

from backend.schemas import TravelResponse

_MODE_LABEL = {"driving": "自驾", "walking": "步行", "transit": "公交/地铁", "cycling": "骑行"}
_LEVEL_LABEL = {"economy": "经济型", "comfort": "舒适型", "luxury": "高档型"}


def _fmt(v: float | int | None) -> str:
    if v is None:
        return "-"
    return f"{v:g}"


def render_travel_md(resp: TravelResponse) -> str:
    meta = resp.meta
    lines: list[str] = []
    lines.append(f"# {meta.destination} {meta.days} 日旅行计划（{meta.start_date} ~ {meta.end_date}）")
    lines.append("")
    lines.append(f"> 出行方式：{_MODE_LABEL.get(meta.travel_mode, meta.travel_mode)}　·　"
                 f"数据源：{meta.actual_providers.get('data_mode', meta.data_provider)}")
    if meta.actual_providers.get("llm"):
        lines.append(f"> AI 润色：{meta.actual_providers['llm']}")
    if meta.warnings:
        lines.append(">")
        for w in meta.warnings:
            lines.append(f"> ⚠️ {w}")
    lines.append("")

    # 天气
    lines.append("## 天气概览")
    lines.append("")
    for w in resp.weather:
        tip = f"（{w.advice}）" if w.advice else ""
        lines.append(f"- {w.date}：{w.condition}，{_fmt(w.temp_min)}°C ~ {_fmt(w.temp_max)}°C，"
                     f"降水概率 {_fmt(w.precipitation_prob)}%{tip}")
    lines.append("")

    # 景点
    lines.append("## 推荐景点")
    lines.append("")
    for i, p in enumerate(resp.pois, 1):
        tags = "、".join(p.tags) if p.tags else p.category
        rating = f"，评分 {p.rating}" if p.rating is not None else ""
        ticket = f"，门票 ¥{_fmt(p.ticket)}" if p.ticket else ""
        lines.append(f"{i}. **{p.name}**（{tags}）{rating}{ticket}")
        if p.intro:
            lines.append(f"   - {p.intro}")
        if p.opening_hours or p.address:
            detail = "；".join(filter(None, [p.opening_hours and f"开放时间：{p.opening_hours}",
                                             p.address and f"地址：{p.address}"]))
            if detail:
                lines.append(f"   - {detail}")
    lines.append("")

    # 酒店
    lines.append("## 推荐酒店")
    lines.append("")
    for i, h in enumerate(resp.hotels, 1):
        rating = f"，评分 {h.rating}" if h.rating is not None else ""
        est = "（参考价·估算）" if h.price_is_estimated else ""
        lines.append(f"{i}. {h.name}（{_LEVEL_LABEL.get(h.level, h.level)}）"
                     f"¥{_fmt(h.price_per_night)}/晚{rating}{est}")
        if h.address:
            lines.append(f"   - 地址：{h.address}")
    lines.append("")

    # 每日行程
    lines.append("## 每日行程（{}）".format(_MODE_LABEL.get(meta.travel_mode, meta.travel_mode)))
    lines.append("")
    for day in resp.itinerary:
        lines.append(f"### Day {day.day}（{day.date}）")
        lines.append("")
        if day.summary:
            lines.append(f"> {day.summary}")
            lines.append("")
        for s in day.stops:
            seg = s.route_from_prev
            transit = ""
            if seg:
                est_flag = "（估算）" if seg.estimated else ""
                transit = f"　[← {_fmt(seg.distance_km)} km / {_fmt(seg.duration_min)} 分钟{est_flag}]"
            lines.append(f"- {s.time_slot}：{s.poi_name}{transit}")
        if day.dinner_recommendation:
            lines.append(f"- 晚餐推荐：{day.dinner_recommendation}")
        lines.append("")

    # tip 由工作流保留在 state，未进入 TravelResponse；如需展示可后续扩展 travel_tip 字段

    # 预算
    lines.append("## 预算汇总")
    lines.append("")
    lines.append("| 项目 | 金额 | 说明 |")
    lines.append("| --- | --- | --- |")
    for item in resp.budget.items:
        name = {"hotel": "酒店", "ticket": "门票", "food": "餐饮", "transport": "交通"}.get(item.kind, item.name)
        lines.append(f"| {name} | ¥{_fmt(item.amount)} | {item.detail} |")
    lines.append(f"| **总计** | **¥{_fmt(resp.budget.total)}** | {resp.budget.note} |")
    lines.append("")
    lines.append("---")
    lines.append("*本计划由 Travel Assistant 自动生成，价格为估算值，请以实际预订为准。*")
    return "\n".join(lines)
