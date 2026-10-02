"""The ``/sandbox`` pages: her life, visible (v2.0 §100-§111).

Read-only inspection surfaces: overview (what she is doing *now*), entities,
spaces, objects & inventory, needs, actions, modes, social world, the bible
with coverage, simulation trace + replay, and a dry-run simulator. QQ never
sees any of this (v2.0 §153-§154).
"""

from __future__ import annotations

from typing import Any

from app.core.activity import blocking_fn
from app.web import ui


def _tabs(active: str) -> str:
    return ui.tabs(
        [
            ("/sandbox", "概览"),
            ("/sandbox/inspectors", "实体与空间"),
            ("/sandbox/needs", "需求与动作"),
            ("/sandbox/chat", "对话行为"),
            ("/sandbox/bible", "人物档案"),
            ("/sandbox/trace", "回放"),
        ],
        active,
    )


@blocking_fn("web.render sandbox")
def dashboard(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card(
            "生活沙盒未启用",
            "<p class='muted'>在 config 打开 <code>sandbox.enabled</code>。</p>",
        )
    context = data.get("context", {})
    tiles = ui.stats_grid(
        [
            ("当前地点", context.get("location", "-"), "她此刻在哪个空间"),
            ("正在做", context.get("action", "-") or "闲着", "当前 Action"),
            ("模式", " + ".join(context.get("modes", [])) or "-", "可叠加"),
            ("沙盒状态", data.get("phase", "-"), "running / paused / recovering"),
            ("小喵", context.get("pet", "-"), "宠物实体状态"),
            ("需求", context.get("need_line", "") or "都还好", "明显的需求压力"),
        ]
    )
    recent = data.get("recent_events", [])[:8]
    rows = (
        "".join(
            f"<tr><td class='mono'>{ui.esc(row.get('kind', ''))}</td>"
            f"<td>{ui.esc(row.get('summary', ''))}</td>"
            f"<td>{ui.esc(row.get('reason_code', '') or '-')}</td></tr>"
            for row in recent
        )
        or "<tr><td colspan='3' class='muted'>暂无事件</td></tr>"
    )
    return (
        tiles
        + ui.card(
            "她此刻的生活",
            f"<p>{ui.esc(context.get('state_line', ''))}</p>"
            + (
                f"<p class='muted'>{ui.esc(context.get('mode_line', ''))}</p>"
                if context.get("mode_line")
                else ""
            )
            + (
                f"<p class='muted'>最近的牵挂：{ui.esc(context.get('goal', ''))}</p>"
                if context.get("goal")
                else ""
            ),
        )
        + ui.card(
            "最近发生的事",
            f"<table class='data'><tr><th>类型</th><th>发生了什么</th><th>原因</th></tr>{rows}</table>",
        )
    )


def inspectors(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card("生活沙盒未启用", "<p class='muted'>见 config。<p>")
    spaces = data.get("spaces", [])
    space_rows = (
        "".join(
            f"<tr><td>{ui.esc(s['name'])}</td><td>{ui.esc(s['kind'])}</td>"
            f"<td>{ui.esc(s.get('parent_id', '') or '-')}</td>"
            f"<td>{ui.esc('、'.join(s.get('allowed_actions', [])[:4]))}</td>"
            f"<td>{ui.esc('、'.join(s.get('objects', [])))}</td></tr>"
            for s in spaces
        )
        or "<tr><td colspan='5' class='muted'>无空间</td></tr>"
    )

    objects = data.get("objects", [])
    object_rows = (
        "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td class='mono'>{}</td></tr>".format(
                ui.esc(o["name"]),
                ui.esc(o["space_id"]),
                ui.esc(o["kind"]),
                ui.esc(str(o.get("state", {}))),
            )
            for o in objects
        )
        or "<tr><td colspan='4' class='muted'>无物品</td></tr>"
    )

    inventories = data.get("inventories", {})
    inv_rows = (
        "".join(
            f"<tr><td>{ui.esc(key)}</td><td>{ui.esc('、'.join(f'{k}×{v}' for k, v in items.items()) or '空')}</td></tr>"
            for key, items in inventories.items()
            if not key.startswith("need:")
        )
        or "<tr><td colspan='2' class='muted'>无库存</td></tr>"
    )

    pet = data.get("pet", {})
    social = data.get("social_spaces", [])
    social_rows = (
        "".join(
            f"<tr><td>{ui.esc(s['name'])}</td><td>{ui.esc(s['kind'])}</td>"
            f"<td>{ui.esc(s.get('topic', ''))}</td>"
            f"<td>{ui.badge(s.get('character_presence', 'offline'), 'success' if s.get('character_presence') == 'active' else 'default')}</td>"
            f"<td>{s.get('interest', 0):.2f}</td></tr>"
            for s in social
        )
        or "<tr><td colspan='5' class='muted'>暂无社交空间</td></tr>"
    )

    return (
        ui.card(
            "小喵（宠物实体）",
            f"<p>{ui.esc(pet.get('name', '小喵'))} · 现在{ui.esc(str(pet.get('activity', '')))}"
            f" · 位置 {ui.esc(str(pet.get('location', '')))}</p>"
            f"<p class='muted'>饥饿 {pet.get('hunger', 0):.2f} · 精力 {pet.get('energy', 0):.2f}"
            f" · 亲密度 {pet.get('affection', 0):.2f}</p>",
        )
        + ui.card(
            "空间",
            f"<table class='data'><tr><th>空间</th><th>类型</th><th>上级</th>"
            f"<th>可做的事</th><th>物品</th></tr>{space_rows}</table>",
        )
        + ui.card(
            "物品",
            f"<table class='data'><tr><th>物品</th><th>空间</th><th>类型</th>"
            f"<th>状态</th></tr>{object_rows}</table>",
        )
        + ui.card(
            "库存", f"<table class='data'><tr><th>容器</th><th>内容</th></tr>{inv_rows}</table>"
        )
        + ui.card(
            "社交空间",
            f"<table class='data'><tr><th>名称</th><th>类型</th><th>话题</th>"
            f"<th>她在场</th><th>兴趣</th></tr>{social_rows}</table>",
        )
    )


def needs_page(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card("生活沙盒未启用", "<p class='muted'>见 config。</p>")
    needs = data.get("needs", {})
    need_rows = (
        "".join(
            f"<tr><td>{ui.esc(key)}</td><td>{item.get('level', 0):.2f}</td>"
            f"<td>{ui.badge(item.get('band', ''), {'critical': 'danger', 'strong': 'warning', 'soft': 'info'}.get(item.get('band', ''), 'default'))}</td>"
            f"<td>{item.get('growth_per_hour', 0):.3f}/h</td></tr>"
            for key, item in needs.items()
            if not key.startswith("need:")
        )
        or "<tr><td colspan='4' class='muted'>无需求</td></tr>"
    )

    action = data.get("action") or {}
    action_block = (
        f"<p><strong>{ui.esc(action.get('definition_id', ''))}</strong>"
        f"（{ui.esc(action.get('detail', '') or '—')}）</p>"
        f"<p class='muted'>开始 {action.get('started_at', 0):.0f} · 进度 {action.get('progress', 0):.0%}"
        f" · 原因 {ui.esc(action.get('reason_code', ''))}</p>"
        if action
        else "<p class='muted'>当前没有动作</p>"
    )
    defs = data.get("action_defs", [])
    def_rows = (
        "".join(
            f"<tr><td>{ui.esc(d['id'])}</td><td>{ui.esc(d['name'])}</td>"
            f"<td>{ui.esc('、'.join(d.get('spaces', [])[:3]))}</td>"
            f"<td>{d.get('min_minutes', 0):.0f}/{d.get('typical_minutes', 0):.0f}/{d.get('max_minutes', 0):.0f}</td>"
            f"<td>{d.get('interruptibility', 0):.2f}</td></tr>"
            for d in defs
        )
        or "<tr><td colspan='5' class='muted'>无动作定义</td></tr>"
    )
    return (
        ui.card(
            "需求压力",
            f"<table class='data'><tr><th>需求</th><th>压力</th>"
            f"<th>档位</th><th>增长</th></tr>{need_rows}</table>",
        )
        + ui.card("当前动作", action_block)
        + ui.card(
            "动作目录（人物档案锚定）",
            f"<table class='data'><tr><th>id</th><th>名称</th><th>可用空间</th>"
            f"<th>最短/典型/最长(分)</th><th>可打断度</th></tr>{def_rows}</table>",
        )
    )


def bible_page(data: dict[str, Any]) -> str:
    bible = data.get("bible")
    if not bible:
        return ui.card("人物档案未编译", "<p class='muted'>检查 config/character_bible.md。</p>")
    coverage = bible.get("coverage", {})
    facts = bible.get("facts", {})
    fact_rows = (
        "".join(
            f"<tr><td>{ui.esc(key)}</td><td>{ui.esc(str(value))}</td></tr>"
            for key, value in facts.items()
        )
        or "<tr><td colspan='2' class='muted'>无</td></tr>"
    )
    rule_rows = (
        "".join(
            f"<tr><td class='mono'>{ui.esc(rule['id'])}</td><td>{ui.esc(rule['text'])}</td>"
            f"<td>{ui.esc(rule.get('runtime', ''))}</td>"
            f"<td>{ui.esc(rule.get('source', ''))}</td></tr>"
            for rule in bible.get("rules", [])
        )
        or "<tr><td colspan='4' class='muted'>无</td></tr>"
    )
    mode_rows = (
        "".join(
            f"<tr><td class='mono'>{ui.esc(mode['id'])}</td><td>{ui.esc(mode['name'])}</td>"
            f"<td>{ui.esc(mode.get('trigger', ''))}</td><td>{ui.esc(mode.get('style', ''))}</td></tr>"
            for mode in bible.get("modes", [])
        )
        or "<tr><td colspan='4' class='muted'>无</td></tr>"
    )
    unresolved = bible.get("unresolved", []) or coverage.get("unresolved", [])
    conflicts = bible.get("conflicts", []) or coverage.get("conflicts", [])
    return (
        ui.stats_grid(
            [
                ("档案版本", bible.get("version", "-"), "source hash"),
                ("静态事实", coverage.get("facts", 0), "进入 CanonicalCharacterFacts"),
                ("模式", coverage.get("modes", 0), "Mode Runtime"),
                ("规则", coverage.get("rules", 0), "WorldRuleEngine"),
                ("仅 Prompt", len(coverage.get("prompt_only", [])), "语气与散文（允许）"),
                ("未解析", len(unresolved), "不静默丢弃"),
            ]
        )
        + ui.card(
            "静态事实", f"<table class='data'><tr><th>键</th><th>值</th></tr>{fact_rows}</table>"
        )
        + ui.card(
            "模式",
            f"<table class='data'><tr><th>id</th><th>名称</th><th>触发</th>"
            f"<th>风格</th></tr>{mode_rows}</table>",
        )
        + ui.card(
            "规则（Canonical）",
            f"<table class='data'><tr><th>id</th><th>内容</th><th>运行时归属</th>"
            f"<th>档案出处</th></tr>{rule_rows}</table>",
        )
        + ui.card(
            "未解析 / 冲突",
            f"<p>未解析：{ui.esc('；'.join(unresolved) or '无')}</p>"
            f"<p class='muted'>冲突：{ui.esc('；'.join(conflicts) or '无')}</p>",
        )
    )


def trace_page(data: dict[str, Any]) -> str:
    if not data.get("enabled"):
        return ui.card("生活沙盒未启用", "<p class='muted'>见 config。</p>")
    replay = data.get("replay", [])
    rows = (
        "".join(
            f"<tr><td class='mono'>{ui.esc(_fmt_ts(item.get('ts', 0)))}</td>"
            f"<td>{ui.esc(item.get('kind', ''))}</td>"
            f"<td>{ui.esc(item.get('summary', ''))}</td>"
            f"<td>{ui.esc(item.get('reason', '') or '-')}</td></tr>"
            for item in replay
        )
        or "<tr><td colspan='4' class='muted'>暂无轨迹</td></tr>"
    )
    sim = data.get("simulation")
    sim_block = ""
    if sim:
        sim_block = ui.card(
            "干跑模拟（未发送 QQ）",
            f"<p>运行 {sim.get('hours')} 小时 → {sim.get('ticks')} tick，完成 {sim.get('completed_actions')} 个动作</p>"
            + (
                f"<p class='muted'>最近的动作序列：{ui.esc(' → '.join(sim.get('transitions', [])))}</p>"
                if sim.get("transitions")
                else ""
            ),
        )
    return sim_block + ui.card(
        "生活回放（结构化，无思维链）",
        f"<table class='data'><tr><th>时间</th><th>类型</th><th>发生了什么</th>"
        f"<th>原因码</th></tr>{rows}</table>",
    )


def _fmt_ts(stamp: float) -> str:
    import time

    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(stamp)))
    except (TypeError, ValueError, OSError):
        return str(stamp)
