"""The ``/memory/correction`` page: fix a memory by describing the fix.

Flow: pick a user → see their current memories → describe the correction in
plain language → review the model's *structured* plan → apply. Nothing is
written until the operator confirms.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.activity import blocking_fn
from app.web import ui


@blocking_fn("web.render memory correction")
def render(
    service: Any,
    *,
    targets: list[dict[str, Any]],
    scope_key: str = "",
    memories: list[dict[str, Any]] | None = None,
    instruction: str = "",
    plan: dict[str, Any] | None = None,
    msg: str = "",
    kind: str = "ok",
) -> str:
    memories = memories or []
    body = [
        ui.flash(kind, msg) if msg else "",
        _explainer(),
        _picker(targets, scope_key, instruction),
    ]
    if scope_key and memories:
        body.append(_memories(scope_key, memories))
    if plan is not None:
        body.append(_plan_card(plan))
    return "".join(body)


def _explainer() -> str:
    return ui.card(
        "记忆修正怎么工作",
        """
<ol style="margin:0 0 6px 18px;padding:0;line-height:1.9">
  <li>你写一句人话，例如「把用户喜欢吃西瓜改成草莓」。{tip}</li>
  <li>模型只在<strong>这位用户已有的记忆</strong>里找目标，并给出「修正后的最终事实」。{plan_tip}</li>
  <li>确认后：旧记忆被标记为 <code>superseded</code>（保留在时间线里可审计），
      新记忆以 <code>explicit</code> 来源写入，内容就是一句普通的既存事实。{apply_tip}</li>
  <li>检索只会看到新事实——所以她会认为「用户一直喜欢吃草莓」，而不是「曾经喜欢西瓜后来改了」。{believe_tip}</li>
</ol>
""".format(
            tip=ui.tip("不需要写 JSON，也不用选记忆；说得越具体，模型定位越准"),
            plan_tip=ui.tip("这一步只生成方案，不会写入数据库；你可以先看再决定"),
            apply_tip=ui.tip("不做原地编辑：原文不会出现「已更正」这类字样，避免她察觉被改过"),
            believe_tip=ui.tip("这正是「记忆修正」和「手工改一行字」的区别"),
        ),
        tip_text="用自然语言纠正长期记忆，而不是直接编辑数据库",
    )


def _picker(targets: list[dict[str, Any]], scope_key: str, instruction: str) -> str:
    options = (
        "".join(
            f"<option value='{ui.esc(item['scope_key'])}'"
            f"{' selected' if item['scope_key'] == scope_key else ''}>"
            f"{ui.esc(item['label'])}（{ui.esc(item['scope_key'])}，"
            f"{item['active']} 条生效 / 共 {item['total']}）</option>"
            for item in targets
        )
        or "<option value=''>（还没有任何用户记忆）</option>"
    )
    field = f"""
<form method="get" action="/memory/correction">
  <label class="field">
    <span class="field-label">选择用户{ui.tip("记忆是按用户/群隔离的，先选人要修正谁")}</span>
    <select name="scope">{options}</select>
  </label>
  <label class="field">
    <span class="field-label">修正指令{ui.tip("用一句人话说明要改成什么，例如：把用户喜欢吃的东西改成草莓")}</span>
    <input name="instruction" value="{ui.esc(instruction)}"
      placeholder="例如：把用户喜欢吃西瓜改成草莓">
  </label>
  <p><button class="btn btn-secondary" type="submit"
      data-tip="先加载这位用户的记忆，并让模型给出修正方案（此时不会写库）">生成修正方案</button></p>
</form>
"""
    return ui.card("1 · 选择对象并描述修正", field, tip_text="生成方案只是预览，确认后才会真正生效")


def _memories(scope_key: str, memories: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr><td>{memory['id']}</td><td>{ui.esc(memory['content'])}</td>"
        f"<td>{ui.badge(memory['category'], 'info')}</td>"
        f"<td>{memory['importance']:.2f}</td><td>{memory['confidence']:.2f}</td>"
        f"<td class='muted'>{ui.esc(memory['layer'])}</td></tr>"
        for memory in memories
    )
    return ui.card(
        f"当前记忆（{ui.esc(scope_key)}）",
        ui.table(
            ["ID", "内容", "类别", "重要度", "置信度", "层级"],
            rows,
            tips=[
                "记忆 ID（模型会用它定位）",
                "记忆正文",
                "类别：fact/preference/…",
                "重要度：越高越优先被想起",
                "置信度：模型有多确信",
                "semantic=长期结论，episodic=某件事",
            ],
            empty="这位用户还没有记忆",
            table_id="correction-memories",
        ),
        tip_text="这里只显示 active 记忆；被 superseded 的旧事实不会出现在提示词里",
    )


def _plan_card(plan: dict[str, Any]) -> str:
    action = str(plan.get("action") or "no_change")
    labels = {
        "replace": ("替换已有记忆", "success"),
        "create": ("新增一条事实", "info"),
        "remove": ("忘记这条记忆", "warning"),
        "no_change": ("不改动", "default"),
    }
    label, tone = labels.get(action, (action, "default"))
    detail = ui.kv(
        [
            ("原始记忆", plan.get("before") or "—"),
            ("修正后", plan.get("after") or "—"),
            ("类别", plan.get("category") or "-"),
            ("重要度", f"{float(plan.get('importance') or 0):.2f}"),
            ("模型的判断依据", plan.get("reason") or "-"),
        ],
        tips={
            "原始记忆": "将被标记为 superseded（保留可审计，但不再进入提示词）",
            "修正后": "会作为一句普通的既存事实写入，不含任何「被修改过」的痕迹",
            "模型的判断依据": "结构化方案的说明，不是隐藏思维链",
        },
    )
    confirm = ""
    if action != "no_change":
        payload = ui.esc(json.dumps(plan, ensure_ascii=False))
        confirm = f"""
<form method="post" action="/memory/correction/apply"
      data-confirm="确认应用这次修正？旧记忆会被标记为 superseded。">
  <input type="hidden" name="plan" value="{payload}">
  <button class="btn btn-primary" type="submit"
    data-tip="写入新事实并让旧记忆退出检索">确认应用修正</button>
</form>
"""
    else:
        confirm = "<p class='hint'>模型认为无需改动。可以把指令写得更具体（指明对象 + 新内容）后重试。</p>"

    return ui.card(
        f"2 · 修正方案 {ui.badge(label, tone)}",
        f"{detail}{confirm}",
        tip_text="请先确认「修正后」这句话就是你要她相信的事实",
    )
