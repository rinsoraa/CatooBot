"""迁移演练（migration rehearsal）的**状态判定**与**schema 漂移检测**。

Phase 8G Follow-up 引入三态（no_op / migrated / failed）；Phase 8H Follow-up 把 schema 检测补完整：
只用「版本不变且 added_tables 非空」证明不了没有别的 schema 变化。

**Schema 快照**全部来自真实 SQLite 元数据（`sqlite_master` + `PRAGMA table_info`），规范化后比较：

* 覆盖 table / index / trigger / view 四类对象，外加每张表的列定义；
* 规范化 = 折叠空白、去掉结尾分号（确定性；不做会制造假阴性的过度改写）；
* 明确排除 SQLite 内部对象：`sqlite_%` 名字与 `sqlite_autoindex_%` 自动索引
  （它们跟着 UNIQUE/PRIMARY KEY
  约束走，正常变化不是产品 schema 漂移）；
* 快照采集失败/不完整 -> 抛 :class:`SchemaSnapshotError`，由调用方标成"证据不可用"，
  **绝不静默降级成合法 no_op**；
* 绝不只比表数量（表数量相同不代表 schema 没变）。

**三态契约**（实现、文档、测试一致）：

* ``no_op`` —— 没有执行迁移（版本不变）、前后完整性 ok、没有丢行、**schema 快照可用且没有变化**；
* ``migrated`` —— 版本按预期递增、前后完整性 ok、没有未经允许的数据损失
  （**预期内的 schema 变化不算错**）；
* ``failed`` —— 完整性异常、数据损失、版本倒退、**版本未变却出现 schema 变化**、
  或**必需证据采集失败**。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: 三态（与调用方的退出码约定：只要不是 failed 就是 0）。
STATUS_FAILED = "failed"
STATUS_MIGRATED = "migrated"
STATUS_NO_OP = "no_op"

#: 受监控的 schema 对象类型（sqlite_master.type）。
SCHEMA_OBJECT_TYPES = ("table", "index", "trigger", "view")


class SchemaSnapshotError(RuntimeError):
    """schema 快照采集失败/不完整：调用方必须按"证据不可用"处理，绝不能当成 no_op。"""


def _normalize_sql(sql: object) -> str:
    """把对象定义规范化成可比较的字符串（确定性）。

    只做两件不会制造假阴性的事：折叠所有空白、去掉结尾分号。
    不改变大小写、不改写标识符 —— 过度规范化会把真实变化吃掉。
    """
    text = str(sql or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(";").strip()


def _is_internal(name: str) -> bool:
    """SQLite 内部对象：`sqlite_%` 名字，以及自动创建的 `sqlite_autoindex_%` 索引。"""
    lowered = str(name or "").lower()
    return lowered.startswith("sqlite_")


def schema_snapshot(connection: sqlite3.Connection) -> dict[str, str]:
    """从**真实** SQLite 连接采集规范化 schema 快照（对象名 -> 规范化定义）。

    另外为每张表的每一列记录一条 `column:<表>.<列>`（类型/非空/默认值/主键位），
    这样"新增列"即使没有改动表级 SQL 也能被发现。
    """
    snapshot: dict[str, str] = {}
    try:
        rows = list(
            connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master"
                " WHERE type IN ('table', 'index', 'trigger', 'view')"
            )
        )
    except sqlite3.Error as exc:  # pragma: no cover - 连接层错误
        raise SchemaSnapshotError(f"sqlite_master 读取失败：{exc}") from exc
    tables: list[str] = []
    for row in rows:
        kind = str(row[0] or "")
        name = str(row[1] or "")
        if not name or _is_internal(name):
            continue
        if kind == "table":
            tables.append(name)
        snapshot[f"{kind}:{name}"] = _normalize_sql(row[3])
    for table in sorted(tables):
        try:
            info = list(connection.execute(f"PRAGMA table_info({_quote(table)})"))
        except sqlite3.Error as exc:
            raise SchemaSnapshotError(f"table_info({table}) 读取失败：{exc}") from exc
        if not info:
            raise SchemaSnapshotError(f"table_info({table}) 返回空：schema 快照不完整")
        for column in info:
            cid, cname, ctype, notnull, dflt, pk = (list(column) + [None] * 6)[:6]
            key = f"column:{table}.{cname}"
            snapshot[key] = f"cid={cid};type={ctype};notnull={notnull};dflt={dflt};pk={pk}"
    if not snapshot:
        raise SchemaSnapshotError("schema 快照为空：无法证明没有 schema 变化")
    return dict(sorted(snapshot.items()))


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def schema_fingerprint(snapshot: Mapping[str, str]) -> str:
    """规范化快照的确定性指纹（sha256），用于快速判断"有没有变"。"""
    canonical = json.dumps(
        dict(sorted(snapshot.items())), ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def schema_snapshot_of(path: str | Path) -> dict[str, str]:
    """只读连接打开一个真实数据库文件并采集快照（演练入口用）。"""
    target = Path(path)
    try:
        connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise SchemaSnapshotError(f"无法只读打开 {target}：{exc}") from exc
    try:
        return schema_snapshot(connection)
    finally:
        connection.close()


def schema_diff(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, Any]:
    """规范化快照的差异摘要（只记对象名/方向，不写整库内容）。"""
    keys_before = set(before)
    keys_after = set(after)
    added = sorted(keys_after - keys_before)
    removed = sorted(keys_before - keys_after)
    changed = sorted(key for key in (keys_before & keys_after) if before[key] != after[key])
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_count": len(added) + len(removed) + len(changed),
        "changed_before": schema_fingerprint(before) if before else "",
        "changed_after": schema_fingerprint(after) if after else "",
    }


def rehearsal_status(
    *,
    integrity_before: str,
    integrity_after: str,
    version_before: int,
    version_after: int,
    added_tables: Sequence[str],
    lost_rows: Mapping[str, Any],
    schema_evidence_ok: bool,
    schema_changed: bool,
) -> str:
    """三态判定（纯函数，无 I/O）。契约见模块文档。

    ``schema_evidence_ok`` 是**必需**参数：schema 快照缺失或采集失败时不许给出健康结论。
    ``schema_changed`` 表示前后规范化快照确实有差异（对象新增/删除/定义变化）。
    """
    if integrity_before != "ok" or integrity_after != "ok":
        return STATUS_FAILED
    if lost_rows or int(version_after) < int(version_before):
        return STATUS_FAILED
    if not schema_evidence_ok:
        return STATUS_FAILED
    if schema_changed and int(version_after) == int(version_before):
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
    schema_before: Mapping[str, str] | None = None,
    schema_after: Mapping[str, str] | None = None,
    schema_evidence_ok: bool | None = None,
) -> dict[str, Any]:
    """与既有 JSON 字段**兼容**的报告（新增 schema 证据与差异字段）。

    传了前后快照就用真实差异判定；快照缺失/采集失败时 ``schema_evidence_ok=False``，
    于是状态只能是 ``failed``（绝不静默降级成合法 no_op）。
    """
    if schema_before is not None and schema_after is not None:
        diff = schema_diff(schema_before, schema_after)
        evidence_ok = True if schema_evidence_ok is None else bool(schema_evidence_ok)
    else:
        diff = {"added": [], "removed": [], "changed": [], "changed_count": 0}
        evidence_ok = False if schema_evidence_ok is None else bool(schema_evidence_ok)
    schema_changed = bool(diff.get("changed_count"))
    version_unchanged = int(version_after) == int(version_before)
    status = rehearsal_status(
        integrity_before=integrity_before,
        integrity_after=integrity_after,
        version_before=int(version_before),
        version_after=int(version_after),
        added_tables=added_tables,
        lost_rows=lost_rows,
        schema_evidence_ok=evidence_ok,
        schema_changed=schema_changed,
    )
    return {
        "db": db,
        "version": [int(version_before), int(version_after)],
        "integrity": [integrity_before, integrity_after],
        "tables": [int(tables_before), int(tables_after)],
        "added_tables": list(added_tables),
        "lost_rows": dict(lost_rows),
        "log": list(log),
        "schema_evidence_ok": evidence_ok,
        "schema_changed": schema_changed,
        "schema_changed_without_version_bump": bool(schema_changed and version_unchanged),
        "schema_diff": diff,
        "status": status,
        "ok": status != STATUS_FAILED,
    }
