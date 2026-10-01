"""The dashboard: connection, character and sandbox state at a glance (Task 18).

Moved verbatim out of server.py; the route snapshot keeps the served
surface identical.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web import ui
from app.web.routes.base import WebContext, esc, layout


class DashboardRoutes(WebContext):
    """Handlers for this domain."""

    async def _dashboard(self, request: web.Request) -> web.Response:
        data = await self._admin.dashboard()
        metrics = data["metrics"]
        sandbox = getattr(self._bot, "sandbox", None)
        character = getattr(self._bot, "character", None)

        online = bool(data["online"])
        stats = [
            ("NapCat 连接", "在线" if online else "离线", ""),
            ("机器人 QQ", data["self_id"] or "-", ""),
            (
                "当前模型",
                data["current_model"] or "-",
                "最近一次成功调用使用的模型；限流时会自动切到备用模型",
            ),
            ("会话数", data["sessions"], ""),
            ("用户数", data["users"], ""),
            ("群数", data["groups"], ""),
            ("记忆条数", data["memories"], "含已被取代、仍可审计的旧事实"),
            ("收到消息", metrics["messages_received"], ""),
            ("AI 请求", metrics["ai_requests"], "每一次模型调用（含故障转移时的重试）"),
            ("限流 429", metrics["rate_limited"], "被服务商限流时自动故障转移到备用模型"),
            ("AI 失败", metrics["ai_errors"], "所有模型都没能答出来的请求数"),
            ("新增记忆", metrics["memories_extracted"], "从对话里抽取并落库的记忆条数"),
            ("运行时长", f"{metrics['uptime_seconds'] // 60} 分钟", ""),
        ]
        cards = ui.stats_grid(stats)

        state_bits: list[tuple[str, Any, str]] = []
        if character is not None:
            state = await self._admin.character_state()
            state_bits = [
                ("角色", state.get("mood") or "-", "她当前的心情阶梯（down→cheerful）"),
                (
                    "精力",
                    f"{float(state.get('energy') or 0):.0%}",
                    "0~100%，随活动与睡眠变化，太低时回复会更短",
                ),
                ("正在做", state.get("activity") or "发呆", "来自生活沙盒：她此刻在做的事"),
            ]
        if sandbox is not None and sandbox.enabled:
            try:
                context = sandbox.context()
                state_bits.append(("地点", context.get("location", "-"), "她此刻所在的空间"))
                state_bits.append(
                    ("模式", " + ".join(context.get("modes", [])) or "-", "可叠加的人格模式")
                )
                state_bits.append(("小喵", context.get("pet", "-"), "她的猫此刻的状态"))
            except Exception:  # noqa: BLE001 - dashboard must never fail
                pass
        character_card = (
            ui.card(
                "角色此刻",
                ui.stats_grid(state_bits)
                + "<p class='hint'>想看更细的生活与时间线，去 <a href='/sandbox'>沙盒</a> 页面；"
                "控制台里也会实时播报（logging.narrate）。</p>",
                tip_text="她的状态是叙事状态（演出来的），不是真实心理测量",
            )
            if state_bits
            else ""
        )

        model_rows = "".join(
            f"<tr><td>{esc(m['name'])}</td><td>{esc(m['provider'])}</td><td>{esc(m['model'])}</td>"
            f"<td>{ui.badge('启用', 'success') if m['enabled'] else ui.badge('停用', 'default')}</td>"
            f"<td>{ui.badge('冷却中', 'warning') if m['in_cooldown'] else '—'}</td>"
            f"<td>{m['failure_count']}</td>"
            f"<td class='muted'>{esc(m['last_error'] or '')}</td></tr>"
            for m in data["models"]
        )
        model_card = ui.card(
            "模型与故障转移",
            ui.table(
                ["名称", "Provider", "模型 ID", "状态", "冷却", "失败", "最近错误"],
                model_rows,
                tips=[
                    "配置里的模型别名",
                    "使用哪个服务商",
                    "服务商真实的模型 ID",
                    "是否参与调度",
                    "被限流/报错后会短暂冷却",
                    "本次运行累计失败次数",
                    "最后一次失败原因（已脱敏）",
                ],
                empty="还没有配置模型，去「配置 → AI 模型」添加",
                table_id="dash-models",
            )
            + "<p class='hint'>列表顺序即优先级；在 <a href='/models'>模型</a> 页可即时启停与调整顺序。</p>",
            tip_text="主模型被限流时会自动切到下一个可用模型",
        )

        quick = ui.card(
            "常见操作",
            "<div class='toolbar'>"
            + ui.link_button(
                "/config", "改配置（Provider/模型/权限）", tip_text="不用再手工编辑 YAML"
            )
            + ui.link_button(
                "/memory/correction", "修正一条记忆", tip_text="用一句人话让她记住新的说法"
            )
            + ui.link_button(
                "/sandbox",
                "看看她现在在干嘛",
                tip_text="生活沙盒：她在哪个房间、正在做什么、小喵在干嘛",
            )
            + ui.link_button(
                "/sandbox/chat",
                "调回复节奏与主动性",
                tip_text="私聊回复、群聊参与、主动回复（已并入沙盒页）",
            )
            + ui.link_button("/tools", "管理工具", tip_text="启用/限流/权限与执行记录")
            + ui.link_button("/runtime", "运行时与插件", tip_text="重载插件、健康检查")
            + "</div>",
            tip_text="常用的入口都放在这里，省得在左侧找",
        )

        ai_notice = ""
        ai = getattr(self._bot, "ai", None)
        if self._bot.config.ai.enabled and (ai is None or not ai.enabled):
            ai_notice = ui.flash(
                "error",
                "AI 已启用，但没有任何可用模型——聊天、记忆抽取与视觉识别都会直接跳过。"
                "请在「配置 → AI 模型」检查 Provider 的 API Key 是否已配置、模型是否绑定到正确的 Provider。",
            )
        body = ai_notice + cards + quick + character_card + model_card
        return web.Response(
            text=layout(
                "仪表盘",
                "/",
                body,
                subtitle=f"{self._bot.name} · {'在线' if online else '等待 NapCat 连接'}",
            ),
            content_type="text/html",
        )

    def register_dashboard(self, app: web.Application) -> None:
        app.router.add_get("/", self._dashboard)
