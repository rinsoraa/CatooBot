# v3.0 设计 · 角色数据导出 / 导入（Task 24）

> 状态：**设计待确认**（P3 规矩：先设计页，确认后编码）
> 相关代码：`app/sandbox/lifecycle.py`（`CHARACTER_TABLES` / `CHARACTER_SETTINGS` / `reset_character` +
> 备份）、`app/database/database.py`（迁移链 / `schema_migrations`）、`app/web/routes/identity.py`（角色页）、
> `app/web/routes/config.py`（配置页）
> 定位：**迁移 19（删 JSON 列）的保险**，同时是一个有用户价值的功能（换机、迁移、抢救、分享角色）

## 1. 问题

角色的全部"人生"都在 `data/catoobot.db` 里。今天想搬走/存档/出事前留底，只有两条路：
复制数据库文件（**WAL 下不安全**，且跨 schema 版本无保证）或手工写 SQL。
而一旦某个迁移出错（比如将来删 `memories.vector` 的 JSON 列），**没有一条可回滚的数据路径**。

## 2. 目标与非目标

**目标**：一条**可移植、可校验、可回滚**的角色数据通路——导出成单一文件，导入到任意同版本（或可通过迁移链
升到同版本）的库；导入前自动备份、先干跑、失败不动原数据。

**非目标（明确不做）**：
- **不是数据库文件复制**：导出的是**行数据**（JSON），经迁移链适配，可跨 schema 版本；
- **不导出平台数据**：`users`/`groups`/`messages`/会话、QQ 身份、OneBot/模型/tool/WebUI 配置一律不动；
- **不导出任何密钥**：`.env`、provider key、WebUI 密码哈希、凭据（`data/secrets.json`）都不进导出文件；
- 不做云端/网络传输（就是本地文件，用户自己保管）。

## 3. 导出内容（范围 = 角色域，单一真源）

范围直接复用 `app/sandbox/lifecycle.py` 的**两个常量**（这是"重置会清掉什么"的唯一定义，
导出必须与它逐字一致，避免两套清单漂移）：

- `CHARACTER_TABLES`：memories / memory_embeddings / relationships / interaction_profiles /
  shared_experiences / open_loops / micro_events / affective_events / conversation_turns /
  conversations / agent_* / sandbox_* 等；
- `CHARACTER_SETTINGS`：`active_persona` / `character_continuity` / behavior/prompt/model overrides 等；
- 附带**元数据**：`schema_version`（导出时的 `MAX(schema_migrations.version)`）、导出时间、
  角色档案版本（`character_bible` 的 `source_hash`）、每张表行数与整包 sha256。

**格式**（单文件、UTF-8、可读 JSON；附件另计）：

```json
{
  "format": "catoobot-character-export",
  "format_version": 1,
  "exported_at": 1760000000,
  "schema_version": 18,
  "bible_hash": "8f3a…",
  "counts": {"memories": 0, "relationships": 3, "…": 0},
  "tables": {"memories": [{"id": 1, "content": "…"}], "memory_embeddings": []},
  "settings": {"active_persona": {…}}
}
```

- **向量**：`memory_embeddings.vector_blob` 以 base64 导出（保留 blob 与 norm），同时保留 `vector`(JSON)
  ——两条路都在，导入端按当前 schema 决定写哪些列（迁移 19 之后仍能导入旧包）。
- **表情包/图片**：默认**只导出库内元数据**；文件（`data/stickers/**`）作为可选附件（`include_files=true`
  时打包成 `.zip`：`manifest.json` + `tables/` + `files/<sha256>.<ext>`），默认关闭以保持导出轻量。

## 4. 导入流程（安全第一）

1. **校验**：格式、`format_version`、sha256、`schema_version ≤ 当前`（更高版本拒绝，提示先升级代码）；
2. **干跑**（`dry_run=true`，默认）→ 返回差异报告：每张表将写入/覆盖多少行、将影响哪些 setting、
   是否需要先重置；
3. **自动备份**：复用 `CharacterLifecycleManager` 的备份（`data/character_reset_backup/reset_<ts>.json`），
   写入前先生成，路径写进结果；
4. **单事务写入**：`reset_character(confirm=True)`（清角色域）→ 按表插入导出行 → 失败整体回滚
   （SQLite 事务），**绝不留半套数据**；
5. **导入后自检**：行数与 `counts` 比对、抽样检索一次（关键词+语义各一条）确认可用；
6. **幂等**：同一包重复导入结果一致（先清后写）。

## 5. 接口

- **核心**：`app/sandbox/transfer.py` — `CharacterDataTransfer(database, backup_dir=…)`：
  `export(include_files=False) -> dict`、`write_export(path, include_files=False) -> ExportResult`、
  `inspect(path) -> dict`、`import_character(path, *, confirm=False, dry_run=True) -> ImportReport`。
  （导出/导入都在 `to_thread` 之外只做 DB 读写，文件 IO 走线程，绝不阻塞事件循环。）
- **WebUI**：`/character` 页新增「数据」卡（导出=下载按钮；导入=文件上传 + **先干跑**展示差异 + 二次确认 +
  危险提示"会覆盖当前角色，平台数据不受影响"），走 `AdminService`（不在路由里直连核心）。
- **CLI**：`python -m app.sandbox.transfer export|inspect|import <file>`（运维在停机时用；带 `--confirm` 才真写）。

## 6. 验收（每条都可测）

1. 导出→导入到**空库**：`CHARACTER_TABLES` 每张表行数一致、setting 一致、抽样检索结果一致；
2. 导出→导入到**已有别的角色数据**的库：旧数据被清、新数据完整（且备份文件存在）；
3. **失败不破坏**：故意在导入中途塞一条非法行 → 事务回滚，原数据逐行不变（对比导入前后快照）；
4. **不含密钥**：扫描导出文件，`.env` 里的真实值（token/password/key）零命中；`users/groups` 表零出现；
5. **跨版本**：用"迁移前格式"的包（`vector` JSON、无 blob）导入当前代码 → 走回填，检索可用（与 Task 12 的
   预演同一套路）；
6. 干跑不写库：`dry_run` 前后数据库文件哈希不变；
7. 大包的耗时与内存有上限（万级记忆导出 < 数秒；流式写文件，不把整库读进内存两次）。

## 7. 里程碑（每步独立提交 + 独立验收）

1. `CharacterDataTransfer.export/inspect` + `write_export` + CLI `export|inspect`（含 4、7 的测试）
2. `import_character`（dry-run → 备份 → 事务 → 自检；含 1、2、3、5、6 的测试）
3. WebUI `/character` 「数据」卡（导出下载 / 导入上传 + 干跑预览 + 二次确认）
4. **打包三件套**（成本极低，一起收）：`LICENSE`（用户选定）、`EULA`/使用条款要点、
   `CHANGELOG.md`（按会话/dev 版本整理）、把 v2.0 设计规格公开到 `docs/`（README 里 § 编号的引用指向它）

## 8. 与既有体系的关系

| 体系 | 关系 |
|---|---|
| `CharacterLifecycleManager.reset_character` | **复用**：清空与备份都走它，不重写第二套 |
| `CHARACTER_TABLES` / `CHARACTER_SETTINGS` | **唯一真源**：导出范围与重置范围逐字一致 |
| Task 14 outbox | 不参与：导出/导入是显式运维动作，失败要**报错并回滚**，不是"稍后重试" |
| 迁移 19（删 JSON 列） | 本任务先交付，作为它的保险：导出包同时含 blob 与 JSON，删列后仍可导入 |
| 密钥纪律 | 硬约束：导出文件里**永不**出现 `.env` 的任何值（测试断言） |
