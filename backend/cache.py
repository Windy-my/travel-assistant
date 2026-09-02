"""基于 diskcache/cachetools 的短期缓存：相同「数据源+目的地+日期+偏好」避免重复调用计费 API。

此处使用进程内 cachetools.TTLCache；如需跨进程/跨实例共享，可替换为 Redis（见 README）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from cachetools import TTLCache

from backend.config import settings

_cache: TTLCache[str, Any] = TTLCache(maxsize=512, ttl=settings.cache_ttl_seconds)


def _make_key(prefix: str, **parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def cached(prefix: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """装饰器：按入参哈希缓存异步函数的返回值。"""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        import inspect

        is_async = inspect.iscoroutinefunction(func)

        async def awrapper(*args: Any, **kwargs: Any) -> Any:
            key = _make_key(prefix, args=args, kwargs=kwargs)
            if key in _cache:
                return _cache[key]
            result = await func(*args, **kwargs)
            _cache[key] = result
            return result

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = _make_key(prefix, args=args, kwargs=kwargs)
            if key in _cache:
                return _cache[key]
            result = func(*args, **kwargs)
            _cache[key] = result
            return result

        return awrapper if is_async else wrapper

    return decorator


def cache_info() -> dict[str, Any]:
    return {
        "maxsize": _cache.maxsize,
        "currsize": len(_cache),
        "ttl": _cache.ttl,
    }
