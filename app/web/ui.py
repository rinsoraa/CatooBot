"""WebUI presentation layer: theme tokens, app shell, component helpers.

Server-rendered (aiohttp, no build step) but styled like a modern admin app:

* **light / dark mode** plus five accent themes, persisted in cookies so the
  first byte of HTML is already themed (no flash), with a client-side toggle;
* a **sidebar shell** with a top bar, page transitions and micro-animations;
* **hover tooltips** for every control: pass ``tip=`` to any helper, or use
  ``attr_tip()`` on your own markup (``?`` badge for prose-level help);
* small vanilla-JS touches: table filtering, confirm dialogs, copy buttons,
  toast messages, collapsible sidebar, ``/`` to focus the filter.

Design tokens follow the reference UI (HeroUI/NapCat): HSL semantic colours,
``0.75rem`` radii, soft layered shadows, 150–250 ms transitions.
"""

from __future__ import annotations

import contextvars
import html
import json
from collections.abc import Iterable, Sequence
from typing import Any

from app.web.i18n import L, current_lang, normalize_lang

COOKIE_MODE = "catoobot_mode"
COOKIE_THEME = "catoobot_theme"
COOKIE_NAV = "catoobot_nav"
COOKIE_LANG = "catoobot_lang"

MODES: tuple[str, ...] = ("light", "dark")

#: accent themes: key -> (label, primary hsl, primary-foreground hsl, soft tint)
THEMES: dict[str, tuple[str, str, str]] = {
    "blue": ("雾蓝", "217 91% 60%", "210 40% 98%"),
    "sakura": ("樱花", "340 82% 62%", "0 0% 100%"),
    "matcha": ("抹茶", "152 55% 45%", "0 0% 100%"),
    "violet": ("紫罗兰", "262 83% 62%", "0 0% 100%"),
    "amber": ("琥珀", "32 95% 52%", "30 40% 10%"),
}

VERSION = "v0.8"
AUTHOR = "Rinsora"

_DEFAULT_PREFS: dict[str, str] = {"mode": "light", "theme": "blue", "nav": "open", "lang": "zh"}
_prefs: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "catoobot_ui_prefs", default=None
)


# --------------------------------------------------------------------- prefs


def normalize_mode(value: str | None) -> str:
    mode = (value or "light").strip().lower()
    return mode if mode in MODES else "light"


def normalize_theme(value: str | None) -> str:
    theme = (value or "blue").strip().lower()
    return theme if theme in THEMES else "blue"


def preferences_from(mapping: Any) -> dict[str, str]:
    """Read UI preferences from a cookie container (or any mapping)."""
    getter = getattr(mapping, "get", None)
    if getter is None:
        return dict(_DEFAULT_PREFS)
    return {
        "mode": normalize_mode(getter(COOKIE_MODE)),
        "theme": normalize_theme(getter(COOKIE_THEME)),
        "nav": "collapsed" if (getter(COOKIE_NAV) or "") == "collapsed" else "open",
        "lang": normalize_lang(getter(COOKIE_LANG)),
    }


def set_preferences(prefs: dict[str, str]) -> None:
    _prefs.set(prefs)


def current_preferences() -> dict[str, str]:
    return _prefs.get() or dict(_DEFAULT_PREFS)


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


# ------------------------------------------------------------------ tooltips


def tip(text: str, *, label: str = "?") -> str:
    """Inline help badge: hover (or focus) shows plain-language explanation."""
    if not text:
        return ""
    return (
        f'<span class="tip" tabindex="0" role="note" aria-label="{esc(text)}"'
        f' data-tip="{esc(text)}">{esc(label)}</span>'
    )


def attr_tip(text: str) -> str:
    """Markup fragment adding a tooltip to any element."""
    return f' data-tip="{esc(text)}"' if text else ""


# ----------------------------------------------------------------- components


def stat(
    label: str, value: Any, *, tip_text: str = "", hint: str = "", tone: str = ""
) -> str:
    """A metric tile: big value, hover explanation, optional footnote."""
    klass = f"stat {tone}".strip()
    hint_html = f"<span class='hint'>{esc(hint)}</span>" if hint else ""
    return (
        f"<div class='{esc(klass)}'{attr_tip(tip_text)}>"
        f"<span class='label'>{esc(label)}</span>"
        f"<b>{esc(value)}</b>{hint_html}</div>"
    )


def stats_grid(items: Sequence[tuple[str, Any, str]]) -> str:
    """``[(label, value, tooltip), ...]`` -> a grid of metric tiles."""
    return (
        "<div class='grid'>"
        + "".join(stat(label, value, tip_text=tip_text) for label, value, tip_text in items)
        + "</div>"
    )


def badge(text: str, tone: str = "default") -> str:
    return f'<span class="badge badge-{esc(tone)}">{esc(text)}</span>'


def status_badge(value: Any, *, truthy: str = "开启", falsy: str = "关闭") -> str:
    ok = bool(value)
    return badge(truthy if ok else falsy, "success" if ok else "default")


def kv(pairs: Iterable[tuple[str, Any]], *, tips: dict[str, str] | None = None) -> str:
    tips = tips or {}
    rows = "".join(
        f"<div class='kv-row'><span class='kv-key'>{esc(key)}"
        f"{tip(tips.get(key, '')) if tips.get(key) else ''}</span>"
        f"<span class='kv-value'>{esc(value)}</span></div>"
        for key, value in pairs
    )
    return f"<div class='kv'>{rows}</div>"


def progress(value: float, *, tone: str = "primary", label: str = "") -> str:
    pct = max(0.0, min(1.0, float(value))) * 100
    text = label or f"{pct:.0f}%"
    return (
        f"<div class='progress'><div class='progress-bar tone-{esc(tone)}'"
        f" style='width:{pct:.1f}%'></div></div><span class='muted'>{esc(text)}</span>"
    )


def card(
    title: str,
    body: str,
    *,
    tip_text: str = "",
    actions: str = "",
    klass: str = "",
) -> str:
    head = ""
    if title or actions:
        head = (
            f"<div class='card-head'><h3>{esc(title)}{tip(tip_text) if tip_text else ''}</h3>"
            f"<div class='card-actions'>{actions}</div></div>"
        )
    return f"<section class='card {esc(klass)}'>{head}{body}</section>"


def table(
    headers: Sequence[str],
    rows: Iterable[str],
    *,
    tips: Sequence[str] | None = None,
    empty: str = "暂无数据",
    filterable: bool = True,
    table_id: str = "",
    extra_head: str = "",
) -> str:
    tips = list(tips or [])
    head_cells = "".join(
        f"<th{attr_tip(tips[index]) if index < len(tips) and tips[index] else ''}>"
        f"{esc(name)}</th>"
        for index, name in enumerate(headers)
    )
    body_rows = list(rows)
    if not body_rows:
        body_rows = [f"<tr><td colspan='{len(headers) + 1}' class='empty'>{esc(empty)}</td></tr>"]
    ident = f" id='{esc(table_id)}'" if table_id else ""
    toolbar = (
        f"<div class='table-tools'><input class='table-filter' data-filter='#{esc(table_id)}'"
        f"{attr_tip('输入关键词即时过滤当前表格（按 / 可快速聚焦）')} placeholder='筛选…'></div>"
        if filterable and table_id
        else ""
    )
    return (
        f"{toolbar}<div class='table-wrap'><table{ident}>"
        f"<thead><tr>{head_cells}{extra_head}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


def field(
    label: str,
    name: str,
    value: Any = "",
    *,
    tip_text: str = "",
    placeholder: str = "",
    type: str = "text",
    klass: str = "",
    readonly: bool = False,
) -> str:
    return (
        f"<label class='field {esc(klass)}'><span class='field-label'>{esc(label)}</span>"
        f"<input type='{esc(type)}' name='{esc(name)}' value='{esc(value)}'"
        f"{' placeholder=' + repr(placeholder) if placeholder else ''}"
        f"{' readonly' if readonly else ''}{attr_tip(tip_text)}></label>"
    )


def textarea(
    label: str,
    name: str,
    value: Any = "",
    *,
    rows: int = 4,
    tip_text: str = "",
    placeholder: str = "",
    mono: bool = False,
) -> str:
    return (
        f"<label class='field'><span class='field-label'>{esc(label)}</span>"
        f"<textarea name='{esc(name)}' rows='{rows}'"
        f"{' class=\"mono\"' if mono else ''}"
        f"{' placeholder=' + repr(placeholder) if placeholder else ''}"
        f"{attr_tip(tip_text)}>{esc(value)}</textarea></label>"
    )


def select(
    label: str,
    name: str,
    options: Sequence[tuple[str, str]],
    selected: Any = "",
    *,
    tip_text: str = "",
) -> str:
    opts = "".join(
        f"<option value='{esc(value)}'{' selected' if str(value) == str(selected) else ''}>"
        f"{esc(text)}</option>"
        for value, text in options
    )
    return (
        f"<label class='field'><span class='field-label'>{esc(label)}</span>"
        f"<select name='{esc(name)}'{attr_tip(tip_text)}>{opts}</select></label>"
    )


def switch(
    name: str,
    checked: bool,
    label: str,
    *,
    tip_text: str = "",
    value: str = "1",
) -> str:
    return (
        f"<label class='switch'{attr_tip(tip_text)}>"
        f"<input type='checkbox' name='{esc(name)}' value='{esc(value)}'"
        f"{' checked' if checked else ''}>"
        f"<span class='switch-track'><span class='switch-knob'></span></span>"
        f"<span class='switch-label'>{esc(label)}</span></label>"
    )


def button(
    label: str,
    *,
    variant: str = "primary",
    tip_text: str = "",
    type: str = "submit",
    small: bool = False,
) -> str:
    klass = f"btn btn-{esc(variant)}" + (" btn-sm" if small else "")
    return (
        f"<button class='{klass}' type='{esc(type)}'{attr_tip(tip_text)}>"
        f"{esc(label)}</button>"
    )


def form_button(
    action: str,
    label: str,
    *,
    fields: dict[str, Any] | None = None,
    method: str = "post",
    variant: str = "secondary",
    tip_text: str = "",
    confirm: str = "",
    small: bool = True,
) -> str:
    hidden = "".join(
        f"<input type='hidden' name='{esc(key)}' value='{esc(value)}'>"
        for key, value in (fields or {}).items()
    )
    klass = f"btn btn-{esc(variant)}" + (" btn-sm" if small else "")
    confirm_attr = f" data-confirm='{esc(confirm)}'" if confirm else ""
    return (
        f"<form class='inline' method='{esc(method)}' action='{esc(action)}'{confirm_attr}>"
        f"{hidden}<button class='{klass}' type='submit'{attr_tip(tip_text)}>"
        f"{esc(label)}</button></form>"
    )


def link_button(action: str, label: str, *, tip_text: str = "", small: bool = True) -> str:
    klass = "btn btn-ghost" + (" btn-sm" if small else "")
    return f"<a class='{klass}' href='{esc(action)}'{attr_tip(tip_text)}>{esc(label)}</a>"


def tabs(items: Sequence[tuple[str, str]], active: str) -> str:
    links = "".join(
        f"<a href='{esc(href)}' class='tab{' active' if href == active else ''}'>{esc(label)}</a>"
        for href, label in items
    )
    return f"<nav class='tabs'>{links}</nav>"


def flash(kind: str, text: str) -> str:
    if not text:
        return ""
    icon = {"ok": "✅", "warn": "⚠️", "error": "⛔", "info": "ℹ️"}.get(kind, "ℹ️")
    return f"<div class='flash flash-{esc(kind)}'>{icon} <span>{esc(text)}</span></div>"


def empty(text: str, *, icon: str = "🗂️") -> str:
    return f"<div class='empty'><span class='empty-icon'>{icon}</span>{esc(text)}</div>"


def json_block(payload: Any, *, tip_text: str = "", collapsed: bool = False) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2) if not isinstance(payload, str) else payload
    klass = "code-block collapsed" if collapsed else "code-block"
    return (
        f"<div class='{klass}'>"
        f"<button class='copy' type='button'{attr_tip('复制到剪贴板')}>复制</button>"
        f"{tip(tip_text) if tip_text else ''}<pre>{esc(body)}</pre></div>"
    )


# ----------------------------------------------------------------------- nav

#: (href, label, short icon, tooltip)
NAV: tuple[tuple[str, str, str, str], ...] = (
    ("/", "仪表盘", "◎", "总览：连接状态、当前模型、消息与 AI 指标"),
    ("/character", "角色", "☺", "人设、身份与当前状态（心情/精力/活动）"),
    ("/behavior", "行为", "◔", "回复节奏、分段、作息、主动性、群聊参与"),
    ("/conversation", "对话", "⇄", "v1.2 对话轮次与延续状态：Turn、过期响应、Open Loop、共同经历"),
    ("/sandbox", "沙盒", "🏠", "v2.0 生活沙盒：她此刻在哪个房间、做什么、小喵在干嘛、冰箱还剩什么"),
    ("/topics", "话题", "❝", "未聊完的话题与重要度管理"),
    ("/memory", "记忆", "✦", "长期记忆：搜索、时间线、健康度、修正"),
    ("/users", "用户", "☷", "用户档案、关系阶段、主动聊天开关"),
    ("/groups", "群组", "☰", "群资料与参与开关"),
    ("/sessions", "会话", "◫", "按会话隔离的上下文，可清空"),
    ("/models", "模型", "⌁", "模型列表、优先级、冷却与可用状态"),
    ("/config", "配置", "⚙", "在网页上编辑 provider / 模型 / 权限 / 日志等配置"),
    ("/prompts", "提示词", "✎", "系统提示词与人格相关内容"),
    ("/logs", "日志", "▤", "运行日志（含内心播报）"),
    ("/tools", "工具", "🔧", "工具运行时：启用、权限、限流、执行记录"),
    ("/agent", "Agent", "🧩", "多步任务：计划、步骤、观察、控制与指标"),
    ("/social", "社交", "🧠", "社交认知：她会先听，再判断自己有没有必要说话"),
    ("/stickers", "表情", "😺", "表情包库：收藏、识别、检索与自主使用（普通图片严格隔离）"),
    ("/credentials", "凭据", "🔑", "API Key 等敏感凭据（只加密存储，不显示明文）"),
    ("/runtime", "运行", "▶", "运行时开关、重载插件、数据库与健康检查"),
)

#: sections so the sidebar stays readable as pages accumulate
NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("概览", ("/", "/runtime", "/logs")),
    ("角色", ("/character", "/behavior", "/sandbox", "/conversation", "/topics")),
    ("数据", ("/memory", "/users", "/groups", "/sessions")),
    ("能力", ("/models", "/config", "/prompts", "/credentials", "/tools", "/agent", "/social", "/stickers")),
)


def nav_html(active: str) -> str:
    by_href = {href: (label, icon, tip_text) for href, label, icon, tip_text in NAV}
    groups: list[str] = []
    for title, hrefs in NAV_GROUPS:
        items = []
        for href in hrefs:
            label, icon, tip_text = by_href[href]
            current = href == active
            items.append(
                f"<a class='nav-item{' active' if current else ''}' href='{esc(href)}'"
                f"{attr_tip(L(tip_text))}><span class='nav-icon'>{icon}</span>"
                f"<span class='nav-label'>{esc(L(label))}</span></a>"
            )
        groups.append(
            f"<div class='nav-group'><span class='nav-group-title'>{esc(L(title))}</span>"
            + "".join(items)
            + "</div>"
        )
    return "".join(groups)


# ------------------------------------------------------------------ document


_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --font:"Quicksand","Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
         "PingFang SC","Microsoft YaHei",sans-serif;
  --mono:"JetBrains Mono","Cascadia Code",Consolas,"Courier New",monospace;
  --radius-sm:.5rem; --radius:.75rem; --radius-lg:1rem;
  --speed:.18s; --ease:cubic-bezier(.4,0,.2,1);
  --sidebar-w:236px;
  --bg:240 20% 98%; --surface:0 0% 100%; --surface-2:240 20% 96%;
  --border:240 6% 88%; --text:222 30% 12%; --muted:220 12% 46%; --faint:220 10% 62%;
  --shadow-sm:0 1px 2px rgb(16 24 40 / .06),0 1px 3px rgb(16 24 40 / .08);
  --shadow:0 2px 8px rgb(16 24 40 / .06),0 8px 24px rgb(16 24 40 / .08);
  --shadow-lg:0 8px 30px rgb(16 24 40 / .12),0 24px 60px rgb(16 24 40 / .12);
  --success:152 55% 42%; --warning:35 92% 48%; --danger:0 72% 52%; --info:200 85% 48%;
  --ok-soft:152 60% 94%; --warn-soft:38 95% 92%; --danger-soft:0 80% 95%; --info-soft:200 85% 94%;
}
html[data-theme="blue"]{--primary:217 91% 60%;--primary-fg:210 40% 98%;--primary-soft:217 90% 95%}
html[data-theme="sakura"]{--primary:340 82% 62%;--primary-fg:0 0% 100%;--primary-soft:340 85% 96%}
html[data-theme="matcha"]{--primary:152 55% 45%;--primary-fg:0 0% 100%;--primary-soft:152 55% 94%}
html[data-theme="violet"]{--primary:262 83% 62%;--primary-fg:0 0% 100%;--primary-soft:262 85% 96%}
html[data-theme="amber"]{--primary:32 95% 52%;--primary-fg:30 40% 10%;--primary-soft:35 95% 93%}
html[data-mode="dark"]{
  color-scheme:dark;
  --bg:222 22% 7%; --surface:222 20% 11%; --surface-2:222 18% 15%;
  --border:222 14% 22%; --text:210 30% 96%; --muted:215 16% 66%; --faint:215 12% 52%;
  --shadow-sm:0 1px 2px rgb(0 0 0 / .4); --shadow:0 2px 10px rgb(0 0 0 / .45);
  --shadow-lg:0 10px 40px rgb(0 0 0 / .55);
  --success:152 55% 52%; --warning:38 92% 58%; --danger:0 72% 62%; --info:200 85% 62%;
  --ok-soft:152 40% 16%; --warn-soft:38 45% 16%; --danger-soft:0 40% 18%; --info-soft:200 45% 17%;
}
html{background:hsl(var(--bg))}
body{margin:0;font-family:var(--font);background:hsl(var(--bg));color:hsl(var(--text));
  font-size:14.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
a{color:hsl(var(--primary));text-decoration:none;transition:opacity var(--speed) var(--ease)}
a:hover{opacity:.8}

/* ---------- shell ---------- */
.shell{display:flex;min-height:100vh}
.sidebar{width:var(--sidebar-w);flex:0 0 var(--sidebar-w);background:hsl(var(--surface));
  border-right:1px solid hsl(var(--border));padding:18px 12px;position:sticky;top:0;height:100vh;
  overflow-y:auto;transition:width var(--speed) var(--ease),flex-basis var(--speed) var(--ease)}
.brand{display:flex;align-items:center;gap:10px;padding:6px 10px 16px}
.brand-logo{width:34px;height:34px;border-radius:10px;flex:0 0 34px;
  background:linear-gradient(135deg,hsl(var(--primary)),hsl(var(--primary)/.55));
  display:flex;align-items:center;justify-content:center;color:hsl(var(--primary-fg));
  font-weight:700;box-shadow:0 6px 18px hsl(var(--primary)/.35)}
.brand-text{display:flex;flex-direction:column;min-width:0}
.brand-name{font-weight:700;letter-spacing:.3px}
.brand-sub{font-size:11px;color:hsl(var(--faint))}
.nav-group{margin:10px 0}
.nav-group-title{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:hsl(var(--faint));padding:6px 10px}
.nav-item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:var(--radius-sm);
  color:hsl(var(--text));margin:2px 0;position:relative;transition:background var(--speed) var(--ease),
  transform var(--speed) var(--ease),color var(--speed) var(--ease)}
.nav-item:hover{background:hsl(var(--surface-2));transform:translateX(2px);opacity:1}
.nav-item.active{background:hsl(var(--primary-soft));color:hsl(var(--primary));font-weight:600}
.nav-item.active::before{content:"";position:absolute;left:-12px;top:8px;bottom:8px;width:3px;
  border-radius:0 3px 3px 0;background:hsl(var(--primary))}
.nav-icon{width:18px;text-align:center;font-size:14px}
.sidebar-foot{padding:14px 10px 4px;color:hsl(var(--faint));font-size:11px;line-height:1.7}
body[data-nav="collapsed"] .sidebar{width:64px;flex-basis:64px}
body[data-nav="collapsed"] .nav-label,body[data-nav="collapsed"] .brand-text,
body[data-nav="collapsed"] .nav-group-title,body[data-nav="collapsed"] .sidebar-foot{display:none}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.topbar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;
  padding:12px 22px;background:hsl(var(--surface)/.85);backdrop-filter:blur(10px);
  border-bottom:1px solid hsl(var(--border))}
.topbar h1{font-size:16px;margin:0;font-weight:700}
.topbar .subtitle{font-size:12px;color:hsl(var(--faint));margin-left:2px}
.topbar-spacer{flex:1}
.content{padding:20px 22px 60px;max-width:1280px;width:100%;animation:rise .28s var(--ease)}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

/* ---------- controls ---------- */
.icon-btn{border:1px solid hsl(var(--border));background:hsl(var(--surface));color:hsl(var(--text));
  width:34px;height:34px;border-radius:var(--radius-sm);cursor:pointer;display:inline-flex;
  align-items:center;justify-content:center;transition:all var(--speed) var(--ease)}
.icon-btn:hover{background:hsl(var(--surface-2));transform:translateY(-1px)}
.btn{border:0;border-radius:var(--radius-sm);padding:8px 14px;cursor:pointer;font-size:13.5px;
  font-family:inherit;font-weight:600;display:inline-flex;align-items:center;gap:6px;
  transition:transform var(--speed) var(--ease),box-shadow var(--speed) var(--ease),
  background var(--speed) var(--ease),opacity var(--speed) var(--ease)}
.btn:hover{transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.btn:active{transform:translateY(0) scale(.985)}
.btn-primary{background:hsl(var(--primary));color:hsl(var(--primary-fg));
  box-shadow:0 4px 14px hsl(var(--primary)/.3)}
.btn-secondary{background:hsl(var(--surface-2));color:hsl(var(--text));border:1px solid hsl(var(--border))}
.btn-danger{background:hsl(var(--danger));color:#fff}
.btn-ghost{background:transparent;color:hsl(var(--primary));border:1px solid hsl(var(--border))}
.btn-sm{padding:4px 10px;font-size:12.5px}
input,select,textarea{font-family:inherit;font-size:13.5px;color:hsl(var(--text));
  background:hsl(var(--surface));border:1px solid hsl(var(--border));border-radius:var(--radius-sm);
  padding:8px 10px;width:100%;transition:border-color var(--speed) var(--ease),
  box-shadow var(--speed) var(--ease)}
input:focus,select:focus,textarea:focus{outline:0;border-color:hsl(var(--primary));
  box-shadow:0 0 0 3px hsl(var(--primary)/.18)}
textarea{min-height:70px;resize:vertical}
textarea.mono,pre{font-family:var(--mono)}
.field{display:block;margin:10px 0}
.field-label{display:flex;align-items:center;gap:6px;font-size:12px;color:hsl(var(--muted));
  margin-bottom:4px}
.switch{display:inline-flex;align-items:center;gap:8px;margin:6px 14px 6px 0;cursor:pointer;
  font-size:13px;user-select:none}
.switch input{position:absolute;opacity:0;width:0;height:0}
.switch-track{width:38px;height:21px;border-radius:999px;background:hsl(var(--border));
  position:relative;transition:background var(--speed) var(--ease);flex:0 0 38px}
.switch-knob{position:absolute;top:2px;left:2px;width:17px;height:17px;border-radius:50%;
  background:#fff;box-shadow:0 1px 3px rgb(0 0 0 / .3);transition:transform var(--speed) var(--ease)}
.switch input:checked + .switch-track{background:hsl(var(--primary))}
.switch input:checked + .switch-track .switch-knob{transform:translateX(17px)}

/* ---------- tooltips ---------- */
.tip{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
  border-radius:50%;background:hsl(var(--surface-2));color:hsl(var(--muted));font-size:10px;
  font-weight:700;cursor:help;position:relative;border:1px solid hsl(var(--border));
  transition:all var(--speed) var(--ease)}
.tip:hover,.tip:focus{background:hsl(var(--primary));color:hsl(var(--primary-fg));
  border-color:hsl(var(--primary))}
[data-tip]{cursor:help}
.tooltip{position:fixed;z-index:9999;background:hsl(222 25% 14%);color:#f3f6fb;font-size:12px;
  font-weight:500;line-height:1.5;padding:7px 11px;border-radius:var(--radius-sm);
  max-width:300px;text-align:left;white-space:normal;pointer-events:none;
  box-shadow:var(--shadow-lg);border:1px solid hsl(222 20% 26%);opacity:0;
  transform:translateY(3px);transition:opacity var(--speed) var(--ease),transform var(--speed) var(--ease)}
.tooltip.show{opacity:1;transform:none}

/* ---------- cards & data ---------- */
.card{background:hsl(var(--surface));border:1px solid hsl(var(--border));border-radius:var(--radius);
  padding:16px 18px;margin-bottom:16px;box-shadow:var(--shadow-sm);
  transition:box-shadow var(--speed) var(--ease),transform var(--speed) var(--ease)}
.card:hover{box-shadow:var(--shadow)}
.card-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.card-head h3{margin:0;font-size:14px;display:flex;align-items:center;gap:6px}
.card-actions{margin-left:auto;display:flex;gap:8px;align-items:center}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:16px}
.stat{background:hsl(var(--surface));border:1px solid hsl(var(--border));border-radius:var(--radius);
  padding:12px 14px;transition:transform var(--speed) var(--ease),box-shadow var(--speed) var(--ease)}
.stat:hover{transform:translateY(-2px);box-shadow:var(--shadow)}
.stat .label{font-size:11.5px;color:hsl(var(--muted));display:flex;align-items:center;gap:5px}
.stat b{display:block;font-size:21px;margin-top:4px;font-variant-numeric:tabular-nums}
.stat .hint{font-size:11px;color:hsl(var(--faint))}
.table-wrap{overflow-x:auto;border-radius:var(--radius);border:1px solid hsl(var(--border))}
table{border-collapse:collapse;width:100%;font-size:13px;background:hsl(var(--surface))}
th,td{padding:8px 12px;text-align:left;vertical-align:top;border-bottom:1px solid hsl(var(--border))}
th{background:hsl(var(--surface-2));font-size:12px;color:hsl(var(--muted));position:relative;
  white-space:nowrap}
tbody tr{transition:background var(--speed) var(--ease)}
tbody tr:hover{background:hsl(var(--surface-2)/.7)}
tbody tr:last-child td{border-bottom:0}
td .muted,span.muted{color:hsl(var(--muted))}
.table-tools{margin-bottom:8px}
.table-filter{max-width:260px}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:11.5px;
  font-weight:600;border:1px solid transparent}
.badge-success{background:hsl(var(--ok-soft));color:hsl(var(--success))}
.badge-warning{background:hsl(var(--warn-soft));color:hsl(var(--warning))}
.badge-danger{background:hsl(var(--danger-soft));color:hsl(var(--danger))}
.badge-info{background:hsl(var(--info-soft));color:hsl(var(--info))}
.badge-default{background:hsl(var(--surface-2));color:hsl(var(--muted));border-color:hsl(var(--border))}
.kv{display:grid;gap:6px}
.kv-row{display:flex;gap:12px;border-bottom:1px dashed hsl(var(--border));padding-bottom:5px}
.kv-key{color:hsl(var(--muted));min-width:150px;display:flex;align-items:center;gap:5px}
.kv-value{flex:1;word-break:break-word}
.progress{height:8px;border-radius:999px;background:hsl(var(--surface-2));overflow:hidden;
  width:180px;display:inline-block;vertical-align:middle;margin-right:8px}
.progress-bar{height:100%;background:hsl(var(--primary));border-radius:999px;
  transition:width .5s var(--ease)}
.tone-success{background:hsl(var(--success))} .tone-warning{background:hsl(var(--warning))}
.tone-danger{background:hsl(var(--danger))}
.code-block{position:relative;background:hsl(222 25% 11%);border-radius:var(--radius);padding:12px;
  border:1px solid hsl(222 16% 22%)}
.code-block pre{margin:0;color:#d7e2f1;font-size:12.5px;overflow:auto;max-height:420px}
.code-block .copy{position:absolute;right:8px;top:8px;background:hsl(222 20% 20%);color:#cbd5e1;
  border:1px solid hsl(222 16% 30%);border-radius:var(--radius-sm);padding:2px 8px;cursor:pointer;
  font-size:11px;opacity:0;transition:opacity var(--speed) var(--ease)}
.code-block:hover .copy{opacity:1}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px;padding:4px;border-radius:var(--radius);
  background:hsl(var(--surface-2)/.6);border:1px solid hsl(var(--border))}
.tab{padding:6px 12px;border-radius:calc(var(--radius) - 4px);color:hsl(var(--muted));font-size:13px;
  transition:all var(--speed) var(--ease)}
.tab:hover{background:hsl(var(--surface));opacity:1}
.tab.active{background:hsl(var(--surface));color:hsl(var(--primary));font-weight:600;
  box-shadow:var(--shadow-sm)}
.flash{display:flex;align-items:center;gap:8px;padding:10px 14px;border-radius:var(--radius);
  margin-bottom:14px;border:1px solid hsl(var(--border));background:hsl(var(--surface));
  animation:rise .25s var(--ease)}
.flash-ok{border-color:hsl(var(--success)/.45);background:hsl(var(--ok-soft))}
.flash-warn{border-color:hsl(var(--warning)/.45);background:hsl(var(--warn-soft))}
.flash-error{border-color:hsl(var(--danger)/.45);background:hsl(var(--danger-soft))}
.empty{padding:26px;text-align:center;color:hsl(var(--faint));font-size:13px}
.empty-icon{display:block;font-size:22px;margin-bottom:6px}
form.inline{display:inline}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.section-title{font-size:13px;font-weight:700;color:hsl(var(--muted));margin:22px 0 10px;
  text-transform:uppercase;letter-spacing:.08em}
.hint{font-size:12px;color:hsl(var(--faint));margin:6px 0}
.hint-inline{font-size:12px;color:hsl(var(--faint));margin-left:8px}
.toast-wrap{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:99}
.toast{background:hsl(222 25% 14%);color:#f1f5f9;padding:10px 14px;border-radius:var(--radius);
  box-shadow:var(--shadow-lg);font-size:13px;animation:rise .22s var(--ease)}
@media (max-width:900px){
  .sidebar{position:fixed;z-index:40;transform:translateX(-100%)}
  body[data-nav="open"] .sidebar{transform:none}
  .content{padding:16px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

_JS = """
(function(){
  const root=document.documentElement;
  const setCookie=(k,v)=>document.cookie=`${k}=${v};path=/;max-age=31536000;samesite=lax`;
  function apply(mode,theme){
    if(mode) root.dataset.mode=mode;
    if(theme) root.dataset.theme=theme;
  }
  function current(){return {mode:root.dataset.mode||'light',theme:root.dataset.theme||'blue'}}
  window.cbSetMode=function(mode){const c=current();apply(mode,c.theme);setCookie('catoobot_mode',mode);
    const el=document.getElementById('mode-btn'); if(el) el.textContent=mode==='dark'?'🌙':'☀️';
    toast(mode==='dark'?'已切换到夜间模式':'已切换到日间模式');};
  window.cbSetTheme=function(theme){const c=current();apply(c.mode,theme);setCookie('catoobot_theme',theme);
    toast('主题：'+theme);};
  window.cbToggleNav=function(){const collapsed=document.body.dataset.nav==='collapsed';
    document.body.dataset.nav=collapsed?'open':'collapsed';
    setCookie('catoobot_nav',collapsed?'open':'collapsed');};
  window.cbToggleMode=function(){const c=current();window.cbSetMode(c.mode==='dark'?'light':'dark');};
  function toast(text){
    let wrap=document.querySelector('.toast-wrap');
    if(!wrap){wrap=document.createElement('div');wrap.className='toast-wrap';document.body.appendChild(wrap);}
    const node=document.createElement('div');node.className='toast';node.textContent=text;
    wrap.appendChild(node);setTimeout(()=>{node.style.opacity='0';setTimeout(()=>node.remove(),250);},1800);
  }
  window.cbToast=toast;
  window.cbCopy=function(btn){
    const pre=btn.parentElement.querySelector('pre'); if(!pre) return;
    navigator.clipboard.writeText(pre.innerText).then(()=>{btn.textContent='已复制';
      setTimeout(()=>btn.textContent='复制',1200);});
  };
  document.addEventListener('click',function(ev){
    const copy=ev.target.closest('.copy'); if(copy){window.cbCopy(copy);}
    const form=ev.target.closest('form[data-confirm]');
    if(form && !form.dataset.confirmed){
      if(!window.confirm(form.dataset.confirm)){ev.preventDefault();}
      else{form.dataset.confirmed='1';}
    }
  },true);
  // fixed-position tooltip (never clipped by overflow:hidden/auto containers)
  let tipEl=null;
  function tip(){if(!tipEl){tipEl=document.createElement('div');tipEl.className='tooltip';
    document.body.appendChild(tipEl);}return tipEl;}
  function showTip(src){
    const el=tip(); el.textContent=src.getAttribute('data-tip'); el.classList.add('show');
    const r=src.getBoundingClientRect();
    el.style.left=Math.max(8,r.left)+'px';
    el.style.top=(r.bottom+8)+'px';
    const box=el.getBoundingClientRect();
    if(box.right>innerWidth-8){el.style.left=Math.max(8,innerWidth-box.width-8)+'px';}
    if(box.bottom>innerHeight-8){el.style.top=Math.max(8,r.top-box.height-8)+'px';}
  }
  function hideTip(){const el=tip();el.classList.remove('show');}
  document.addEventListener('mouseover',function(ev){
    const src=ev.target.closest('[data-tip]'); if(src){showTip(src);}
  });
  document.addEventListener('mouseout',function(ev){
    const src=ev.target.closest('[data-tip]'); if(src){hideTip();}
  });
  document.addEventListener('focusin',function(ev){
    const src=ev.target.closest('[data-tip]'); if(src){showTip(src);}
  });
  document.addEventListener('focusout',function(){hideTip();});
  document.addEventListener('scroll',hideTip,true);

  window.cbSetLang=function(lang){setCookie('catoobot_lang',lang);location.reload();};

  document.addEventListener('input',function(ev){
    const input=ev.target.closest('.table-filter'); if(!input) return;
    const table=document.querySelector(input.dataset.filter); if(!table) return;
    const needle=input.value.trim().toLowerCase();
    table.querySelectorAll('tbody tr').forEach(function(row){
      row.style.display=!needle||row.innerText.toLowerCase().includes(needle)?'':'none';
    });
  });
  document.addEventListener('keydown',function(ev){
    if(ev.key==='/'&&!/input|textarea|select/i.test(document.activeElement.tagName)){
      const input=document.querySelector('.table-filter'); if(input){ev.preventDefault();input.focus();}
    }
  });
})();
"""


def page(
    title: str,
    active: str,
    body: str,
    *,
    subtitle: str = "",
    actions: str = "",
    prefs: dict[str, str] | None = None,
    minimal: bool = False,
) -> str:
    """Render a full page: themed shell + top bar + content.

    ``minimal=True`` (login screen) keeps the theme and the mode toggle but
    drops the navigation, so an unauthenticated visitor sees no dead links.
    """
    prefs = prefs or current_preferences()
    mode = normalize_mode(prefs.get("mode"))
    theme = normalize_theme(prefs.get("theme"))
    nav_state = prefs.get("nav", "open")
    from app.web.i18n import set_lang as _set_lang

    _set_lang(prefs.get("lang", "zh"))
    mode_icon = "🌙" if mode == "dark" else "☀️"
    lang = current_lang()
    if lang == "en":
        lang_button = (
            "<button class='icon-btn' type='button' onclick=\"cbSetLang('zh')\""
            " data-tip='Switch to Chinese / 切换到中文'>中</button>"
        )
    else:
        lang_button = (
            "<button class='icon-btn' type='button' onclick=\"cbSetLang('en')\""
            " data-tip='Switch to English / 切换到英文'>EN</button>"
        )
    theme_swatches = "".join(
        f"<button class='icon-btn' type='button' data-tip='切换到「{esc(label)}」主题'"
        f" style='width:22px;height:22px;border-radius:50%;background:hsl({primary})'"
        f" onclick=\"cbSetTheme('{key}')\"></button>"
        for key, (label, primary, _fg) in THEMES.items()
    )
    if minimal:
        return f"""<!DOCTYPE html>
<html lang="zh-CN" data-mode="{esc(mode)}" data-theme="{esc(theme)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · CatooBot Admin</title>
<style>{_CSS}</style>
</head>
<body data-nav="open">
<div class="main" style="max-width:1200px;margin:0 auto">
  <header class="topbar">
    <span class="brand-logo">🍥</span>
    <div><h1>CatooBot</h1><div class="subtitle">{VERSION} · by {esc(AUTHOR)}</div></div>
    <div class="topbar-spacer"></div>
    <div style="display:flex;gap:6px;align-items:center">{theme_swatches}</div>
    {lang_button}
    <button class="icon-btn" id="mode-btn" type="button" onclick="cbToggleMode()"
      data-tip="{esc(L('切换日间 / 夜间模式'))}">{mode_icon}</button>
  </header>
  <main class="content">{body}</main>
</div>
<script>{_JS}</script>
</body></html>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-mode="{esc(mode)}" data-theme="{esc(theme)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · CatooBot Admin</title>
<style>{_CSS}</style>
</head>
<body data-nav="{esc(nav_state)}">
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-logo">🍥</span>
      <span class="brand-text">
        <span class="brand-name">CatooBot</span>
        <span class="brand-sub">{VERSION} · by {esc(AUTHOR)}</span>
      </span>
    </div>
    {nav_html(active)}
    <div class="sidebar-foot">
      {esc(L('开发者'))} {esc(AUTHOR)}<br>
      {esc(L('QQ 端零指令 · 一切管理在此'))}
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <button class="icon-btn" type="button" onclick="cbToggleNav()"
        data-tip="折叠 / 展开左侧导航">☰</button>
      <div>
        <h1>{esc(title)}</h1>
        {f'<div class="subtitle">{subtitle}</div>' if subtitle else ''}
      </div>
      <div class="topbar-spacer"></div>
      <div style="display:flex;gap:6px;align-items:center">{theme_swatches}</div>
      {lang_button}
      <button class="icon-btn" id="mode-btn" type="button" onclick="cbToggleMode()"
        data-tip="{esc(L('切换日间 / 夜间模式'))}">{mode_icon}</button>
      {actions}
      <form method="post" action="/logout" class="inline">
        <button class="btn btn-secondary btn-sm" type="submit"
          data-tip="{esc(L('退出登录'))}">{esc(L('退出'))}</button>
      </form>
    </header>
    <main class="content">{body}</main>
  </div>
</div>
<script>{_JS}</script>
</body></html>"""
