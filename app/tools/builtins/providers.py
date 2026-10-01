"""Builtin tools: weather (provider-backed) and web search (provider-backed).

Both follow the same shape: the *tool* owns schema/limits/normalization, the
*provider* owns the vendor. Providers are tried in configured order so one
outage never reaches the user (spec §108/§109).
"""

from __future__ import annotations

from typing import Any

from app.tools.errors import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult
from app.tools.providers.search import MAX_RESULTS, create_search_provider
from app.tools.providers.weather import FORECAST_DAYS, create_weather_provider

WEATHER_METADATA = ToolMetadata(
    name="weather",
    display_name="Weather",
    description="查询指定地点的实时天气和未来几天预报（温度、降水概率、天气状况）。",
    version="0.1.0",
    category="information",
    tags=["weather", "forecast", "rain"],
    keywords=["天气", "会下雨", "下雨吗", "气温", "温度", "预报", "冷不冷", "热不热", "带伞"],
    when_to_use="用户询问某地当前或未来天气、气温、是否需要带伞等实时信息时。",
    when_not_to_use="用户只是感慨天气（如“今天天气不错”）或在聊别的话题时，不要调用。",
    limitations=(
        "只提供气温、降水概率、天气状况和风力；不提供空气质量、潮汐等；"
        "地点不明确时优先使用用户资料中的城市，仍不确定则先询问。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "城市或地区名，例如 新加坡 / Shanghai",
            },
            "days": {
                "type": "integer",
                "description": "预报天数（1-7），默认 3",
                "minimum": 1,
                "maximum": 7,
                "default": FORECAST_DAYS,
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    cache_ttl_seconds=600.0,
    timeout=8.0,
)

SEARCH_METADATA = ToolMetadata(
    name="web_search",
    display_name="Web Search",
    description="搜索当前网络信息，返回标题、链接与摘要。用于需要最新事实的问题。",
    version="0.1.0",
    category="information",
    tags=["search", "news", "web"],
    keywords=["搜索", "查一下", "查查", "最新", "新闻", "最近有什么", "帮我找", "网上"],
    when_to_use="需要最新、实时或超出角色知识范围的事实信息时（新闻、产品、事件）。",
    when_not_to_use="日常闲聊、常识问题、或可以用已有记忆/工具回答的问题。",
    limitations=(
        "只返回标题/摘要/链接，不抓取整篇网页；结果可能不完整，信息不足时必须说明而不是编造。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {
                "type": "integer",
                "description": "返回条数上限（1-10）",
                "minimum": 1,
                "maximum": 10,
                "default": MAX_RESULTS,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    cache_ttl_seconds=300.0,
    timeout=12.0,
)


class WeatherTool(ToolBase):
    metadata = WEATHER_METADATA

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        credentials: Any = None,
        providers: list[Any] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or {}
        self._credentials = credentials
        self._providers = providers or []
        self._runtime_providers: list[Any] = []

    # ------------------------------------------------------------ providers

    def _build_providers(self) -> list[Any]:
        if self._runtime_providers:
            return self._runtime_providers
        names = self._provider_names()
        built = []
        for name in names:
            try:
                built.append(create_weather_provider(name, self.settings, self._credentials))
            except Exception:  # noqa: BLE001 - a bad provider must not break the tool
                self.log.warning("Weather provider '%s' could not be created", name)
        self._runtime_providers = built
        return built

    def _provider_names(self) -> list[str]:
        explicit = self.settings.get("provider")
        if explicit:
            return [str(explicit)]
        fallback = self.settings.get("fallback_providers")
        if isinstance(fallback, list) and fallback:
            return [str(name) for name in fallback]
        return ["open_meteo"]

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        location = str(
            arguments.get("location") or self.settings.get("default_location") or ""
        ).strip()
        if not location:
            return self.failure(
                self.metadata.name,
                "缺少地点：请先确认用户所在城市",
                error_type="invalid_arguments",
            )
        days = int(arguments.get("days") or FORECAST_DAYS)

        providers = self._providers or self._build_providers()
        if not providers:
            return self.failure(self.metadata.name, "没有可用的天气服务", error_type="unavailable")

        last_error = ""
        for provider in providers:
            try:
                payload = await provider.fetch(location, days=days)
            except Exception as exc:  # noqa: BLE001 - fall back to the next provider
                last_error = str(exc)
                self.log.warning("Weather provider %s failed: %s", provider.name, exc)
                continue
            return self._render(provider.name, payload)

        return self.failure(
            self.metadata.name, last_error or "天气服务暂不可用", error_type="external_service"
        )

    def _render(self, provider: str, payload: dict[str, Any]) -> ToolResult:
        current = payload.get("current") or {}
        forecast = payload.get("forecast") or []
        lines = [f"地点：{payload.get('location')}"]
        if current:
            lines.append(
                "当前：{condition}，{temp}°C，湿度 {humidity}%，风速 {wind} km/h".format(
                    condition=current.get("condition") or "未知",
                    temp=_fmt(current.get("temperature_c")),
                    humidity=_fmt(current.get("humidity")),
                    wind=_fmt(current.get("wind_kph")),
                )
            )
        for day in forecast[:FORECAST_DAYS]:
            lines.append(
                "{date}：{condition}，{low}~{high}°C，降水概率 {rain}%".format(
                    date=day.get("date"),
                    condition=day.get("condition") or "未知",
                    low=_fmt(day.get("low_c")),
                    high=_fmt(day.get("high_c")),
                    rain=_fmt(day.get("precipitation_probability")),
                )
            )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"location": payload.get("location"), "current": current, "forecast": forecast},
            summary="\n".join(lines),
            metadata={
                "source_type": "external",
                "confidence": 0.8,
                "source": f"weather:{provider}",
                "provider": provider,
            },
        )

    async def close(self) -> None:
        for provider in self._runtime_providers:
            await provider.close()


class WebSearchTool(ToolBase):
    metadata = SEARCH_METADATA

    def __init__(
        self,
        settings: dict[str, Any] | None = None,
        credentials: Any = None,
        providers: list[Any] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or {}
        self._credentials = credentials
        self._providers = providers or []
        self._runtime_providers: list[Any] = []

    def _build_providers(self) -> list[Any]:
        if self._runtime_providers:
            return self._runtime_providers
        names = self.settings.get("provider")
        name_list = [str(names)] if names else ["tavily", "brave"]
        built = []
        for name in name_list:
            try:
                provider = create_search_provider(name, self.settings, self._credentials)
            except Exception:  # noqa: BLE001
                continue
            if provider.configured:
                built.append(provider)
        if not built:
            built = [create_search_provider("null", self.settings, self._credentials)]
        self._runtime_providers = built
        return built

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return self.failure(
                self.metadata.name, "缺少搜索关键词", error_type="invalid_arguments"
            )
        limit = int(arguments.get("max_results") or self.settings.get("max_results") or MAX_RESULTS)
        limit = max(1, min(10, limit))

        providers = [
            provider
            for provider in (self._providers or self._build_providers())
            if getattr(provider, "configured", True)
        ]
        if not providers:
            return self.failure(
                self.metadata.name,
                "搜索服务未配置（需要在 WebUI 里填一个搜索 Provider 的凭据）",
                error_type="unavailable",
            )

        last_error = ""
        for provider in providers:
            try:
                results = await provider.search(query, limit=limit)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                last_error = str(exc)
                self.log.warning("Search provider %s failed: %s", provider.name, exc)
                continue

            if not results:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"query": query, "results": []},
                    summary=f"没有找到与「{query}」相关的可靠结果。",
                    metadata={
                        "source_type": "external",
                        "confidence": 0.2,
                        "source": f"search:{provider.name}",
                        "insufficient": True,
                    },
                )
            return self._render(provider.name, query, results)

        return self.failure(
            self.metadata.name,
            last_error or "搜索服务未配置或暂不可用",
            error_type="unavailable",
        )

    def _render(self, provider: str, query: str, results: list[dict[str, Any]]) -> ToolResult:
        lines = [f"搜索「{query}」的结果（最多 {len(results)} 条）："]
        for index, item in enumerate(results, start=1):
            lines.append(
                f"{index}. {item['title']}｜{item['snippet']}"
                f"（来源：{item['source'] or item['url']}）"
            )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"query": query, "results": results},
            summary="\n".join(lines),
            metadata={
                "source_type": "external",
                "confidence": 0.6,
                "source": f"search:{provider}",
                "provider": provider,
                "result_count": len(results),
            },
        )

    async def close(self) -> None:
        for provider in self._runtime_providers:
            await provider.close()


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)
