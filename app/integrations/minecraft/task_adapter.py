"""Phase 5A：把 TaskRuntime 接到既有的 Minecraft 工具链路上。

**架构边界**（§四/§五/§一百零二）：TaskRuntime → Agent Bridge → Policy → Confirmation →
MinecraftService → ActionRuntime → Mineflayer。TaskRuntime 永远不会直接碰
Mineflayer 或 runtime HTTP；它只认识工具、参数、风险、授权、action_id 与结果。

这里提供三件东西：

* :class:`MinecraftPlanConfirmation`：计划确认门 —— 复用**同一个** ConfirmationStore
  （§四十：不建第二套确认数据库），只是多带 ``task_id`` / ``plan_hash``；
* :class:`MinecraftTaskInvoker`：把一次步骤调用转成 TaskRuntime 认识的 :class:`TaskInvocation`
  （同步动作直接给结果；持续型动作给 ``RUNNING + action_id``，终态等 action 事件）；
* :func:`build_minecraft_task_runtime`：按当前配置组装 TaskRuntime（含计划校验所需的
  工具表/schema 校验、最终校验要用的背包读取、以及"这一步有没有授权"的校验器）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.confirmation import ConfirmationStore
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import SqliteTaskStore
from app.tasks.validation import default_step_label


class MinecraftPlanConfirmation:
    """计划确认（一次确认整份冻结计划）。"""

    #: 计划确认在 ConfirmationStore 里用的工具名（不是真实工具，只作为确认条目的标识）
    TOOL = "minecraft_task"

    def __init__(self, store: ConfirmationStore) -> None:
        self._store = store

    async def request(
        self,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        risk: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        summary: str,
    ) -> str:
        confirmation = self._store.create(
            session_id=session_id,
            user_id=user_id,
            tool=self.TOOL,
            risk=risk,
            arguments=dict(arguments),
            summary=summary,
            task_id=task_id,
            plan_hash=plan_hash,
        )
        return confirmation.confirmation_id

    async def consume(
        self,
        confirmation_id: str,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        origin: str,
    ) -> tuple[bool, str]:
        from app.character.turn import TurnOrigin

        try:
            turn = TurnOrigin(origin)
        except ValueError:
            return False, "minecraft.confirmation_not_user_turn"
        outcome = self._store.consume(
            confirmation_id,
            session_id=session_id,
            user_id=user_id,
            arguments=dict(arguments),
            turn_origin=turn,
        )
        return outcome.ok, outcome.code

    async def cancel(self, confirmation_id: str) -> None:
        if confirmation_id:
            self._store.cancel(confirmation_id)


class MinecraftTaskInvoker:
    """按步骤调用已有工具（经 Agent Bridge），并把结果转成 TaskInvocation。"""

    def __init__(self, bridge: MinecraftAgentBridge) -> None:
        self._bridge = bridge

    async def __call__(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str = "",
        step_id: str = "",
        plan_hash: str = "",
        risk: str = "",
        authorization: Any = None,
    ) -> TaskInvocation:
        if tool == "minecraft_stop" or not task_id:
            # 控制面调用（取消/过期时停动作）与 SAFE 读取：走正常的 SAFE 通道
            result = await self._bridge.invoke_developer(
                tool, dict(arguments), _service_call(tool, dict(arguments))
            )
            return _to_invocation(tool, result)
        result = await self._bridge.invoke_task_step(
            tool,
            dict(arguments),
            task_id=task_id,
            step_id=step_id,
            plan_hash=plan_hash,
            risk=risk,
            authorization=authorization,
            call=_service_call(tool, dict(arguments)),
        )
        return _to_invocation(tool, result)


def _service_call(
    tool: str, arguments: Mapping[str, Any]
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    """把工具调用映射成 MinecraftService 上的方法（与各 Tool 的 ``_call`` 完全同款）。"""
    handler = _SERVICE_ROUTES.get(tool)
    if handler is None:
        raise KeyError(f"没有为 {tool} 注册服务调用")

    async def call(service: Any) -> dict[str, Any]:
        return await handler(service, arguments)

    return call


async def _world(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return service.world_view()


async def _inventory(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.inventory()


async def _find_blocks(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.find_blocks(
        args.get("block_names"), args.get("max_distance"), args.get("max_results")
    )


async def _dig_capability(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.dig_capability(args.get("x"), args.get("y"), args.get("z"))


async def _dropped_items(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.dropped_items()


async def _look_at(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.look_at(args.get("x"), args.get("y"), args.get("z"))


async def _move_to(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.move_to(args.get("x"), args.get("y"), args.get("z"))


async def _follow_player(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Phase 7D：跟随是**持续型**动作 —— 启动即返回 RUNNING + action_id，
    终态（超时/目标丢失/停止）由 action 事件异步送达（与 LLM 工具路径同一服务方法）。"""
    return await service.follow_player(args.get("username"), args.get("distance"))


async def _dig(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.dig(
        args.get("x"),
        args.get("y"),
        args.get("z"),
        args.get("expected_block"),
        args.get("expected_tool"),
    )


async def _equip(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.equip(args.get("item"))


async def _pickup(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.pickup_item(args.get("entity_id"), args.get("expected_item"))


async def _stop(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.stop_action()


_SERVICE_ROUTES: dict[str, Callable[[Any, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    "minecraft_world": _world,
    "minecraft_inventory": _inventory,
    "minecraft_find_blocks": _find_blocks,
    "minecraft_dig_capability": _dig_capability,
    "minecraft_dropped_items": _dropped_items,
    "minecraft_look_at": _look_at,
    "minecraft_move_to": _move_to,
    "minecraft_follow_player": _follow_player,
    "minecraft_dig": _dig,
    "minecraft_equip": _equip,
    "minecraft_pickup_item": _pickup,
    "minecraft_stop": _stop,
}


def _to_invocation(tool: str, result: Any) -> TaskInvocation:
    """ToolResult → TaskInvocation（编排层只认识后者）。"""
    if result is None:  # pragma: no cover - 防御
        return TaskInvocation(ok=False, error=f"{tool} 没有返回结果", code="task.internal")
    data: dict[str, Any] = dict(getattr(result, "data", None) or {})
    action_id = str(data.get("action_id") or "")
    status = str(data.get("status") or "")
    if not getattr(result, "success", False):
        code = str(getattr(result, "error_type", "") or "")
        return TaskInvocation(
            ok=False,
            status=status,
            action_id=action_id,
            error=str(getattr(result, "error", "") or code),
            code=code,
            detail=data,
        )
    # 有的服务方法把语义结果包在 result 里（例如 find_blocks / inventory）
    payload = data.get("result") if isinstance(data.get("result"), Mapping) else None
    return TaskInvocation(
        ok=True,
        status=status or "SUCCEEDED",
        action_id=action_id,
        result=dict(payload or data),
        summary=str(getattr(result, "summary", "") or ""),
    )


def _tool_of(tool_runtime: Any, tool: str) -> Any:
    return tool_runtime.registry.maybe_get(tool)


def _schema_of(tool_runtime: Any, tool: str) -> Any:
    found = _tool_of(tool_runtime, tool)
    if found is None:
        return None
    return found.metadata.input_schema


def _task_config(source: Any) -> TaskConfig | None:
    """把配置对象（TaskConfig 或 pydantic 的 TaskRuntimeConfig）换成运行时的上限。

    只认这几个旋钮；缺哪个用代码默认值（绝不因为配置缺字段就放开上限）。
    """
    if source is None:
        return None
    if isinstance(source, TaskConfig):
        return source
    base = TaskConfig()
    return TaskConfig(
        ttl_seconds=float(getattr(source, "ttl_seconds", base.ttl_seconds)),
        max_steps=int(getattr(source, "max_steps", base.max_steps)),
        max_action_steps=int(getattr(source, "max_action_steps", base.max_action_steps)),
        max_replans=int(getattr(source, "max_replans", base.max_replans)),
        no_progress_limit=int(getattr(source, "no_progress_limit", base.no_progress_limit)),
    )


async def build_minecraft_task_runtime(
    service: Any,
    *,
    tools_config: Any,
    database: Any = None,
    config: Any = None,
    publish: Callable[[str, dict[str, Any]], None] | None = None,
    store: Any = None,
    logger: Any = None,
) -> TaskRuntime:
    """按当前配置组装 TaskRuntime（并给它装上"这一步有没有授权"的校验器）。"""
    from app.tools.runtime import ToolRuntime
    from app.tools.schema import validate_arguments as validate_tool_arguments

    bridge: MinecraftAgentBridge = service.agent
    # 只用来查"工具注册/schema"：注意 builtins 是在 start() 里注册的，所以必须 await 它，
    # 否则 registry 是空的、计划校验会把所有工具都判成"未注册"。
    tool_runtime = ToolRuntime(tools_config)
    await tool_runtime.start()
    # ToolRuntime 是异步启动的；这里只需要它的静态注册表视图，直接读 registry。
    task_store = store if store is not None else SqliteTaskStore(database)

    async def _world_facts() -> dict[str, Any]:
        try:
            return dict(bridge.world_facts())
        except Exception:  # noqa: BLE001
            return {}

    runtime = TaskRuntime(
        store=task_store,
        invoke=MinecraftTaskInvoker(bridge),
        confirmations=MinecraftPlanConfirmation(bridge.confirmations),
        config=_task_config(config),
        publish=publish,
        risk_of=bridge.policy.risk_of,
        is_registered=lambda tool: _tool_of(tool_runtime, tool) is not None,
        schema_of=lambda tool: _schema_of(tool_runtime, tool),
        validate_arguments=lambda schema, arguments: validate_tool_arguments(
            dict(schema), dict(arguments)
        ),
        world_facts=_world_facts,
        label_of=default_step_label,
        logger=logger,
    )
    bridge.set_task_authorizer(runtime.authorize_step)
    return runtime
