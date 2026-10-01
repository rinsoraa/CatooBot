"""Lightweight i18n for the WebUI.

The default language is Chinese; a ``catoobot_lang`` cookie (toggled in the top
bar) switches the shell to English. ``L(text)`` returns the English translation
when the current language is ``en`` and a mapping exists, otherwise the Chinese
original — so translations can be added incrementally without a parallel key
namespace.
"""

from __future__ import annotations

import contextvars
from typing import Any

LANG_COOKIE = "catoobot_lang"

#: zh -> en. Only the shared chrome and the most common labels live here; the
#: default stays complete Chinese, and EN is a convenience overlay.
TRANSLATIONS: dict[str, str] = {
    # nav
    "仪表盘": "Dashboard",
    "角色": "Character",
    "行为": "Behavior",
    "话题": "Topics",
    "记忆": "Memory",
    "用户": "Users",
    "群组": "Groups",
    "会话": "Sessions",
    "模型": "Models",
    "配置": "Config",
    "提示词": "Prompts",
    "日志": "Logs",
    "工具": "Tools",
    "Agent": "Agent",
    "凭据": "Credentials",
    "运行": "Runtime",
    "概览": "Overview",
    "数据": "Data",
    "能力": "Capabilities",
    # topbar / actions
    "退出": "Sign out",
    "退出登录": "Sign out",
    "折叠 / 展开左侧导航": "Toggle sidebar",
    "切换日间 / 夜间模式": "Toggle light/dark",
    "登录": "Sign in",
    "保存": "Save",
    "删除": "Delete",
    "编辑": "Edit",
    "筛选": "Filter",
    "启用": "Enable",
    "禁用": "Disable",
    "开启": "On",
    "关闭": "Off",
    "重置": "Reset",
    "应用": "Apply",
    "详情": "Details",
    "查看": "View",
    "回到顶部": "Back to top",
    "用户名": "Username",
    "密码": "Password",
    # common stats
    "在线": "Online",
    "离线": "Offline",
    "搜索": "Search",
    "时间线": "Timeline",
    "健康度": "Health",
    "向量": "Embeddings",
    "巩固": "Consolidation",
    "检索调试": "Retrieval Debug",
    "浏览": "Browse",
    "开发者": "Developer",
    "QQ 端零指令 · 一切管理在此": "Zero commands on QQ · manage everything here",
}

_lang: contextvars.ContextVar[str] = contextvars.ContextVar(LANG_COOKIE, default="zh")


def normalize_lang(value: str | None) -> str:
    lang = (value or "zh").strip().lower()
    return lang if lang in ("zh", "en") else "zh"


def set_lang(lang: str) -> None:
    _lang.set(normalize_lang(lang))


def current_lang() -> str:
    return _lang.get()


def L(text: Any) -> str:
    """Localize a UI string (identity when no translation exists)."""
    body = str(text)
    if current_lang() != "en":
        return body
    return TRANSLATIONS.get(body, body)


def lang_from(mapping: Any) -> str:
    getter = getattr(mapping, "get", None)
    if getter is None:
        return "zh"
    return normalize_lang(getter(LANG_COOKIE))


def en_flag(lang: str) -> bool:
    return normalize_lang(lang) == "en"
