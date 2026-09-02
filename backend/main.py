"""FastAPI 入口：REST API（/api/generate 等）。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.cache import cache_info
from backend.config import settings
from backend.graph import WorkflowError, run_workflow
from backend.markdown import render_travel_md
from backend.schemas import (
    DATA_PROVIDERS,
    LLM_PROVIDERS,
    PREFERENCE_POOL,
    TRAVEL_MODES,
    TravelMeta,
    TravelRequest,
    TravelResponse,
)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="旅行助手智能体后端：LangGraph 六步工作流 + 多数据源 + 可导出 Markdown。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_response(request: TravelRequest, state: dict) -> TravelResponse:
    meta = TravelMeta(
        destination=request.destination,
        start_date=request.start_date.isoformat(),
        end_date=request.end_date.isoformat(),
        days=request.days,
        travel_mode=request.travel_mode,
        data_provider=state.get("mode", request.data_provider),
        actual_providers=state.get("providers", {}),
        warnings=state.get("warnings", []),
    )
    return TravelResponse(
        meta=meta,
        city=state["city"],
        weather=state.get("weather", []),
        pois=state.get("pois", []),
        hotels=state.get("hotels", []),
        itinerary=state.get("itinerary", []),
        budget=state.get("budget", {}),
        logs=state.get("logs", []),
    )


@app.get("/")
async def root() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "endpoints": ["/api/health", "/api/providers", "/api/generate", "/api/export/markdown"],
        "frontend": "Streamlit（见仓库 frontend/）",
    }


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "cache": cache_info(),
        "amap_key_configured": bool(settings.amap_key),
        "openweathermap_key_configured": bool(settings.openweathermap_key),
        "default_llm_configured": bool(settings.default_llm_api_key),
    }


@app.get("/api/providers")
async def providers() -> dict:
    return {
        "llm": [
            {"id": p, "key": "api_key", "note": "按次输入，仅本次请求使用"}
            for p in LLM_PROVIDERS
        ],
        "data": [
            {"id": "demo", "key": None,
             "note": "内置北京/上海/成都/西安 curated 数据，离线可跑，结果完整（价格/评分均为演示值）"},
            {"id": "openstreetmap", "key": None,
             "note": "免费真实数据：Open-Meteo 天气 + Overpass POI + OSRM 路线；无评分/门票，酒店价格为估算"},
            {"id": "amap", "key": "amap_api_key / 环境变量 AMAP_KEY",
             "note": "高德地图：POI/酒店/自驾/步行/公交全链路，中文最佳；申请 https://console.amap.com/dev/key/app"},
            {"id": "auto", "key": None, "note": "自动：有 AMAP Key 用高德，否则 openstreetmap（必要时降级演示数据）"},
        ],
        "travel_modes": list(TRAVEL_MODES),
        "preferences": PREFERENCE_POOL,
    }


@app.post("/api/generate", response_model=TravelResponse)
async def generate(payload: TravelRequest) -> TravelResponse:
    """执行完整六步工作流并返回结构化结果 + Markdown。"""
    try:
        state = await run_workflow(payload)
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"工作流执行失败：{exc!r}") from exc

    response = _build_response(payload, state)
    response.markdown = render_travel_md(response)
    return response


@app.post("/api/export/markdown")
async def export_markdown(payload: dict) -> dict:
    """接收 /api/generate 的结果 JSON，返回 Markdown 文本（也可由前端直接使用内嵌 markdown）。"""
    try:
        resp = TravelResponse.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"结果 JSON 无效：{exc}") from exc
    return {"markdown": render_travel_md(resp)}
