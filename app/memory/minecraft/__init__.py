"""Phase 5C：Minecraft 记忆域（身份桥 + 持久世界记忆 + 检索 + 对账）。

对外只暴露四件事：身份解析、把世界事实写成记忆、把记忆和当前世界对账、按需检索。
**Memory 只提供上下文，不提供权限**（§二）——它永远不能改世界、不能改 Policy。
"""

from app.memory.minecraft.model import (
    CATEGORY_BY_KIND,
    DEFAULT_CONFIDENCE,
    DOMAIN,
    FactSource,
    Freshness,
    MinecraftMemoryFact,
    MinecraftMemoryKind,
    cluster_position,
    confidence_for,
    looks_like_directive,
)
from app.memory.minecraft.reconcile import (
    MinecraftMemoryReconciler,
    ReconcileReport,
)
from app.memory.minecraft.retrieval import (
    MAX_CONTEXT_ITEMS,
    MinecraftMemoryContext,
    MinecraftMemoryRetriever,
)
from app.memory.minecraft.store import MinecraftMemoryStore, minecraft_scope_key
from app.memory.minecraft.writer import MinecraftMemoryWriter

__all__ = [
    "CATEGORY_BY_KIND",
    "MAX_CONTEXT_ITEMS",
    "MinecraftMemoryContext",
    "MinecraftMemoryReconciler",
    "MinecraftMemoryRetriever",
    "MinecraftMemoryWriter",
    "ReconcileReport",
    "DEFAULT_CONFIDENCE",
    "DOMAIN",
    "FactSource",
    "Freshness",
    "MinecraftMemoryFact",
    "MinecraftMemoryKind",
    "MinecraftMemoryStore",
    "cluster_position",
    "confidence_for",
    "looks_like_directive",
    "minecraft_scope_key",
]
