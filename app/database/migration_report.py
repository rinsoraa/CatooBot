"""迁移演练（migration rehearsal）的**状态判定**（Phase 8G Follow-up F4）。

背景：演练入口原先把「迁移后 schema 版本必须上升」也算进 ``ok``，于是**没有待执行迁移**的
合法 no-op 会被报成 ``ok:false`` + 退出码 1，看起来像迁移失败。

判定只依赖**真实快照**（版本/完整性/表行数）与迁移日志，绝不含固定成功值：

* ``failed`` —— 迁移后完整性不是 ok、行数减少、或版本**倒退**（真异常仍会向上抛，不产生报告）；
* ``migrated`` —— 版本上升（真的执行了迁移）；
* ``no_op`` —— 以上都不是且版本未变（没有待执行迁移，是**合法 no-op**）。

``ok`` 只回答「演练本身是否健康结束」，不再要求版本必须上升；``status`` 让调用方能区分
「无迁移可执行」与「迁移失败」。未执行的检查不会被写成通过（每个字段都来自真实快照）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: 三态（与调用方的退出码约定：只要不是 failed 就是 0）。
STATUS_FAILED = "failed"
STATUS_MIGRATED = "migrated"
STATUS_NO_OP = "no_op"


def rehearsal_status(
    *,
    integrity_before: str,
    integrity_after: str,
    version_before: int,
    version_after: int,
    added_tables: Sequence[str],
    lost_rows: Mapping[str, Any],
) -> str:
    """三态判定（纯函数，无 I/O）。**契约**（Phase 8H H3 收紧）：

    * failed —— 迁移**前或后**完整性不是 ok；行数减少；版本**倒退**；
      或者**版本没变却观察到表结构变化**（未受托管的 schema 变更不能当成合法 no-op）；
    * migrated —— 版本上升（真的执行了迁移），且前后完整性 ok、没有丢行；
    * no_op —— 以上都不是且版本未变（没有待执行迁移，**合法 no-op**）。

    integrity_before 参与判定：在一份**本来就损坏**的库上"演练成功"不能算健康结论；
    added_tables / lost_rows 参与判定：表结构与行数保真是可观察的事实，不是只给报告看的装饰。
    """
    if integrity_before != "ok" or integrity_after != "ok":
        return STATUS_FAILED
    if lost_rows or int(version_after) < int(version_before):
        return STATUS_FAILED
    if int(version_after) == int(version_before) and added_tables:
        return STATUS_FAILED
    if int(version_after) > int(version_before):
        return STATUS_MIGRATED
    return STATUS_NO_OP


def rehearsal_report(
    *,
    db: str,
    version_before: int,
    version_after: int,
    integrity_before: str,
    integrity_after: str,
    tables_before: int,
    tables_after: int,
    added_tables: Sequence[str],
    lost_rows: Mapping[str, Any],
    log: Sequence[str],
) -> dict[str, Any]:
    """与既有 JSON 字段**兼容**的报告（新增 status 与 schema_changed_without_version_bump）。

    ok 只回答"演练本身是否健康结束"（status != failed），不再要求版本必须上升；
    版本不变但表结构变化会被如实标成 failed，并给出可诊断的标志位。
    """
    status = rehearsal_status(
        integrity_before=integrity_before,
        integrity_after=integrity_after,
        version_before=int(version_before),
        version_after=int(version_after),
        added_tables=added_tables,
        lost_rows=lost_rows,
    )
    schema_drift = int(version_after) == int(version_before) and bool(added_tables)
    return {
        "db": db,
        "version": [int(version_before), int(version_after)],
        "integrity": [integrity_before, integrity_after],
        "tables": [int(tables_before), int(tables_after)],
        "added_tables": list(added_tables),
        "lost_rows": dict(lost_rows),
        "log": list(log),
        "schema_changed_without_version_bump": schema_drift,
        "status": status,
        "ok": status != STATUS_FAILED,
    }
