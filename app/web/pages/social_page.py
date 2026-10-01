"""The ``/social`` pages: dashboard, observations, group context, simulator.

Server-rendered from :mod:`app.web.ui`. "Analyze Now" and "Replay" are read-only
simulations — they never send a QQ message (§88/§90).
"""

from __future__ import annotations

from typing import Any

from app.web import ui

DECISION_LABEL = {
    "reply": ("回复", "success"),
    "observe": ("观察", "info"),
    "ignore": ("忽略", "default"),
    "defer": ("待定", "warning"),
}


def _tabs(active: str) -> str:
    return ui.tabs(
        [
            ("/social", "概览"),
            ("/social/observations", "观察记录"),
            ("/social/group", "群上下文"),
            ("/social/simulator", "手动分析"),
            ("/social/policy", "策略"),
        ],
        active,
    )


def _decision_badge(decision: str) -> str:
    label, tone = DECISION_LABEL.get(decision, (decision, "default"))
    return ui.badge(label, tone)


def dashboard(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card(
            "社交认知未启用",
            "<p class='muted'>在 config 或「策略」页打开 <code>social.enabled</code>。</p>",
        )
    stats = data["stats"]
    tiles = ui.stats_grid(
        [
            ("已观察群", data["group_count"], "有群聊缓冲区、被观察过的群"),
            ("观察次数", stats.get("total", 0), "5 消息观察器触发的次数"),
            ("决定回复", stats.get("replies", 0), ""),
            ("决定忽略", stats.get("ignores", 0), ""),
            ("决定待定", stats.get("defers", 0), ""),
            ("参与率", f"{stats.get('reply_rate', 0):.0%}", "回复 / 观察总次数"),
        ]
    )
    threads = data.get("active_threads", [])
    thread_rows = (
        "".join(
            f"<tr><td>{esc(t['group_id'])}</td><td>{esc(t['topic'] or '-')}</td>"
            f"<td>{esc('、'.join(t['participants']))}</td>"
            f"<td>{esc(t['last_bot_message'][:40])}</td></tr>"
            for t in threads
        )
        or '<tr><td colspan="4" class="muted">当前没有活跃对话线程</td></tr>'
    )
    thread_card = ui.table(
        ["群", "话题", "参与者", "她上一句"],
        thread_rows,
        tips=["群号", "线程话题", "参与的人", "她最近一次发言"],
        empty="当前没有活跃线程",
        table_id="social-threads",
        filterable=False,
    )
    groups = data.get("groups", [])
    group_links = (
        "".join(
            f"<a class='btn btn-ghost btn-sm' href='/social/group?group={esc(g)}'>{esc(g)}</a> "
            for g in groups[:12]
        )
        or '<span class="muted">还没有群聊记录</span>'
    )

    return (
        tiles
        + ui.card(
            "活跃对话线程",
            thread_card,
            tip_text="她刚回复过、正在延续的对话（10 分钟窗口）",
        )
        + ui.card(
            "已被观察的群",
            f"<p>{group_links}</p>"
            + "<p class='hint'>点某个群查看实时上下文；或去「手动分析」立即跑一次观察器。</p>",
            tip_text="只要群里来过消息，就会出现在这里",
        )
    )


def observations_page(rows: list[dict[str, Any]], group_id: str) -> str:
    body_rows = (
        "".join(
            f"<tr><td class='muted'>{esc(ts(row['created_at']))}</td>"
            f"<td>{esc(row['group_id'])}</td><td>{_decision_badge(row['decision'])}</td>"
            f"<td>{esc(row['reason_code'])}</td><td>{row['confidence']:.2f}</td>"
            f"<td>{esc(row['topic'] or '-')}</td>"
            f"<td>{esc(row['response_goal'] or '-')}</td></tr>"
            for row in rows
        )
        or '<tr><td colspan="7" class="muted">还没有观察记录</td></tr>'
    )
    table = ui.table(
        ["时间", "群", "决定", "原因", "置信度", "话题", "回应目标"],
        body_rows,
        tips=[
            "触发时间",
            "群号",
            "reply/observe/ignore/defer",
            "原因代码",
            "结构化置信度（不是概率）",
            "当时话题",
            "简短回应目标",
        ],
        empty="还没有观察记录",
        table_id="social-observations",
    )
    form = (
        "<form method='get' action='/social/observations'>"
        + ui.field("按群过滤", "group", group_id, tip_text="留空查看全部")
        + "<p><button class='btn btn-secondary' type='submit'>筛选</button></p></form>"
    )
    return ui.card("观察记录", form + table, tip_text="结构化决策日志，用于调试（不保存隐私聊天）")


def group_page(snapshot: dict[str, Any] | None, group_id: str) -> str:
    form = (
        "<form method='get' action='/social/group'>"
        + ui.field("群号", "group", group_id, tip_text="例如 987654")
        + "<p><button class='btn btn-secondary' type='submit'>查看</button></p></form>"
    )
    if snapshot is None:
        return ui.card("群上下文", form + "<p class='muted'>输入群号查看实时状态</p>")

    monitor = snapshot.get("monitor", {})
    thread = snapshot.get("thread")
    attention = snapshot.get("attention", {})
    policy = snapshot.get("policy", {})
    recent = monitor.get("recent_messages", [])
    recent_rows = (
        "".join(
            f"<tr><td class='muted'>{'我' if m['is_bot'] else esc(m['nickname'])}</td>"
            f"<td>{esc(m['content'])}</td></tr>"
            for m in recent
        )
        or '<tr><td colspan="2" class="muted">无</td></tr>'
    )

    kv = ui.kv(
        [
            ("未观察消息", f"{monitor.get('unobserved', 0)} / 5"),
            ("当前话题", attention.get("current_topic") or "-"),
            ("注意程度", f"{float(attention.get('attention_level', 0)):.2f}"),
            ("疲劳度", f"{float(attention.get('fatigue', 0)):.2f}"),
            ("活跃线程", "是" if thread else "否"),
            ("她上一句", thread.get("last_bot_message", "-") if thread else "-"),
            ("今日已参与", f"{policy.get('daily_count', 0)} / {policy.get('daily_limit', 0)}"),
        ],
        tips={
            "未观察消息": "攒够 5 条外部消息就触发一次观察器",
            "注意程度": "她对当前群的关注（叙事建模，不是用户心理）",
            "疲劳度": "连续参与会升高，闲置会缓慢恢复",
        },
    )
    return (
        ui.card("群上下文", form)
        + ui.card(f"实时状态 · {esc(group_id)}", kv)
        + ui.card(
            "最近消息",
            ui.table(
                ["发言者", "内容"],
                recent_rows,
                empty="无",
                table_id="social-group-msgs",
                filterable=False,
            ),
        )
    )


def simulator_page(result: dict[str, Any] | None, group_id: str) -> str:
    form = (
        "<form method='post' action='/social/analyze'>"
        + ui.field("群号", "group", group_id)
        + "<p><button class='btn btn-primary' type='submit' "
        + ui.attr_tip("对当前缓冲区立即跑一次观察器（不发送 QQ）")
        + ">Analyze Now</button></p></form>"
    )
    if result is None:
        return ui.card("手动分析", form + "<p class='muted'>不发送 QQ，只看她会怎么判断</p>")
    if result.get("error"):
        return ui.card("手动分析", form + ui.flash("warn", result["error"]))
    decision = result["decision"]
    batch = "；".join(result.get("batch", []))
    scores = decision.get("scores", {})
    kv = ui.kv(
        [
            ("决定", _decision_badge(decision.get("decision", ""))),
            ("原因", decision.get("reason_code", "-")),
            ("置信度", f"{float(decision.get('confidence', 0)):.2f}"),
            ("话题", decision.get("topic") or "-"),
            ("回应目标", decision.get("response_goal") or "-"),
            ("话题相关", f"{scores.get('topic_relevance', 0):.2f}"),
            ("角色相关", f"{scores.get('character_relevance', 0):.2f}"),
            ("社交契合", f"{scores.get('social_fit', 0):.2f}"),
            ("贡献价值", f"{scores.get('contribution_value', 0):.2f}"),
        ]
    )
    return ui.card("手动分析", form + kv + ui.card("分析的批量消息", f"<p>{esc(batch)}</p>"))


def policy_page(config: dict[str, Any], msg: str = "") -> str:
    continuation = config.get("continuation", {})
    observer = config.get("observer", {})
    participation = config.get("participation", {})
    thresholds = config.get("thresholds", {})
    attention = config.get("attention", {})
    fatigue = config.get("fatigue", {})
    topic = config.get("topic", {})

    form = (
        "<form method='post' action='/social/policy'>"
        + '<div class="section-title">总开关</div>'
        + ui.switch(
            "social_enabled",
            config.get("enabled", True),
            "启用社交认知",
            tip_text="关闭后回退到旧的概率参与逻辑",
        )
        + ui.field(
            "决策模型",
            "decision_model",
            config.get("decision_model", ""),
            tip_text="ai.models 里的别名；留空用路由默认（快速）模型",
        )
        + '<div class="section-title">对话延续</div>'
        + "<div class='grid'>"
        + ui.switch(
            "continuation_enabled",
            continuation.get("enabled", True),
            "启用无 @ 追问识别",
            tip_text="识别「看的什么剧？」这类继续对话，优先级高于观察器",
        )
        + ui.field("窗口（分钟）", "window_minutes", continuation.get("window_minutes", 10))
        + ui.field("窗口最大消息", "continuation_max_messages", continuation.get("max_messages", 8))
        + "</div>"
        + '<div class="section-title">观察器</div>'
        + "<div class='grid'>"
        + ui.field(
            "批量大小",
            "batch_size",
            observer.get("batch_size", 5),
            tip_text="每几条外部消息触发一次观察器",
        )
        + ui.field("上下文条数", "min_context", observer.get("min_context_messages", 20))
        + ui.field("过期阈值", "max_staleness", observer.get("max_staleness_messages", 5))
        + "</div>"
        + '<div class="section-title">参与限制</div>'
        + "<div class='grid'>"
        + ui.field("每日上限", "daily_limit", participation.get("daily_limit", 30))
        + ui.field("冷却（秒）", "cooldown_seconds", participation.get("cooldown_seconds", 90))
        + "</div>"
        + '<div class="section-title">决策阈值（分数不是概率）</div>'
        + "<div class='grid'>"
        + ui.field("追问阈值", "follow_up", thresholds.get("follow_up", 0.75))
        + ui.field("话题相关", "topic_relevance", thresholds.get("topic_relevance", 0.70))
        + ui.field("社交契合", "social_fit", thresholds.get("social_fit", 0.65))
        + ui.field("贡献价值", "contribution_value", thresholds.get("contribution_value", 0.65))
        + "</div>"
        + '<div class="section-title">行为</div>'
        + ui.switch("attention_enabled", attention.get("enabled", True), "社交注意", tip_text="")
        + ui.switch(
            "fatigue_enabled",
            fatigue.get("enabled", True),
            "社交疲劳",
            tip_text="只软化判断，不强制沉默",
        )
        + ui.switch("topic_enabled", topic.get("enabled", True), "话题感知", tip_text="")
        + "<p><button class='btn btn-primary' type='submit'>保存并热加载</button></p></form>"
    )
    return (ui.flash("ok", msg) if msg else "") + ui.card("社交认知策略", form)


def esc(value: Any) -> str:
    return ui.esc(value)


def ts(value: Any) -> str:
    import time

    try:
        return time.strftime("%m-%d %H:%M", time.localtime(int(value or 0)))
    except (TypeError, ValueError, OSError):
        return "-"
