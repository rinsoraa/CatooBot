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
from app.tasks.life_guard import LifeGuard, check_life_guard
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


#: Phase 7F.5.1 §三.1：这次调用的**前后**读到的实例不同 —— 说明调用期间 Runtime 换了人，
#: 于是「这个动作被谁接受、算不算数」无法确认。它不是传输层错误码（请求可能确实送到了），
#: 所以单独一个码，交回 TaskRuntime 走既有的 fail-closed 歧义路径。
RUNTIME_INSTANCE_SWAPPED = "runtime.instance_changed"


class MinecraftTaskInvoker:
    """按步骤调用已有工具（经 Agent Bridge），并把结果转成 TaskInvocation。"""

    def __init__(
        self,
        bridge: MinecraftAgentBridge,
        *,
        runtime_instance_of: Callable[[], str] | None = None,
        life_guard: LifeGuard | None = None,
        task_reader: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._bridge = bridge
        self._runtime_instance_of = runtime_instance_of
        self.life_guard = life_guard
        self.task_reader = task_reader

    def runtime_instance(self) -> str:
        """当前 Runtime 实例（Phase 7F.5.1）：读不到就返回空串，绝不抛。"""
        provider = self._runtime_instance_of
        if provider is None:
            return ""
        try:
            return str(provider() or "")
        except Exception:  # noqa: BLE001 - 身份读不到不该让派发崩
            return ""

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
        # Phase 7F.5.1 §三.1：调用**前后**各读一次实例 —— 调用期间 Service 的
        # _ensure_runtime() 会做一次成功的身份探测，所以调用后的读数是「真正服务这次
        # 请求的那个实例」；调用前那次则用来发现「调用过程中换了人」。
        if tool != "minecraft_stop" and task_id and self.task_reader is not None:
            try:
                record = await self.task_reader(task_id)
            except Exception:  # noqa: BLE001 - unreadable authorization context is not permission
                return TaskInvocation(
                    ok=False, code="task.context_unavailable", error="无法读取当前任务，未派发动作"
                )
            if record is None:
                return TaskInvocation(
                    ok=False, code="task.context_unavailable", error="当前任务不存在，未派发动作"
                )
            if str(getattr(record, "source", "")) == "qq_life_plan":
                admission = await check_life_guard(self.life_guard, ignore_task_id=task_id)
                if not admission["allowed"]:
                    return TaskInvocation(
                        ok=False,
                        code="life.precondition_changed",
                        error="生活计划的当前前提不满足，未派发动作",
                        detail={"reason": admission["reason"]},
                    )
        before = self.runtime_instance()
        if tool == "minecraft_stop" or not task_id:
            # 控制面调用（取消/过期时停动作）与 SAFE 读取：走正常的 SAFE 通道
            result = await self._bridge.invoke_developer(
                tool, dict(arguments), _service_call(tool, dict(arguments))
            )
        else:
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
        after = self.runtime_instance()
        invocation = _to_invocation(tool, result)
        if before and after and before != after and invocation.detached:
            # 调用期间实例换了人，而且这个动作**已经跑到外部去了**（detached）：
            # 它被哪个实例接受、还会不会有终态事件，都无法确认 -> 按歧义 fail-closed。
            # 不带 action_id 向上走，确保 _settle 走歧义分支而不是「等动作」分支；
            # 真实的 action_id 留在 detail 里供人工核对。
            return TaskInvocation(
                ok=False,
                ambiguous=True,
                code=RUNTIME_INSTANCE_SWAPPED,
                error=(
                    f"调用 {tool} 期间 Runtime 实例已更换（{before} -> {after}）："
                    "无法确认这次动作是否已被接受，也不会重新派发"
                ),
                detail={
                    **dict(invocation.detail or {}),
                    "action_id": invocation.action_id,
                    "instance_before": before,
                    "instance_after": after,
                },
                runtime_instance=after,
            )
        # 同步完成的动作不受影响（终态已知，不需要实例来关联）；只有 detached 的动作
        # 需要实例来做事件关联。
        invocation.runtime_instance = after or before
        return invocation


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


async def _entities(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.entities()


async def _combat_verify(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.combat_verify(
        args.get("entity_id"),
        args.get("expected_type"),
        expected_uuid=args.get("expected_uuid"),
    )


async def _attack(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.attack(
        args.get("entity_id"),
        args.get("expected_type"),
        expected_uuid=args.get("expected_uuid"),
        max_distance=args.get("max_distance"),
    )


async def _recipe_lookup(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Phase 8F：合成规划期的**只读**配方观察（SAFE，与既有 Tool 的 `_call` 同款）。"""
    return await service.recipe_lookup(args.get("item"), args.get("crafting_table"))


async def _look_at(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.look_at(args.get("x"), args.get("y"), args.get("z"))


async def _move_to(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.move_to(args.get("x"), args.get("y"), args.get("z"))


async def _follow_player(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Phase 7D：跟随是**持续型**动作 —— 启动即返回 RUNNING + action_id，
    终态（超时/目标丢失/停止）由 action 事件异步送达（与 LLM 工具路径同一服务方法）。"""
    return await service.follow_player(args.get("username"), args.get("distance"))


async def _dig(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    extra = (
        {"expected_empty_hand": args["expected_empty_hand"]}
        if "expected_empty_hand" in args
        else {}
    )
    return await service.dig(
        args.get("x"),
        args.get("y"),
        args.get("z"),
        args.get("expected_block"),
        args.get("expected_tool"),
        require_collection=args.get("require_collection") is True,
        **extra,
    )


async def _place(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.place(
        args.get("x"),
        args.get("y"),
        args.get("z"),
        args.get("face"),
        args.get("expected_item"),
    )


async def _equip(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.equip(args.get("item"))


async def _pickup(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    extra = (
        {"expected_break_action": args["expected_break_action"]}
        if "expected_break_action" in args
        else {}
    )
    return await service.pickup_item(
        args.get("entity_id"),
        args.get("expected_item"),
        entity_uuid=args.get("entity_uuid"),
        **extra,
    )


async def _craft(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Phase 8F：合成**执行**步骤（MEDIUM；仍必须过既有 Policy/授权/确认门，绝不旁路）。"""
    return await service.craft(args.get("recipe_id"), args.get("crafting_table"))


async def _stop(service: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    return await service.stop_action()


_SERVICE_ROUTES: dict[str, Callable[[Any, Mapping[str, Any]], Awaitable[dict[str, Any]]]] = {
    "minecraft_world": _world,
    "minecraft_inventory": _inventory,
    "minecraft_find_blocks": _find_blocks,
    "minecraft_dig_capability": _dig_capability,
    "minecraft_dropped_items": _dropped_items,
    "minecraft_entities": _entities,
    "minecraft_recipe_lookup": _recipe_lookup,
    "minecraft_look_at": _look_at,
    "minecraft_move_to": _move_to,
    "minecraft_follow_player": _follow_player,
    "minecraft_dig": _dig,
    "minecraft_attack": _attack,
    "minecraft_combat_verify": _combat_verify,
    "minecraft_place": _place,
    "minecraft_equip": _equip,
    "minecraft_pickup_item": _pickup,
    "minecraft_craft": _craft,
    "minecraft_stop": _stop,
}


#: Phase 7F.4：**传输层**失败的 code —— 请求可能已经送达外部 runtime，只是答复丢了。
#: 这类失败绝不能被当成"动作没有执行"，否则一次 resume() 就会把同一个动作再发一次。
#: 注意**不包含** runtime.error / minecraft.* ：那些是 runtime 给出的**明确答复**（终态已知）。
AMBIGUOUS_TRANSPORT_CODES = frozenset({"runtime.unreachable", "runtime.bad_response"})


def _to_invocation(tool: str, result: Any) -> TaskInvocation:
    """ToolResult → TaskInvocation（编排层只认识后者）。

    Phase 7F.4：工具层保证**不抛异常**（agent.py 的"工具层永不抛给模型"），所以
    "请求发出去了但没拿到可用答复"只能在这里按 code 判定 —— 这是判别歧义的正确位置。
    """
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
            # 传输层失败 -> 无法确认外部是否接受 -> 交给 fail-closed 歧义分支处理。
            ambiguous=code in AMBIGUOUS_TRANSPORT_CODES,
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
    life_guard: LifeGuard | None = None,
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

    # Phase 7F.5.1：身份能力**按能力探测**接入 —— 完全没实现身份 API 的替身（单测里的
    # FakeService）不该被当成"有身份追踪"，否则会假装自己在做实例校验（那才是静默降级）。
    instance_of = getattr(service, "runtime_instance", None)
    identity_probe = getattr(service, "probe_runtime_instance", None)
    invoker = MinecraftTaskInvoker(bridge, runtime_instance_of=instance_of, life_guard=life_guard)
    runtime = TaskRuntime(
        store=task_store,
        invoke=invoker,
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
        # Phase 7F.5：派发时固化「这次动作归哪个 Runtime 实例」（只解决关联唯一性）。
        runtime_instance_of=instance_of,
        # Phase 7F.5.1 §三.1/§四：派发**之前**主动做一次身份探测 —— 拿不到可信身份就
        # 不能在只按 action_id 关联的前提下把外部动作发出去（fail-closed）。
        identity_probe=identity_probe,
        label_of=default_step_label,
        logger=logger,
    )
    bridge.set_task_authorizer(runtime.authorize_step)
    invoker.task_reader = runtime.get
    return runtime
