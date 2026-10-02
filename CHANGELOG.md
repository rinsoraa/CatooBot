# Changelog

本文件记录 CatooBot 的版本演进。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号见 `pyproject.toml`；日期取自真实提交历史（本仓库 2026-09-30 起）。

## [Unreleased] — v3 线（任务 0–25）

### 记忆
- 关键词候选改走 **SQLite FTS5** 索引（迁移 15，`search_text` 双字切分 + 触发器同步）。
- 向量改为 **float64 blob + 预存范数**（迁移 16，`math.sumprod` 单次点积）：候选池内检索
  由 ~159 ms 降到 ~18 ms；JSON 列保留为回退，删除条件已写入代码注释与文档。
- `compression_use_llm` 的 LLM 压缩真正实现；抽取向上的配额与保留策略未变。
- 数据库写入失败不再丢失：**outbox**（JSONL + fsync + 原子重写）落盘并在恢复后重放（任务 14）。
- **图片记忆**：她看懂的图片会成为可回忆的记忆（`source=vision`），WebUI 记忆页可按来源筛选（任务 21）。
- **抽取再也不能静默失败**（任务 25）：每次抽取输出一行
  `[Memory.Extract] saved=x/y reason=…`；原因分类 `disabled / no_models / empty_content /
  parse_failed / timeout / ai_error / invalid_item`；解析失败时 WARN 附脱敏后的前 200 字；
  空 content 但带 reasoning 的回复单独标注并**重试一次**指定模型（未配置时明确提示
  `memory.extraction.model`）；失败重试保留根因（`reason=empty_content retry=failed`）；
  `saved` 只统计**真正入库**的行；连续 5 次零入库 → 一次 WARN + `/memory/health` 红条 +
  `memories_extracted` / `memory_extract_failed{reason}` 计数。

### AI 与运行时
- 按 `base_url` 的并发闸门、限流/服务端错误退避、逐次调用的用量行（迁移 17，`ai_usage`）。
- 启动校验：模型绑定了不存在的 provider → **ERROR 并列出已知 provider**；
  AI 已启用但没有任何可用模型 → **ERROR + 仪表盘横幅**（任务 25）。
- 事件循环卡顿看门狗（任务 16）；实时播报与状态经鉴权 WebSocket 推送（任务 17）。
- 推理模型只输出思考、导致空回复时：同模型重试一次再故障转移，绝不把空回复交给角色层。
- 相对路径（贴纸库/媒体/日志/插件目录）一律按 `PROJECT_ROOT` 解析：
  从别的工作目录启动也不会换掉数据位置（任务 25）。

### 社交与行为
- **回复效果回流**（任务 20，迁移 18）：每回合记录观察 → 结算
  `engaged / ambient / silence / unknown`（保守极性、只记 0/−1）→ 时间衰减加权的参与度 EMA
  软性反馈给群参与决策；群参与概率改为**确定性额度**（1.0 = 每条都参与）。
- `[Initiative] Gate rejected` 仅在拒绝原因发生变化时记 INFO，其余降为 DEBUG（任务 25）。
- 世界事实注入 + 越界声明审计：沙盒实体标签命中即注入真实数字（含库存）并禁止编造，
  出站再做一次矛盾声明剥离；模型思考内容只上控制台、不落盘。

### 媒体
- **识别 → 判断 → 收藏**：QQ 原生表情/动画表情与普通图片都在"要不要回复"决策之前
  后台识别，识别摘要并入参与判断；配字被当作对方真正想说的话。
- 普通图片永不进入表情库（`looks_like_sticker` 边界）；表情收藏**真正下载文件**
  （sha256 去重 + 平均哈希 + 粘贴检测），收藏判定与视觉摘要共享一次下载。

### 运维与安全
- WebUI 路由清单守卫（97 条）+ 登录限流 + CSRF；`server.py` 拆分为 13 个域路由模块（任务 18）。
- 日志脱敏扩展；测试与运行目录解耦并有"不得写真实数据"的守卫。
- **`scripts/backup_db.py`**：`VACUUM INTO` 在线快照 + `integrity_check` + 逐表行数核对
  （绝不对运行中的 WAL 库做文件拷贝）。
- **`scripts/migrate_db.py`**：对单个库文件预演/应用迁移链，报告版本、新增表、只许增不许减的行数核对。
- **角色数据导出/检查**（任务 24 里程碑 1，`app/sandbox/transfer.py`）：
  `python -m app.sandbox.transfer export|inspect`；单文件 JSON、含 schema 版本 / Bible hash /
  逐表计数 / 内容哈希，流式写入（单块 < 64 KiB）；`inspect` 离线校验格式、哈希与计数；
  不含任何密钥与平台数据（有测试断言）。与迁移 19（删 JSON 列）配套的保险。
- 迁移 14–18 已在真库执行（schema 13 → 18），预演先于真库、备份先于预演。

### 打包
- `LICENSE`（Apache-2.0）、`NOTICE`（角色内容不随代码授权）、`EULA.md`（隐私说明 + AI 声明）。

### 重启后的观察期检查项（本批改动的落地核对）
- 模型页 `ai_usage` 出现 `purpose=social_observer`，且延迟约 3.4s（`models.decision` 指向非推理模型后）。
- `[Watchdog]` 卡顿告警带上了「正在执行：… / 任务栈：…」线索，可直接定位阻塞来源。
- `/social/group` 显示「因 poor_timing 连续 N 次未开口」（defer 只推迟、不否决）。
- 模型页 `ai_empty_finish_length` 停止增长（`ai.max_tokens` 建议 2048 起）。
- `/memory/health` 无红条，`[Memory.Extract]` 无 `parse_failed`。

## [2.0.0] — 2026-10-01 · Character Life Sandbox

- 人物档案 → **Bible 编译器** → Seed：实体/空间/物件/需求/动作/规则/模式；
  有界 tick、中断与恢复、快照与回放、沙盒 WebUI 全家桶、角色重置。
- 删除旧 World runtime 全部代码/测试/界面：活动、主动性与生活片段归沙盒所有；
  `CharacterState` 只能由 `SandboxStateManager` 写入。
- 群参与与主动性并入 `/sandbox/chat`（私聊回复 / 群参与 / 主动回复三块独立配置），
  `/behavior` 重定向；配置段顺序修正，两处配置文件对齐。
- 角色页人设改为从 Bible 编译，支持版本感知的人设同步与热加载。

## [1.2.0] — 2026-09-30 · Human Conversation Runtime + Character Continuity

- 回合运行期：合并/去抖、生成中断、静默判定、分段与延迟模型（区间 + 抖动 + 状态因子）。
- 角色延续：未闭环话题、共同经历、互动画像、情绪惯性。
- 历史时效标记（`[今天凌晨4点]` 式）与出站标记剥离。
- 表情包采集落盘、视觉确认的表情包可收藏、回复会接住"配字"而不是播报收藏。

## [0.1.0] — 2026-09-30 · 从零自研的运行时骨架

- 自研 Event / Message / Segment / Router / Command / Permission / Plugin / Config / DB / Logger，
  **不依赖** NoneBot / NcatBot / MaiBot / AstrBot / Koishi。
- OneBot 11 反向 WebSocket 适配（token 鉴权、单连接接管、断线保活）、
  API 客户端（echo/future/超时/断开清理）、事件总线、命令系统、权限、插件加载、SQLite（WAL）。
- 内置 `/ping` `/help` `/about`；WebUI 起步（登录 + 仪表盘）。
