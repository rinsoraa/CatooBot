"""The ``/conversation`` pages: turn runtime dashboard + continuity inspector.

Server-rendered from :mod:`app.web.ui`. Read-only observation surfaces for
v1.2 — the operator can see turns, decisions, sequences and continuity, but
QQ never sees any of it (v1.2 §131-§138).
"""

from __future__ import annotations

from typing import Any

from app.web import ui

STATUS_TONE = {
    "delivered": "success",
    "silent": "default",
    "stale": "warning",
    "cancelled": "warning",
    "generating": "info",
    "pending": "info",
    "error": "danger",
    "open": "info",
}


def _tabs(active: str) -> str:
    return ui.tabs(
        [
            ("/conversation", "概览"),
            ("/conversation/continuity", "延续状态"),
        ],
        active,
    )


def dashboard(data: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    sessions = data.get("sessions", [])
    session_rows = (
        "".join(
            f"<tr><td>{ui.esc(s['session_id'])}</td>"
            f"<td>{'群聊' if s['is_group'] else '私聊'}</td>"
            f"<td>{s['buffered_messages']}</td>"
            f"<td>{s['queued_turns']}</td>"
            f"<td>{'#' + str(s['active_generation']) if s['active_generation'] else '-'}</td>"
            f"<td>{
                ui.badge(
                    '已过期' if s['generation_stale'] else '正常',
                    'warning' if s['generation_stale'] else 'success',
                )
            }</td>"
            f"<td>{s['momentum']:.2f}</td>"
            f"<td>{s['consecutive_questions']}</td></tr>"
            for s in sessions
        )
        or "<tr><td colspan='8' class='muted'>暂无活跃会话</td></tr>"
    )

    turn_rows = (
        "".join(
            f"<tr><td class='mono'>{ui.esc(t['turn_id'])}</td>"
            f"<td>{ui.esc(t['session_id'])}</td>"
            f"<td>{ui.esc(t['classification'])}</td>"
            f"<td>{ui.badge(t['status'], STATUS_TONE.get(t['status'], 'default'))}</td>"
            f"<td>{ui.esc((t['text'] or '')[:60])}</td>"
            f"<td>{ui.esc(t['silence_reason'] or '-')}</td>"
            f"<td class='mono'>#{t['generation_id'] or '-'}</td></tr>"
            for t in turns
        )
        or "<tr><td colspan='7' class='muted'>还没有已完成的对话轮次</td></tr>"
    )

    return (
        ui.stats_grid(
            [
                ("活跃会话", data.get("session_count", 0), "有缓冲或生成中的会话"),
                ("缓冲中消息", data.get("buffered", 0), "等待 debounce 合并成 Turn"),
                ("生成中", data.get("generating", 0), "同一会话同时最多一个"),
                ("过期响应", data.get("stale_total", 0), "被用户改口作废、未发送"),
            ]
        )
        + ui.card(
            "会话状态",
            f"<table class='data'><tr><th>会话</th><th>类型</th><th>缓冲</th>"
            f"<th>队列</th><th>生成</th><th>状态</th><th>动量</th><th>连续追问</th></tr>"
            f"{session_rows}</table>",
        )
        + ui.card(
            "最近对话轮次",
            f"<table class='data'><tr><th>Turn</th><th>会话</th><th>分类</th>"
            f"<th>状态</th><th>用户侧内容</th><th>沉默原因</th><th>Generation</th></tr>"
            f"{turn_rows}</table>",
        )
    )


def continuity_page(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card(
            "角色延续未启用",
            "<p class='muted'>在 config 打开 <code>continuity.enabled</code>。</p>",
        )
    state = data["state"]

    def field(label: str, value: str, note: str = "") -> str:
        shown = ui.esc(value) if value else "<span class='muted'>（空）</span>"
        tip = f"<div class='muted small'>{ui.esc(note)}</div>" if note else ""
        return f"<div class='field-row'><strong>{ui.esc(label)}</strong>{shown}{tip}</div>"

    loops = data.get("open_loops", [])
    loop_rows = (
        "".join(
            f"<tr><td>{ui.esc(loop['summary'])}</td><td>{ui.esc(loop['type'])}</td>"
            f"<td>{ui.badge(loop['status'], 'success' if loop['status'] == 'open' else 'default')}</td>"
            f"<td>{loop['progress']:.0%}</td><td>{ui.esc(loop['source'] or '-')}</td>"
            f"<td>{loop['confidence']:.2f}</td></tr>"
            for loop in loops
        )
        or "<tr><td colspan='6' class='muted'>暂无未完成的事</td></tr>"
    )

    shared = data.get("shared_experiences", [])
    shared_rows = (
        "".join(
            f"<tr><td>{ui.esc(exp['summary'])}</td><td>{ui.esc(exp['type'])}</td>"
            f"<td>{ui.esc('、'.join(exp['keywords']))}</td><td>{exp['times_referenced']}</td>"
            f"<td>{exp['confidence']:.2f}</td></tr>"
            for exp in shared
        )
        or "<tr><td colspan='5' class='muted'>暂无共同经历记录</td></tr>"
    )

    affect = state.get("affect", {})
    dimensions = affect.get("dimensions", {})
    affect_bits = (
        "、".join(f"{name} {level:.2f}" for name, level in dimensions.items()) or "（平静）"
    )

    profiles = data.get("profiles", [])
    profile_rows = (
        "".join(
            "<tr><td class='mono'>{}</td><td>{}</td><td>{}</td></tr>".format(
                ui.esc(p["user_id"]),
                "、".join(
                    f"{key}={pat['value']:.2f}(±{pat['confidence']:.2f},n={pat['sample_count']})"
                    for key, pat in p["patterns"].items()
                )
                or "<span class='muted'>观察中</span>",
                f"<span class='mono'>{p['updated_at'] or '-'}</span>",
            )
            for p in profiles
        )
        or "<tr><td colspan='3' class='muted'>暂无用户画像样本</td></tr>"
    )

    return (
        ui.card(
            "她此刻的延续状态",
            field("最近关注", state.get("current_interest", ""), "小时级 TTL，自动衰减")
            + field("未说完的想法", state.get("unfinished_thought", ""))
            + field("近期情绪", state.get("recent_emotion", ""), "分钟级 TTL")
            + field("上一句说过", state.get("last_response_context", ""))
            + field("当前情感", affect_bits, "事件驱动 + 衰减，独立于整体心情")
            + field(
                "最近小事", "；".join(state.get("recent_events", [])[-3:]), "来自生活沙盒的小事"
            ),
        )
        + ui.card(
            "未完成的事（Open Loops）",
            f"<table class='data'><tr><th>内容</th><th>类型</th><th>状态</th>"
            f"<th>进度</th><th>来源</th><th>置信度</th></tr>{loop_rows}</table>",
        )
        + ui.card(
            "共同经历（Shared Experience）",
            f"<table class='data'><tr><th>摘要</th><th>类型</th><th>关键词</th>"
            f"<th>提及次数</th><th>置信度</th></tr>{shared_rows}</table>",
        )
        + ui.card(
            "用户聊天习惯（Interaction Profile）",
            f"<table class='data'><tr><th>用户</th><th>观察到的模式（值/置信度/样本）</th>"
            f"<th>更新时间</th></tr>{profile_rows}</table>"
            + "<div class='muted small'>模式是统计+衰减+置信度，不是永久标签（spec v1.2 §41-§43）。</div>",
        )
    )
