"""Memory correction: fix what the character believes, through plain language.

The operator describes the fix ("把用户喜欢吃的西瓜改成草莓"); a model turns that
into a *structured* plan against the user's actual memories; the plan is then
applied through :meth:`MemoryManager.force_supersede` — the old row is retired,
the new one is written as a plain fact.

Design rules that matter here:

* **no edit-in-place**: the stored content never carries a "corrected" marker,
  so retrieval — and therefore the character — treats it as always true;
* **no silent guessing**: the plan is always shown for confirmation first;
* **audit keeps history**: the superseded row and the relation stay in the DB
  (and a ``memory_correction`` behaviour event records who changed what).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.memory.model import CATEGORIES

if TYPE_CHECKING:
    from app.core.bot import Bot

CORRECTION_PROMPT = """你是记忆维护工具。用户会给你一条"修正指令"和该用户当前的记忆列表。

请判断这条指令要修正哪一条记忆，并输出**修正后的最终事实**。

规则：
1. new_content 必须是"关于这个用户的、稳定成立的事实"，用第三人称、陈述句，例如"用户喜欢吃草莓"。
2. new_content 里**绝对不能**出现"改成了/更正为/不再是/以前/现在/更新"等任何暗示修改过程的词，
   也不要提及修正指令本身。它就是一句一直成立的事实。
3. action 取值：
   - "replace"：指令要求把某条已有记忆的内容改掉（必须给出 memory_id，且是列表里真实存在的 id）
   - "remove"：指令要求忘掉/删除某条记忆（必须给出 memory_id）
   - "create"：列表里没有对应记忆，指令是在补充一条新事实（memory_id 留空）
   - "no_change"：指令与记忆无关、太模糊、或无法安全执行
   无法确定 memory_id 时一律用 "no_change"。
4. category 只能从这些里选：
   fact / preference / profile / project / interest / habit / event / relationship
   （不确定就沿用这条记忆原来的类别）。
5. importance 0~1，表示这条事实的重要程度（不确定就沿用原记忆的值）。
6. 只输出 JSON，不要解释，不要 markdown 代码块。

输出格式：
{"action":"replace","memory_id":12,"new_content":"用户喜欢吃草莓","category":"preference","importance":0.7,"summary":"","reason":"一句话说明判断依据"}
"""


class MemoryCorrectionService:
    """Prompt-driven memory correction for the WebUI (never exposed to QQ)."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Memory")

    # ------------------------------------------------------------- browsing

    async def targets(self) -> list[dict[str, Any]]:
        """Scopes that have memories, newest activity first (for the picker)."""
        if self.bot.memory is None:
            return []
        rows = await self.bot.database.fetchall(
            """SELECT scope_key, COUNT(*) AS total,
                      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                      MAX(updated_at) AS last_at
                 FROM memories GROUP BY scope_key ORDER BY last_at DESC LIMIT 100"""
        )
        relationships = {r.user_id: r for r in await self.bot.relationships.all()}
        targets: list[dict[str, Any]] = []
        for row in rows:
            scope = str(row["scope_key"])
            user_id = scope.split(":", 1)[-1]
            relationship = relationships.get(user_id)
            profile = await self.bot.database.fetchone(
                """SELECT COALESCE(p.nickname_override, u.nickname) AS display
                     FROM users u LEFT JOIN user_profiles p ON p.user_id = u.user_id
                    WHERE u.user_id = ?""",
                (user_id,),
            )
            targets.append(
                {
                    "scope_key": scope,
                    "label": (profile or {}).get("display") or f"用户 {user_id}",
                    "user_id": user_id,
                    "active": int(row["active"] or 0),
                    "total": int(row["total"] or 0),
                    "stage": getattr(relationship, "stage", "") or "-",
                    "last_at": int(row["last_at"] or 0),
                }
            )
        return targets

    async def memories(self, scope_key: str, *, limit: int = 60) -> list[dict[str, Any]]:
        if self.bot.memory is None or not scope_key:
            return []
        rows = await self.bot.memory.list_memories(
            scope_key=scope_key, status="active", limit=limit
        )
        return [
            {
                "id": memory.id,
                "content": memory.content,
                "category": memory.category,
                "importance": memory.importance,
                "confidence": memory.confidence,
                "layer": memory.layer,
                "updated_at": memory.updated_at,
            }
            for memory in rows
        ]

    # -------------------------------------------------------------- planning

    async def plan(self, scope_key: str, instruction: str) -> dict[str, Any]:
        """Ask the model which memory to fix and what the new fact should be."""
        instruction = (instruction or "").strip()
        if not instruction:
            raise ValueError("请先写清楚要修正什么")
        if self.bot.memory is None:
            raise ValueError("记忆功能未启用")
        memories = await self.memories(scope_key)
        if not memories:
            raise ValueError("这个 scope 下没有可修正的记忆")

        listing = "\n".join(f"- id={m['id']} [{m['category']}] {m['content']}" for m in memories)
        payload = f"修正指令：{instruction}\n\n当前记忆列表：\n{listing}"
        response = await self._ask(payload)
        plan = self._parse(response)
        if plan is None:
            raise ValueError("模型没有给出可执行的修正方案，请把指令写得更具体一些")

        action = str(plan.get("action", "no_change"))
        memory_id = plan.get("memory_id")
        try:
            memory_id = int(memory_id) if memory_id is not None else 0
        except (TypeError, ValueError):
            memory_id = 0
        if action in ("replace", "remove") and memory_id not in {m["id"] for m in memories}:
            action = "no_change"
            plan["reason"] = plan.get("reason") or "memory_id 不在候选列表内"

        target = next((m for m in memories if m["id"] == memory_id), None)
        new_content = " ".join(str(plan.get("new_content") or "").split())
        if action == "replace" and not new_content:
            action = "no_change"
        if action == "create" and not new_content:
            action = "no_change"

        category = str(plan.get("category") or (target or {}).get("category") or "fact")
        if category not in CATEGORIES:
            category = (target or {}).get("category", "fact")
        raw_importance = plan.get("importance")
        try:
            importance = float(raw_importance) if raw_importance is not None else 0.6
        except (TypeError, ValueError):
            importance = float((target or {}).get("importance", 0.6) or 0.6)

        return {
            "action": action,
            "memory_id": memory_id or None,
            "before": (target or {}).get("content", ""),
            "after": new_content,
            "category": category,
            "importance": max(0.0, min(1.0, importance)),
            "summary": " ".join(str(plan.get("summary") or "").split()),
            "reason": str(plan.get("reason") or ""),
            "scope_key": scope_key,
            "instruction": instruction,
        }

    async def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Execute a confirmed plan (retire old, write new, keep the audit trail)."""
        memory = self.bot.memory
        if memory is None:
            raise ValueError("记忆功能未启用")
        action = str(plan.get("action") or "no_change")
        scope_key = str(plan.get("scope_key") or "")
        instruction = str(plan.get("instruction") or "")
        memory_id = plan.get("memory_id")

        result: dict[str, Any] = {"action": action, "changed": False}
        if action == "replace" and memory_id:
            replacement = await memory.force_supersede(
                int(memory_id),
                content=str(plan.get("after") or ""),
                category=str(plan.get("category") or ""),
                importance=float(plan.get("importance") or 0.6),
                summary=str(plan.get("summary") or ""),
            )
            if replacement is None:
                raise ValueError("目标记忆已经不存在了，请刷新后重试")
            result.update(
                {"changed": True, "new_id": replacement.id, "content": replacement.content}
            )
        elif action == "create":
            scope, user_id, group_id = self._scope_parts(scope_key)
            created = await memory.remember(
                scope,
                user_id or group_id or "",
                str(plan.get("after") or ""),
                category=str(plan.get("category") or "fact"),
                importance=float(plan.get("importance") or 0.6),
                confidence=0.95,
                user_id=user_id,
                group_id=group_id,
                source="explicit",
                summary=str(plan.get("summary") or ""),
            )
            if created is not None:
                result.update({"changed": True, "new_id": created.id, "content": created.content})
        elif action == "remove" and memory_id:
            result["changed"] = await memory.forget(int(memory_id), reason="webui_correction")
        else:
            result["reason"] = "无需修改"

        await self._audit(result, plan, instruction, scope_key)
        return result

    # ------------------------------------------------------------- internals

    @staticmethod
    def _scope_parts(scope_key: str) -> tuple[str, str | None, str | None]:
        """``user:123`` / ``group:456`` -> (scope, user_id, group_id)."""
        kind, _, ref = scope_key.partition(":")
        if kind == "group":
            return "group", None, ref or None
        return "user", ref or None, None

    async def _ask(self, payload: str) -> str:
        engine = self.bot.ai
        if not engine.enabled:
            raise ValueError("AI 未启用，无法解析修正指令")
        request = AIRequest(
            messages=[
                ChatMessage.system(CORRECTION_PROMPT),
                ChatMessage.user(payload),
            ],
            temperature=0.1,
        )
        try:
            response = await engine.chat(request)
        except AIError as exc:
            raise ValueError(f"模型调用失败：{exc}") from exc
        return response.content

    @staticmethod
    def _parse(content: str) -> dict[str, Any] | None:
        text = content.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    async def _audit(
        self, result: dict[str, Any], plan: dict[str, Any], instruction: str, scope_key: str
    ) -> None:
        try:
            detail = json.dumps(
                {
                    "instruction": instruction[:200],
                    "action": result.get("action"),
                    "before": str(plan.get("before") or "")[:200],
                    "after": str(plan.get("after") or "")[:200],
                },
                ensure_ascii=False,
            )
            await self.bot.database.execute(
                """INSERT INTO behavior_events
                   (type, scope_key, user_id, reason, detail, status, created_at)
                   VALUES ('memory_correction', ?, ?, 'webui', ?, 'done', ?)""",
                (
                    scope_key,
                    scope_key.split(":", 1)[-1],
                    detail,
                    int(time.time()),
                ),
            )
        except Exception:  # noqa: BLE001 - auditing must never block the correction
            self._log.exception("Failed to record memory correction audit")

        if result.get("changed"):
            try:
                from app.utils.narrator import narrate

                narrate().say(
                    "mind",
                    f"记忆已改成：{str(result.get('content') or '')[:40]}",
                    detail=f"{scope_key} · WebUI 修正",
                )
            except Exception:  # noqa: BLE001 - cosmetic
                pass
