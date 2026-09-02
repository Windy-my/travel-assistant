"""Provider 基础：自定义异常与 HTTP 帮助函数。"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

UA = "travel-assistant-demo/0.1 (langchain travel planner; contact: local)"

_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
    return _shared_client


class DataSourceError(RuntimeError):
    """数据源通用错误（网络失败、HTTP 错误等）。"""


class ProviderUnavailable(DataSourceError):
    """当前配置下该数据源不可用（如缺少 Key、服务不支持某出行方式）。"""


class CityNotSupported(DataSourceError):
    """演示模式下目的地不在内置城市库 / 地理编码未命中。"""


async def http_get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    """GET 并解析 JSON，带一次重试；失败抛 DataSourceError。"""
    last_err: Exception | None = None
    for attempt in (0, 1):
        try:
            resp = await _client().get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:  # 4xx/5xx 重试无意义
            raise DataSourceError(f"HTTP {exc.response.status_code} @ {exc.request.url}") from exc
        except Exception as exc:  # 网络/超时
            last_err = exc
            if attempt == 0:
                await asyncio.sleep(0.4)
    raise DataSourceError(f"请求失败: {url} -> {last_err}")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间球面直线距离（千米）。"""
    import math

    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(r * 2 * math.asin(math.sqrt(a)), 2)
