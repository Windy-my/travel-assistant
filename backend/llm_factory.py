"""LLM 供应商工厂：OpenAI / Anthropic / Azure OpenAI / 智谱 / 百度千帆。

- 动态配置优先取请求体 LlmConfig；请求未带 Key 时回退 .env 的 DEFAULT_LLM_*；
- 智谱与百度千帆均提供 OpenAI 兼容端点，直接使用 ChatOpenAI 指向其 base_url；
- 演示/规则模式完全不依赖 LLM 也能产出完整计划；LLM 仅在可用时做内容润色。
"""
from __future__ import annotations

from backend.config import settings
from backend.schemas import LlmConfig

MODEL_DEFAULTS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "azure_openai": "",  # 使用 azure_deployment
    "zhipu": "glm-4-flash",
    "qianfan": "ernie-4.0-turbo-8k",
}

OPENAI_COMPAT_BASE_URL: dict[str, str] = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qianfan": "https://qianfan.baidubce.com/v2",
}


def resolve_llm(cfg: LlmConfig) -> LlmConfig | None:
    """返回“生效的”LLM 配置；没有任何 Key 时返回 None（关闭 LLM 功能）。"""
    api_key = (cfg.api_key or "").strip() or (settings.default_llm_api_key or "").strip()
    if not api_key:
        return None
    provider = cfg.provider
    model = cfg.model or settings.default_llm_model or MODEL_DEFAULTS.get(provider) or ""
    base_url = cfg.base_url or settings.default_llm_base_url or OPENAI_COMPAT_BASE_URL.get(provider)
    azure_endpoint = cfg.azure_endpoint or settings.default_llm_base_url or ""
    return LlmConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model or None,
        temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        azure_endpoint=azure_endpoint or None,
        azure_api_version=cfg.azure_api_version,
        azure_deployment=cfg.azure_deployment or cfg.model,
    )


def build_chat_model(cfg: LlmConfig):
    """根据生效配置构造 LangChain 聊天模型。Key 无效/服务不可达会抛异常，由调用方捕获。"""
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import AzureChatOpenAI, ChatOpenAI

    api_key = cfg.api_key
    temperature = cfg.temperature
    max_tokens = cfg.max_tokens

    if cfg.provider == "anthropic":
        return ChatAnthropic(
            model=cfg.model or MODEL_DEFAULTS["anthropic"],
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if cfg.provider == "azure_openai":
        if not cfg.azure_endpoint or not cfg.azure_deployment:
            raise ValueError("Azure OpenAI 需要 azure_endpoint 与 azure_deployment")
        return AzureChatOpenAI(
            azure_endpoint=cfg.azure_endpoint,
            azure_deployment=cfg.azure_deployment,
            api_version=cfg.azure_api_version,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    # openai / zhipu / qianfan（OpenAI 兼容）
    return ChatOpenAI(
        model=cfg.model or MODEL_DEFAULTS["openai"],
        api_key=api_key,
        base_url=cfg.base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=90,
    )


def provider_display(cfg: LlmConfig) -> str:
    names = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "azure_openai": "Azure OpenAI",
        "zhipu": "智谱 AI",
        "qianfan": "百度千帆",
    }
    return names.get(cfg.provider, cfg.provider)
