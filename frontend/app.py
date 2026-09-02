"""旅行助手智能体 —— Streamlit 前端骨架。

运行：streamlit run frontend/app.py（或 docker compose up）
配置（侧边栏）：后端地址、数据源、可选 LLM / 高德 Key（仅随请求发送，不落盘）。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta

import streamlit as st

BACKEND_DEFAULT = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

MODE_LABEL = {"auto": "自动（推荐）", "demo": "演示数据（离线）", "openstreetmap": "OpenStreetMap（免费真实）", "amap": "高德地图（需 Key）"}
MODE_REVERSE = {v: k for k, v in MODE_LABEL.items()}
TRAVEL_MODE_LABEL = {"driving": "驾车", "walking": "步行", "transit": "公交/地铁"}
TRAVEL_MODE_REVERSE = {v: k for k, v in TRAVEL_MODE_LABEL.items()}
HOTEL_LABEL = {"economy": "经济型", "comfort": "舒适型", "luxury": "高档型"}
HOTEL_REVERSE = {v: k for k, v in HOTEL_LABEL.items()}
PREF_OPTIONS = ["历史文化", "博物馆", "自然风光", "美食", "购物", "亲子", "休闲", "地标", "夜生活"]


def post_json(url: str, payload: dict, timeout: int = 240) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw).get("detail", raw)
        except json.JSONDecodeError:
            detail = raw
        raise RuntimeError(f"后端返回 {exc.code}：{detail}") from exc


def _render_weather(weather: list[dict]) -> None:
    if not weather:
        st.info("无天气数据")
        return
    for i in range(0, len(weather), 3):
        cols = st.columns(3)
        for j, w in enumerate(weather[i : i + 3]):
            with cols[j]:
                st.markdown(
                    f"**{w['date']}**　{w['condition']}\n\n"
                    f"{w['temp_min']:.0f} ~ {w['temp_max']:.0f}°C\n\n"
                    f"🌧 {w['precipitation_prob']:.0f}%　💨 {w['wind_kmh']:.0f} km/h"
                )
                if w.get("advice"):
                    st.caption(f"💡 {w['advice']}")


def _render_pois(pois: list[dict]) -> None:
    for p in pois:
        tags = "、".join(p.get("tags") or []) or p.get("category", "")
        rating = f"⭐ {p['rating']}" if p.get("rating") is not None else ""
        ticket = f"🎫 ¥{p['ticket']:g}" if p.get("ticket") else ""
        with st.expander(f"{p['name']}　{tags}　{rating}　{ticket}"):
            if p.get("intro"):
                st.write(p["intro"])
            row = []
            if p.get("opening_hours"):
                row.append(f"🕐 {p['opening_hours']}")
            if p.get("address"):
                row.append(f"📍 {p['address']}")
            if p.get("source"):
                row.append(f"数据源：{p['source']}")
            if row:
                st.caption("　·　".join(row))


def _render_hotels(hotels: list[dict]) -> None:
    rows = [
        {
            "酒店": h["name"],
            "档位": HOTEL_LABEL.get(h.get("level"), h.get("level", "")),
            "参考价(元/晚)": f"¥{h['price_per_night']:g}" + ("*" if h.get("price_is_estimated") else ""),
            "评分": h.get("rating") if h.get("rating") is not None else "-",
            "距市中心(km)": h.get("distance_to_center_km") if h.get("distance_to_center_km") is not None else "-",
            "地址": h.get("address", ""),
        }
        for h in hotels
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption("* 参考价为按档位估算（公开 API 不提供实时房价），请以实际预订为准。")


def _render_itinerary(itinerary: list[dict], mode: str) -> None:
    for day in itinerary:
        st.subheader(f"Day {day['day']}（{day['date']}）")
        if day.get("summary"):
            st.markdown(f"> {day['summary']}")
        for s in day["stops"]:
            seg = s.get("route_from_prev")
            tail = ""
            if seg:
                est = "（估算）" if seg.get("estimated") else ""
                tail = f"　`← {seg['distance_km']} km · {seg['duration_min']} 分钟{est}`"
            st.markdown(f"- **{s['time_slot']}**：{s['poi_name']}{tail}")
        if day.get("dinner_recommendation"):
            st.markdown(f"- 🍽 晚餐推荐：{day['dinner_recommendation']}")
        st.divider()


def _render_budget(budget: dict) -> None:
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric("总预算", f"¥{budget['total']:,.0f}")
        st.caption(budget.get("note", ""))
    with c2:
        rows = []
        kind_name = {"hotel": "酒店", "ticket": "门票", "food": "餐饮", "transport": "交通"}
        for item in budget["items"]:
            rows.append({
                "项目": kind_name.get(item["kind"], item["name"]),
                "金额": f"¥{item['amount']:,.0f}",
                "说明": item["detail"],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(page_title="旅行助手智能体", page_icon="🧳", layout="wide")
    st.title("🧳 旅行助手智能体")
    st.caption("LangGraph 六步工作流（天气 → 景点 → 酒店 → 行程 → 预算）· FastAPI 后端 · 支持导出 Markdown")

    # ---------------- 侧边栏：服务与模型配置 ----------------
    with st.sidebar:
        st.header("⚙️ 服务配置")
        backend_url = st.text_input("后端 API 地址", BACKEND_DEFAULT)
        st.caption("docker compose 启动后前端已自动指向 http://backend:8000")

        data_mode = st.selectbox("数据源", list(MODE_LABEL.values()), index=0)
        amap_key = st.text_input("高德 AMAP_KEY（可选）", type="password",
                                 help="勾选自动/高德数据源时可在此按次输入，仅用于本次请求")
        use_llm = st.checkbox("启用 LLM 润色", value=False, help="填写下方任一供应商 Key 后生效；留空使用规则引擎生成行程")
        with st.expander("🧠 LLM 模型设置"):
            llm_provider = st.selectbox("供应商", ["openai", "anthropic", "azure_openai", "zhipu", "qianfan"])
            llm_key = st.text_input("API Key", type="password")
            llm_model = st.text_input("模型名称（留空用默认）", value="")
            llm_base = st.text_input("Base URL（可选）", value="")
            temperature = st.slider("温度", 0.0, 1.5, 0.2, 0.1)
            azure_endpoint = st.text_input("Azure Endpoint（azure 必填）", value="")
            azure_deployment = st.text_input("Azure Deployment（azure 必填）", value="")
            st.caption("Key 只随本次请求发送，服务端不保存")

    # ---------------- 主区：输入表单 ----------------
    today = date.today()
    with st.form("travel_form"):
        c1, c2 = st.columns([2, 2])
        with c1:
            destination = st.text_input("目的地（城市）", value="北京", placeholder="如：北京 / 上海 / 成都 / 西安")
        with c2:
            date_range = st.date_input(
                "日期范围（起止）", value=(today, today + timedelta(days=2)),
                min_value=today, max_value=today + timedelta(days=15),
            )
        c3, c4, c5 = st.columns(3)
        with c3:
            preferences = st.multiselect("旅行偏好（可多选）", PREF_OPTIONS, default=["历史文化", "美食"])
        with c4:
            travel_mode = st.radio("出行方式", list(TRAVEL_MODE_LABEL.values()), horizontal=True)
        with c5:
            hotel_level = st.selectbox("酒店档位", list(HOTEL_LABEL.values()), index=1)
        c6, c7 = st.columns(2)
        with c6:
            travelers = st.number_input("同行人数", 1, 20, 2)
        with c7:
            daily_food_budget = st.number_input("餐饮预算（元/人/天，0=按城市估算）", 0, 2000, 0)
        submitted = st.form_submit_button("🚀 生成旅行计划", type="primary", use_container_width=True)

    if submitted:
        if len(date_range) != 2:
            st.error("请选择完整的起止日期")
            st.stop()
        if not destination.strip():
            st.error("请填写目的地")
            st.stop()
        payload = {
            "destination": destination.strip(),
            "start_date": str(date_range[0]),
            "end_date": str(date_range[1]),
            "preferences": preferences or ["历史文化"],
            "travel_mode": TRAVEL_MODE_REVERSE[travel_mode],
            "travelers": int(travelers),
            "data_provider": MODE_REVERSE[data_mode],
            "amap_api_key": amap_key.strip(),
            "hotel_level": HOTEL_REVERSE[hotel_level],
            "daily_food_budget": int(daily_food_budget) or None,
            "llm": {
                "provider": llm_provider,
                "api_key": llm_key.strip() if use_llm else "",
                "model": llm_model.strip() or None,
                "base_url": llm_base.strip() or None,
                "temperature": temperature,
                "azure_endpoint": azure_endpoint.strip() or None,
                "azure_deployment": azure_deployment.strip() or None,
            },
        }
        try:
            with st.status("🔄 正在执行六步工作流（天气 → 景点 → 酒店 → 行程 → 预算）…", expanded=True) as status:
                st.session_state["result"] = post_json(f"{backend_url.rstrip('/')}/api/generate", payload)
                st.session_state["result_payload"] = payload
                status.update(label="✅ 生成完成", state="complete")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
            st.stop()

    result = st.session_state.get("result")
    if not result:
        st.info("👈 填写左侧表单后点击「生成旅行计划」。无任何 Key 也能用演示/免费数据源体验完整流程。")
        st.stop()

    # ---------------- 结果展示 ----------------
    meta = result["meta"]
    st.success(f"🎉 {meta['destination']} {meta['days']} 日计划已生成（{meta['start_date']} ~ {meta['end_date']}）")

    providers = meta.get("actual_providers", {})
    chips = "　·　".join(
        f"{k}: **{v}**" for k, v in providers.items() if v
    )
    st.markdown(f"实际数据源：{chips}")
    for w in meta.get("warnings", []):
        st.warning(f"⚠️ {w}")
    if meta.get("data_provider") == "demo":
        st.caption("当前为演示数据（内置北京/上海/成都/西安），可在侧边栏切换 OpenStreetMap 或高德数据源获取真实数据。")

    dl_name = f"{meta['destination']}_{meta['start_date']}_{meta['end_date']}_旅行计划.md"
    st.download_button("⬇️ 下载 Markdown 旅行计划", data=result.get("markdown", ""),
                       file_name=dl_name, mime="text/markdown", type="primary")

    tabs = st.tabs(["🌦 天气", "🏛 景点", "🏨 酒店", "🗺 每日行程", "💰 预算", "🧾 执行日志"])
    with tabs[0]:
        _render_weather(result["weather"])
    with tabs[1]:
        st.caption("可在下方勾选心仪景点后重新生成，行程将只包含所选景点。")
        names = [p["name"] for p in result["pois"]]
        chosen = st.multiselect("勾选要保留的景点（默认全部）", names, default=names)
        if st.button("按勾选重新生成", disabled=len(chosen) == len(names)):
            payload = dict(st.session_state.get("result_payload", {}))
            payload["selected_pois"] = chosen
            try:
                with st.status("🔄 重新生成中…", expanded=True):
                    st.session_state["result"] = post_json(
                        f"{backend_url.rstrip('/')}/api/generate", payload)
                    st.session_state["result_payload"] = payload
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        _render_pois(result["pois"])
    with tabs[2]:
        _render_hotels(result["hotels"])
    with tabs[3]:
        _render_itinerary(result["itinerary"], meta["travel_mode"])
    with tabs[4]:
        _render_budget(result["budget"])
    with tabs[5]:
        for log in result.get("logs", []):
            icon = {"ok": "✅", "degraded": "⚠️", "failed": "❌"}.get(log.get("status"), "·")
            st.markdown(f"{icon} **{log['step']}**　数据源：`{log.get('provider') or '-'}`　{log.get('detail', '')}")


if __name__ == "__main__":
    main()
