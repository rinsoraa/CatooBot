# CatooBot WebUI v1.0 · W1 配置矩阵

> WebUI v1.0 的配置审计产物。字段清单来自 `app/config/settings.py` 的真实 pydantic 模型；
> 每个键都实际 grep 过读取方（排除 `app/config/**`、`tests/**`、`docs/**`、`sync_github.py`）。
> 状态取值：**ACTIVE**（运行期被读）/ **ACTIVE_WITH_RESTART**（仅启动/init 读，改后需重启）/
> **CONDITIONALLY_USED**（仅某开关打开时读）/ **DEFINED_BUT_UNUSED**（未找到真实读取方）/
> **LEGACY**（有读取方但语义废弃）/ **UNKNOWN**。审计基线 commit `004fe0a`。

---

## 1. 配置来源与优先级

### 1.1 加载链（真实代码）

`app/config/settings.py:1003-1047 load_config()`：

1. `load_dotenv(PROJECT_ROOT/".env")`（`:1014`，`override=False` → 进程环境变量优先于 .env）
2. 读 `config/config.yaml`（缺失时从 `config.example.yaml` 复制，`:1016-1021`）
3. `read_overrides()` + `deep_merge()`（`:1037-1039`，读 `config/overrides.yaml`）
4. `_apply_env_overrides()`（`:1043`）——仅 10 个 `CATOOBOT_*` 映射（`:43-54`）：
   `CATOOBOT_ONEBOT_ACCESS_TOKEN`、`CATOOBOT_ONEBOT_HOST/PORT/PATH`、`CATOOBOT_BOT_NAME/DEBUG`、
   `CATOOBOT_LOG_LEVEL/COLOR/NARRATE`、`CATOOBOT_DATABASE_URL`
5. `_resolve_models()`（`:1044`）——`models:` 虚拟段的展开（只填空缺，`set_if_blank` `:889-897`），
   展开到 `ai.providers/models`、`memory.*`、`media.*`、`social.decision_model`、`sandbox.decision_model`、
   `agent.planner/evaluator`（`:860-923`）
6. `AppConfig.model_validate()` + `_validate_model_refs()`（`:1045-1046`）

**最终优先级（高→低）**：真实环境变量 / `.env`（10 项映射） > `config/overrides.yaml`（WebUI 写入） >
`config/config.yaml` > `models:` 虚拟段展开 > pydantic 默认值。

### 1.2 所有写盘位置

| 位置 | 代码 | 写什么 | 原子/备份 |
|---|---|---|---|
| `config/overrides.yaml` | `settings.py:844-857`（tmp + `Path.replace`，**原子**）；调用 `config_admin.py:184-190`（表单）/`:202-210`（raw）；`:192-200` reset 直接 unlink | WebUI 覆盖（含 header 注释） | 原子，无备份 |
| `config/config.yaml` | `settings.py:1017-1021` `shutil.copyfile(example, path)` | 首次启动复制示例 | 非原子，无备份，仅文件不存在时 |
| `.env` | `config_admin.py:151-174 set_env_secret` → `path.write_text`（**:171 非原子**）+ `os.environ[name]=value`（`:172`）；唯一调用点 `:452`（OneBot Token） | `NAME=value` 追加/替换 | **非原子，无备份** |
| SQLite `settings` 表 | `services/behavior.py:144`（`behavior_overrides`）、`services/social.py:259`（`social_overrides`）、`persona_manager.py:62`（`active_persona`）、`services/admin.py:253/336`（`model_overrides`）、`admin.py:295-298`（`prompt_overrides`）、`tools/runtime.py:209-221`（`tool_configs`）、`social/feedback.py:135` | 行为/社交/角色/模型/提示词/工具覆盖 | 数据库事务 |
| `data/secrets.json` | `tools/credentials.py:80-90` + `_save :118-124`（`write_text` + chmod 600 尽力） | 工具凭据 | 非原子，无备份 |

> v1.0 要求：凭据写入必须原子替换 + 保留既有行 + 不破坏 `.env` 其他配置；Key 只经 masked 状态返回。

---

## 2. 全字段矩阵

`providers.<n>` / `models[i]` / `configs.<t>` 为字典/列表通配行。
「现有 WebUI 入口」中 `raw` 表示只能通过 `/config?tab=raw`（overrides.yaml）编辑。

| key | 类型 | 默认 | 建议中文标签 | 状态 | 读取证据（file:line） | 热改 | 需重启 | 现有入口 | v1.0 区 | 级别 |
|---|---|---|---|---|---|---|---|---|---|---|
| bot.name | str | CatooBot | 机器人名称 | ACTIVE | `core/bot.py:317` | Y | N | config/basic | 系统-基础 | basic |
| bot.debug | bool | True | 调试模式 | ACTIVE | `core/router.py:40` | Y | N | config/basic | 系统-基础 | advanced |
| bot.command_prefix | str | / | 命令前缀 | ACTIVE_WITH_RESTART | `core/bot.py:301` | N | Y | config/basic | 系统-基础 | expert |
| onebot.enabled | bool | True | 启用 QQ 接入 | CONDITIONALLY_USED（gateway_enabled） | `core/bot.py:263` | N | Y | raw | 系统-QQ | basic |
| onebot.host | str | 127.0.0.1 | QQ 监听地址 | ACTIVE_WITH_RESTART | `adapters/onebot_v11/server.py:89` | N | Y | config/onebot | 系统-QQ | basic |
| onebot.port | int | 8080 | QQ 监听端口 | ACTIVE_WITH_RESTART | `server.py:90` | N | Y | config/onebot | 系统-QQ | basic |
| onebot.path | str | /onebot/v11/ws | WebSocket 路径 | ACTIVE_WITH_RESTART | `server.py:129` | N | Y | config/onebot | 系统-QQ | basic |
| onebot.access_token | str | "" | 访问令牌 | ACTIVE_WITH_RESTART | `server.py:137` | N | Y | config/onebot（写 .env） | 凭据 | basic |
| onebot.api_timeout | float | 10.0 | QQ API 超时(秒) | ACTIVE_WITH_RESTART | `server.py:164` | N | Y | config/onebot | 系统-QQ | advanced |
| onebot.gateway_enabled | bool | False | 真实外部网关 | ACTIVE_WITH_RESTART | `core/bot.py:264` | N | Y | raw | 系统-QQ | expert |
| onebot.self_ids | list[str] | [] | 本机 QQ 白名单 | CONDITIONALLY_USED（gateway_enabled） | `integrations/onebot/gateway.py:365` | N | Y | raw | 系统-QQ | expert |
| onebot.dedupe_ttl | float | 600.0 | 去重 TTL(秒) | CONDITIONALLY_USED | `gateway.py:218` | N | Y | raw | 高级 | expert |
| onebot.dedupe_max_size | int | 2048 | 去重容量 | CONDITIONALLY_USED | `gateway.py:219` | N | Y | raw | 高级 | expert |
| onebot.max_pending_per_lane | int | 20 | 单会话待处理上限 | CONDITIONALLY_USED | `gateway.py:424` | N | Y | raw | 高级 | expert |
| onebot.outbound_max_retries | int | 3 | 发送重试次数 | CONDITIONALLY_USED | `gateway.py:579` | N | Y | raw | 高级 | expert |
| onebot.reconnect_max_seconds | float | 30.0 | 重连退避上限(秒) | CONDITIONALLY_USED | `gateway.py:295` | N | Y | raw | 高级 | expert |
| onebot.shutdown_timeout | float | 5.0 | 关闭收尾预算(秒) | CONDITIONALLY_USED | `gateway.py:268` | N | Y | raw | 高级 | expert |
| logging.level | str | INFO | 日志级别 | ACTIVE | `main.py:33`；`config_admin.py:262` | Y | N | config/basic | 系统-日志 | basic |
| logging.log_dir | str | logs | 日志目录 | ACTIVE | `main.py:34`；`services/admin.py:302` | 部分 | Y（handler 不重建） | raw | 系统-日志 | advanced |
| logging.color | bool | True | 彩色终端 | ACTIVE | `main.py:35` | Y | N | config/basic | 系统-日志 | basic |
| logging.narrate | bool | True | 内心播报 | ACTIVE | `main.py:36` | Y | N | config/basic | 系统-日志 | basic |
| logging.narrate_world_ticks | bool | False | 每 tick 世界播报 | ACTIVE | `core/bot.py:247` | Y | N | config/basic | 系统-日志 | advanced |
| logging.narrate_thinking | bool | True | 显示模型思考 | ACTIVE | `main.py:37` | Y | N | raw | 系统-日志 | advanced |
| logging.watchdog_enabled | bool | True | 事件循环看门狗 | ACTIVE_WITH_RESTART | `core/bot.py:83` | N | Y | raw | 系统-日志 | expert |
| logging.watchdog_interval_seconds | float | 1.0 | 看门狗间隔(秒) | ACTIVE_WITH_RESTART | `core/bot.py:79` | N | Y | raw | 高级 | expert |
| logging.watchdog_threshold_ms | int | 500 | 卡顿告警阈值(ms) | ACTIVE_WITH_RESTART | `core/bot.py:80` | N | Y | raw | 高级 | expert |
| logging.watch_config_enabled | bool | True | 监听 config 热重载 | ACTIVE_WITH_RESTART | `core/bot.py:93` | N | Y | raw | 高级 | advanced |
| logging.watch_config_interval_seconds | float | 2.0 | 监听轮询(秒) | ACTIVE_WITH_RESTART | `core/bot.py:90` | N | Y | raw | 高级 | expert |
| database.url | str | sqlite:///data/catoobot.db | 数据库地址 | ACTIVE_WITH_RESTART | `core/bot.py:73,118` | N | Y | raw | 系统-基础 | advanced |
| permissions.superusers | list[str] | [] | 超级管理员 QQ | ACTIVE | `permissions/manager.py:19` | Y | N | config/basic | 系统-基础 | basic |
| permissions.admins | list[str] | [] | 管理员 QQ | ACTIVE | `permissions/manager.py:20` | Y | N | config/basic | 系统-基础 | basic |
| ai.enabled | bool | False | 启用 AI | ACTIVE | `ai/engine.py:84` | Y | N | config/ai | AI与模型 | basic |
| ai.system_prompt | str | 内置中文 | AI 系统提示词 | ACTIVE | `ai/engine.py:209` | Y | N | raw | AI与模型 | advanced |
| ai.default_temperature | float | 0.8 | 默认温度 | ACTIVE | `ai/engine.py:251` | Y | N | config/ai | AI与模型 | basic |
| ai.timeout | float | 60.0 | 请求超时(秒) | ACTIVE | `ai/engine.py:121` | Y | N | config/ai | AI与模型 | basic |
| ai.max_tokens | int | 0 | 最大输出 tokens(0=不限) | ACTIVE | `ai/engine.py:233` | Y | N | raw | AI与模型 | advanced |
| ai.context.enabled | bool | True | 启用短期上下文 | ACTIVE | `ai/context.py:37` | Y | N | raw | AI与模型 | advanced |
| ai.context.max_messages | int | 20 | 上下文条数 | ACTIVE | `ai/context.py:73` | Y | N | raw | AI与模型 | basic |
| ai.cooldown.rate_limit_seconds | float | 30.0 | 限流冷却(秒) | ACTIVE | `ai/engine.py:64` | Y | N | config/ai | AI与模型 | advanced |
| ai.cooldown.server_error_seconds | float | 10.0 | 5xx 冷却(秒) | ACTIVE | `ai/engine.py:65` | Y | N | config/ai | AI与模型 | advanced |
| ai.cooldown.retry_backoff_seconds | float | 0.5 | 重试退避基数(秒) | ACTIVE | `ai/engine.py:66` | Y | N | raw | 高级 | expert |
| ai.cooldown.retry_backoff_max_seconds | float | 4.0 | 重试退避上限(秒) | ACTIVE | `ai/engine.py:67` | Y | N | raw | 高级 | expert |
| ai.concurrency.max_parallel_per_provider | int | 4 | 单 Provider 并发 | ACTIVE | `ai/engine.py:104` | Y | N | raw | AI与模型 | advanced |
| ai.usage.enabled | bool | True | 记录调用用量 | ACTIVE_WITH_RESTART | `core/bot.py:97` | N | Y | raw | 高级 | advanced |
| ai.usage.retention_days | int | 30 | 用量保留天数 | ACTIVE | `core/bot.py:603`；`routes/models.py:39` | Y | N | raw | 高级 | advanced |
| ai.providers.<n>.type | str | openai_compatible | 接口类型 | ACTIVE | `ai/engine.py:114` | Y | N | config/ai | AI与模型 | advanced |
| ai.providers.<n>.base_url | str | （无） | 接口地址 | ACTIVE | `ai/engine.py:119` | Y | N | config/ai | AI与模型 | basic |
| ai.providers.<n>.api_key_env | str | "" | Key 环境变量名 | ACTIVE | `ai/engine.py:111` | Y | N | config/ai + credentials | 凭据 | basic |
| ai.models[i].name | str | （无） | 模型别名 | ACTIVE | `ai/engine.py:149` | Y | N | config/ai | AI与模型 | basic |
| ai.models[i].provider | str | （无） | 所属 Provider | ACTIVE | `ai/engine.py:150` | Y | N | config/ai | AI与模型 | basic |
| ai.models[i].model | str | （无） | 服务商模型 ID | ACTIVE | `ai/engine.py:151` | Y | N | config/ai | AI与模型 | basic |
| ai.models[i].enabled | bool | True | 启用该模型 | ACTIVE | `ai/engine.py:152` | Y | N | config/ai | AI与模型 | basic |
| character.timezone | str | Asia/Shanghai | 角色时区 | ACTIVE | `core/bot.py:167`；`config_admin.py:315` | Y | N | raw | 角色 | basic |
| character.identity | dict | {} | 角色身份 | ACTIVE_WITH_RESTART | `character/persona_manager.py:31` | N | Y | /character（存 DB） | 角色 | basic |
| character.personality | dict | {} | 性格 | ACTIVE_WITH_RESTART | `persona_manager.py:31` | N | Y | /character | 角色 | basic |
| character.speaking_style | dict | {} | 说话风格 | ACTIVE_WITH_RESTART | `persona_manager.py:31` | N | Y | /character | 角色 | basic |
| character.behavior_rules | list[str] | [] | 行为守则 | ACTIVE_WITH_RESTART | `persona_manager.py:31` | N | Y | /character | 角色 | advanced |
| character.system_prompt | str | "" | 角色补充 Prompt | ACTIVE_WITH_RESTART | `persona_manager.py:31` | N | Y | /character + /prompts | 角色 | basic |
| memory.enabled | bool | True | 启用长期记忆 | ACTIVE | `core/bot.py:124` | 部分 | Y（off→on 需重建） | config/basic | 记忆 | basic |
| memory.retrieval.top_k | int | 8 | 召回条数 | ACTIVE | `memory/retrieval.py:190` | Y | N | raw | 记忆 | advanced |
| memory.retrieval.weights.semantic | float | 0.40 | 语义权重 | ACTIVE | `retrieval.py:260` | Y | N | raw | 记忆 | expert |
| memory.retrieval.weights.keyword | float | 0.20 | 关键词权重 | ACTIVE | `retrieval.py:261` | Y | N | raw | 记忆 | expert |
| memory.retrieval.weights.importance | float | 0.15 | 重要度权重 | ACTIVE | `retrieval.py:262` | Y | N | raw | 记忆 | expert |
| memory.retrieval.weights.confidence | float | 0.10 | 置信度权重 | ACTIVE | `retrieval.py:263` | Y | N | raw | 记忆 | expert |
| memory.retrieval.weights.recency | float | 0.10 | 新近度权重 | ACTIVE | `retrieval.py:264` | Y | N | raw | 记忆 | expert |
| memory.retrieval.weights.relationship | float | 0.05 | 关系权重 | ACTIVE | `retrieval.py:265` | Y | N | raw | 记忆 | expert |
| memory.retrieval.min_final_score | float | 0.18 | 召回总分下限 | ACTIVE | `retrieval.py:299` | Y | N | raw | 记忆 | expert |
| memory.retrieval.min_relevance | float | 0.05 | 相关性证据下限 | ACTIVE | `retrieval.py:294` | Y | N | raw | 记忆 | expert |
| memory.retrieval.keyword_candidates | int | 20 | 关键词候选数 | ACTIVE | `retrieval.py:219` | Y | N | raw | 高级 | expert |
| memory.retrieval.semantic_candidates | int | 20 | 语义候选数 | ACTIVE | `retrieval.py:228` | Y | N | raw | 高级 | expert |
| memory.retrieval.topic_bonus | float | 0.12 | 话题加成 | ACTIVE | `retrieval.py:365` | Y | N | raw | 记忆 | expert |
| memory.retrieval.cache_ttl_seconds | float | 60.0 | 检索缓存(秒) | ACTIVE | `retrieval.py:196` | Y | N | raw | 高级 | expert |
| memory.extraction.enabled | bool | True | 自动提取记忆 | ACTIVE | `memory/extraction.py:87` | Y | N | config/basic | 记忆 | basic |
| memory.extraction.model | str | "" | 提取模型 | ACTIVE | `extraction.py:121` | Y | N | config/basic | 记忆 | basic |
| memory.extraction.timeout | float | 12.0 | 提取超时(秒) | ACTIVE | `extraction.py:128` | Y | N | config/basic | 记忆 | advanced |
| memory.extraction.min_content_length | int | 4 | 最短可提取长度 | ACTIVE | `memory/manager.py:160` | Y | N | raw | 记忆 | expert |
| memory.semantic.enabled | bool | False | 启用语义检索 | ACTIVE_WITH_RESTART | `core/bot.py:112` | N | Y | raw | 记忆 | advanced |
| memory.semantic.embedding.provider | str | "" | 复用 Provider | ACTIVE_WITH_RESTART | `memory/embedding.py:158` | N | Y | raw | 记忆 | expert |
| memory.semantic.embedding.model | str | "" | Embedding 模型 | ACTIVE_WITH_RESTART | `embedding.py:166,182` | N | Y | raw | 记忆 | advanced |
| memory.semantic.embedding.dimensions | int? | None | 向量维度 | ACTIVE_WITH_RESTART | `embedding.py:184` | N | Y | raw | 高级 | expert |
| memory.semantic.embedding.timeout | float | 10.0 | Embedding 超时 | ACTIVE_WITH_RESTART | `embedding.py:183` | N | Y | raw | 高级 | expert |
| memory.semantic.embedding.base_url | str | "" | 独立 Embedding 地址 | ACTIVE_WITH_RESTART | `embedding.py:156` | N | Y | raw | 记忆 | expert |
| memory.semantic.embedding.api_key_env | str | "" | 独立 Key 变量 | ACTIVE_WITH_RESTART | `embedding.py:157` | N | Y | raw | 凭据 | expert |
| memory.semantic.batch_size | int | 32 | 向量批大小 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 高级 | expert |
| memory.consolidation.enabled | bool | True | 启用记忆巩固 | **DEFINED_BUT_UNUSED** | no reference found（调度只由 `schedule` 驱动） | — | — | raw | 记忆 | advanced |
| memory.consolidation.duplicate_threshold | float | 0.92 | 去重阈值 | ACTIVE | `memory/consolidation.py:205` | Y | N | raw | 记忆 | expert |
| memory.consolidation.conflict_threshold | float | 0.75 | 冲突聚类阈值 | ACTIVE | `consolidation.py:322` | Y | N | raw | 记忆 | expert |
| memory.consolidation.schedule | str | daily | 巩固周期 | ACTIVE | `core/bot.py:137`；`config_admin.py:299` | Y | N | raw | 记忆 | advanced |
| memory.consolidation.compression_min_cluster | int | 5 | 压缩最小簇 | ACTIVE | `consolidation.py:275` | Y | N | raw | 高级 | expert |
| memory.consolidation.compression_use_llm | bool | False | LLM 压缩(预留) | ACTIVE | `consolidation.py:341` | Y | N | raw | 高级 | expert |
| memory.consolidation.max_scan | int | 500 | 单轮扫描上限 | ACTIVE | `consolidation.py:178` | Y | N | raw | 高级 | expert |
| memory.retention.episodic_days | int | 180 | 情景记忆保留天数 | ACTIVE | `consolidation.py:239` | Y | N | raw | 记忆 | advanced |
| memory.policy.enabled | bool | True | 启用记忆配额 | ACTIVE | `memory/manager.py:353` | Y | N | raw | 记忆 | expert |
| memory.policy.max_active_per_user | int | 500 | 单用户活跃上限 | ACTIVE | `manager.py:358` | Y | N | raw | 记忆 | expert |
| memory.policy.max_active_per_group | int | 300 | 单群活跃上限 | ACTIVE | `manager.py:356` | Y | N | raw | 记忆 | expert |
| web.enabled | bool | False | 启用 WebUI | ACTIVE_WITH_RESTART | `core/bot.py:726` | N | Y | raw | WebUI | basic |
| web.host | str | 127.0.0.1 | WebUI 监听地址 | ACTIVE_WITH_RESTART | `core/bot.py:730` | N | Y | config/web | WebUI | basic |
| web.port | int | 8500 | WebUI 端口 | ACTIVE_WITH_RESTART | `core/bot.py:734` | N | Y | config/web | WebUI | basic |
| web.username | str | admin | 登录用户名 | ACTIVE | `web/auth.py:70`；`config_admin.py:468` | Y | N | config/web | WebUI | basic |
| web.password | str | "" | 初始密码 | ACTIVE_WITH_RESTART | `web/auth.py:76` | N | Y | config/web | 凭据 | basic |
| web.password_env | str | CATOOBOT_WEB_PASSWORD | 密码环境变量 | ACTIVE_WITH_RESTART | `web/auth.py:76` | N | Y | raw | 凭据 | advanced |
| behavior.enabled | bool | True | 启用行为引擎 | ACTIVE_WITH_RESTART | `core/bot.py:517` | N | Y | raw | 行为 | basic |
| behavior.reply.enabled | bool | True | 启用延迟回复 | ACTIVE | `response/timing.py:38` | Y | N | /behavior | 行为 | basic |
| behavior.reply.min_delay | float | 0.8 | 最短延迟(秒) | ACTIVE | `timing.py:78` | Y | N | /behavior | 行为 | basic |
| behavior.reply.max_delay | float | 8.0 | 最长延迟(秒) | ACTIVE | `timing.py:78` | Y | N | /behavior | 行为 | basic |
| behavior.reply.per_char_delay | float | 0.045 | 每字延迟(秒) | ACTIVE | `timing.py:56` | Y | N | raw | 行为 | advanced |
| behavior.reply.reading_floor | float | 0.5 | 起读延迟(秒) | ACTIVE | `timing.py:56` | Y | N | raw | 行为 | advanced |
| behavior.reply.fast_exchange_window | float | 90.0 | 快速往来窗口(秒) | ACTIVE | `timing.py:68` | Y | N | raw | 行为 | expert |
| behavior.reply.fast_exchange_factor | float | 0.65 | 快速往来加速 | ACTIVE | `timing.py:71` | Y | N | raw | 行为 | expert |
| behavior.reply.busy_factor | float | 1.6 | 忙碌减速系数 | ACTIVE | `timing.py:59` | Y | N | raw | 行为 | advanced |
| behavior.reply.sleeping_factor | float | 2.2 | 睡眠减速系数 | ACTIVE | `timing.py:61` | Y | N | raw | 行为 | advanced |
| behavior.reply.night_factor | float | 1.15 | 夜间减速系数 | ACTIVE | `timing.py:63` | Y | N | raw | 行为 | advanced |
| behavior.reply.close_relationship_factor | float | 0.85 | 亲密加速系数 | ACTIVE | `timing.py:65` | Y | N | raw | 行为 | advanced |
| behavior.reply.jitter | float | 0.2 | 随机抖动 | ACTIVE | `timing.py:74-75` | Y | N | raw | 行为 | advanced |
| behavior.chunking.enabled | bool | True | 启用自然分段 | ACTIVE | `response/splitter.py:67` | Y | N | /behavior | 行为 | basic |
| behavior.chunking.chunk_probability | float | 0.20 | 分段概率 | ACTIVE | `splitter.py:91` | Y | N | /behavior | 行为 | basic |
| behavior.chunking.max_chunks | int | 3 | 最多段数 | ACTIVE | `splitter.py:50,80` | Y | N | /behavior | 行为 | basic |
| behavior.chunking.min_chunk_length | int | 6 | 最短段长 | ACTIVE | `splitter.py:80,86` | Y | N | raw | 行为 | advanced |
| behavior.chunking.paragraph_always_split | bool | True | 段落必拆 | ACTIVE | `splitter.py:79` | Y | N | raw | 行为 | advanced |
| behavior.chunking.inter_chunk_delay_min | float | 0.6 | 段间最小间隔(秒) | ACTIVE | `response/planner.py:77` | Y | N | raw | 行为 | advanced |
| behavior.chunking.inter_chunk_delay_max | float | 2.0 | 段间最大间隔(秒) | ACTIVE | `planner.py:77` | Y | N | raw | 行为 | advanced |
| behavior.schedule.sleep_enabled | bool | True | 启用睡眠时段 | ACTIVE | `behavior/presence.py:114,138` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.sleep_start | str | 00:30 | 入睡时间 | ACTIVE | `presence.py:115,140` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.sleep_end | str | 08:00 | 起床时间 | ACTIVE | `presence.py:116,141` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.dnd_enabled | bool | False | 启用免打扰 | ACTIVE | `presence.py:117,148` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.dnd_start | str | 23:00 | 免打扰开始 | ACTIVE | `presence.py:118,150` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.dnd_end | str | 08:00 | 免打扰结束 | ACTIVE | `presence.py:119,151` | Y | N | /behavior | 行为 | basic |
| behavior.schedule.dnd_blocks_replies | bool | False | 免打扰屏蔽被动回复 | ACTIVE | `presence.py:177` | Y | N | /behavior | 行为 | advanced |
| behavior.schedule.night_start | str | 23:00 | 夜晚开始 | ACTIVE | `presence.py:158` | Y | N | raw | 行为 | advanced |
| behavior.schedule.night_end | str | 06:00 | 夜晚结束 | ACTIVE | `presence.py:159` | Y | N | raw | 行为 | advanced |
| behavior.group.participation_enabled | bool | True | 允许非@插话 | ACTIVE | `social/cognition.py:238` | Y | N | /behavior | 行为 | basic |
| behavior.group.participation_probability | float | 0.04 | 插话频率(legacy 低权重) | ACTIVE | `cognition.py:278` | Y | N | /behavior | 社交 | basic |
| behavior.group.min_message_length | int | 3 | 参与判定最短消息 | ACTIVE | `cognition.py:229` | Y | N | /behavior | 行为 | advanced |
| behavior.initiative.enabled | bool | False | 启用主动聊天 | ACTIVE | `behavior/initiative.py:207`；`scheduler.py:98` | Y | N | /behavior | 社交 | basic |
| behavior.initiative.min_interval_minutes | int | 120 | 主动最小间隔(分) | ACTIVE | `initiative.py:217` | Y | N | /behavior | 社交 | basic |
| behavior.initiative.daily_limit | int | 3 | 每日主动上限 | ACTIVE | `initiative.py:222` | Y | N | /behavior | 社交 | basic |
| behavior.initiative.hourly_limit | int | 1 | 每小时主动上限 | ACTIVE | `initiative.py:220` | Y | N | /behavior | 社交 | basic |
| behavior.initiative.min_relationship_stage | str | familiar | 最低关系阶段 | ACTIVE | `initiative.py:207` | Y | N | /behavior | 社交 | advanced |
| behavior.initiative.idle_hours | float | 6.0 | 闲置触发(小时) | ACTIVE | `initiative.py:171` | Y | N | /behavior | 社交 | advanced |
| behavior.initiative.base_probability | float | 0.35 | 主动基础概率 | ACTIVE | `initiative.py:231` | Y | N | raw | 社交 | advanced |
| behavior.initiative.relationship_bonus | float | 0.2 | 亲密加成 | ACTIVE | `initiative.py:235` | Y | N | raw | 社交 | expert |
| behavior.initiative.topic_bonus | float | 0.35 | 话题加成 | ACTIVE | `initiative.py:233` | Y | N | raw | 社交 | expert |
| behavior.initiative.active_activity_factor | float | 1.0 | 活跃活动因子 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 高级 | expert |
| behavior.initiative.max_unanswered | int | 1 | 未回复追问上限 | ACTIVE | `initiative.py:214` | Y | N | /behavior | 社交 | advanced |
| behavior.initiative.duplicate_similarity | float | 0.6 | 重复相似度阈值 | ACTIVE | `initiative.py:270` | Y | N | raw | 社交 | expert |
| social.enabled | bool | True | 启用社交认知 | ACTIVE | `social/cognition.py:90` | 部分 | N* | /social/policy | 社交 | basic |
| social.decision_model | str | "" | 决策快模型 | ACTIVE | `social/observer.py:115` | 部分 | N* | /social/policy | 社交 | advanced |
| social.group_context.max_messages | int | 30 | 群上下文条数 | ACTIVE | `cognition.py:58` | 部分 | N* | /social/policy | 社交 | advanced |
| social.continuation.enabled | bool | True | 启用续话识别 | **DEFINED_BUT_UNUSED** | no reference found | — | — | /social/policy | 社交 | advanced |
| social.continuation.window_minutes | int | 10 | 续话窗口(分) | ACTIVE | `social/thread.py:34` | 部分 | N* | /social/policy | 社交 | basic |
| social.continuation.max_messages | int | 8 | 续话最多条数 | **DEFINED_BUT_UNUSED** | no reference found | — | — | /social/policy | 社交 | advanced |
| social.observer.batch_size | int | 5 | 观察批大小 | ACTIVE | `cognition.py:243` | 部分 | N* | /social/policy | 社交 | advanced |
| social.observer.min_context_messages | int | 20 | 观察最少上下文 | ACTIVE | `cognition.py:346` | 部分 | N* | /social/policy | 社交 | advanced |
| social.observer.max_staleness_messages | int | 5 | 过期消息阈值 | **DEFINED_BUT_UNUSED** | no reference found | — | — | /social/policy | 社交 | expert |
| social.participation.daily_limit | int | 30 | 每日参与上限 | ACTIVE | `social/policy.py:74` | 部分 | N* | /social/policy | 社交 | basic |
| social.participation.cooldown_seconds | int | 90 | 参与冷却(秒) | ACTIVE | `policy.py:68` | 部分 | N* | /social/policy | 社交 | basic |
| social.participation.max_consecutive_defer | int | 3 | 连续推迟上限 | ACTIVE | `cognition.py:320` | 部分 | N* | raw | 社交 | expert |
| social.thresholds.follow_up | float | 0.75 | 续话阈值 | ACTIVE | `observer.py:143-145` | 部分 | N* | /social/policy | 社交 | expert |
| social.thresholds.topic_relevance | float | 0.70 | 话题相关阈值 | ACTIVE | `observer.py:145` | 部分 | N* | /social/policy | 社交 | expert |
| social.thresholds.social_fit | float | 0.65 | 社交契合阈值 | ACTIVE | `observer.py:147` | 部分 | N* | /social/policy | 社交 | expert |
| social.thresholds.contribution_value | float | 0.65 | 贡献价值阈值 | ACTIVE | `observer.py:146` | 部分 | N* | /social/policy | 社交 | expert |
| social.attention.enabled | bool | True | 社交注意开关 | **DEFINED_BUT_UNUSED** | no reference found（页面可写但运行时不读） | — | — | /social/policy | 社交 | advanced |
| social.fatigue.enabled | bool | True | 社交疲劳开关 | **DEFINED_BUT_UNUSED** | no reference found | — | — | /social/policy | 社交 | advanced |
| social.topic.enabled | bool | True | 话题跟踪开关 | **DEFINED_BUT_UNUSED** | no reference found | — | — | /social/policy | 社交 | advanced |
| social.observation_retention_days | int | 30 | 观察记录保留天数 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 社交 | advanced |
| social.interaction_episode_timeout_seconds | float | 900.0 | 会话静默上限(秒) | ACTIVE | `sandbox/runtime.py:848` | N | Y | raw | 社交 | expert |
| social.feedback_retention_days | int | 30 | 反馈保留天数 | ACTIVE | `core/bot.py:593` | Y | N | raw | 高级 | advanced |
| tools.enabled | bool | False | 启用工具 | ACTIVE | `core/bot.py:614`；`tools/runtime.py:68` | N | Y | raw | 媒体与能力 | basic |
| tools.decision_mode | str | json | 工具决策模式 | ACTIVE | `core/bot.py:620`；`runtime.py:273` | N | Y | raw | 媒体与能力 | advanced |
| tools.candidate_tools | int | 5 | 候选工具数 | ACTIVE | `tools/router.py:70` | N | Y | raw | 媒体与能力 | advanced |
| tools.default_timeout | float | 10.0 | 工具默认超时 | ACTIVE | `tools/executor.py:167` | N | Y | raw | 媒体与能力 | advanced |
| tools.max_calls_per_turn | int | 3 | 单轮最大调用 | ACTIVE | `router.py:172` | N | Y | raw | 媒体与能力 | advanced |
| tools.max_execution_time | float | 30.0 | 单轮工具总限时 | ACTIVE | `tools/policy.py:64` | N | Y | raw | 媒体与能力 | advanced |
| tools.max_concurrent | int | 4 | 并发工具数 | ACTIVE_WITH_RESTART | `executor.py:76` | N | Y | raw | 高级 | advanced |
| tools.max_retries | int | 1 | 工具重试次数 | ACTIVE | `executor.py:168` | N | Y | raw | 媒体与能力 | advanced |
| tools.cache_enabled | bool | True | 启用工具缓存 | ACTIVE | `executor.py:155` | N | Y | raw | 媒体与能力 | advanced |
| tools.rate_limit.per_user_per_minute | int | 10 | 单用户限流/分 | ACTIVE | `policy.py:130` | N | Y | /tools（DB 覆盖） | 媒体与能力 | advanced |
| tools.rate_limit.per_group_per_minute | int | 20 | 单群限流/分 | ACTIVE | `policy.py:138` | N | Y | /tools（DB 覆盖） | 媒体与能力 | advanced |
| tools.rate_limit.global_per_minute | int | 60 | 全局限流/分 | ACTIVE | `policy.py:126` | N | Y | /tools | 媒体与能力 | advanced |
| tools.permissions.allowed_risk_levels | list[str] | ["low"] | 允许风险等级 | ACTIVE | `policy.py:103` | N | Y | raw | 高级 | expert |
| tools.permissions.default_enabled | bool | True | 工具默认启用 | **DEFINED_BUT_UNUSED** | no reference found（启用来自 DB/registry） | — | — | raw | 高级 | expert |
| tools.configs.<t>.enabled | bool? | None | 单工具启用 | ACTIVE | `policy.py:162` | N | Y | /tools（DB 覆盖） | 媒体与能力 | advanced |
| tools.configs.<t>.timeout | float? | None | 单工具超时 | ACTIVE | `tools/executor.py`（metadata.timeout） | N | Y | /tools | 媒体与能力 | advanced |
| tools.configs.<t>.cache_ttl_seconds | float? | None | 单工具缓存(秒) | ACTIVE | `executor.py:155` | N | Y | /tools | 媒体与能力 | advanced |
| tools.configs.<t>.rate_limit | obj? | None | 单工具限流 | ACTIVE | `policy.py:163` | N | Y | /tools | 媒体与能力 | advanced |
| tools.configs.<t>.settings | dict | {} | 单工具私有配置 | ACTIVE_WITH_RESTART | `tools/runtime.py:93` | N | Y | /tools（DB 覆盖） | 媒体与能力 | advanced |
| agent.enabled | bool | True | 启用 Agent | ACTIVE | `core/bot.py:628`；`agent/goal.py:130` | N | Y | raw | 媒体与能力 | basic |
| agent.autonomy | str | normal | 自主级别 | ACTIVE | `agent/runtime.py:886` | N | Y | raw | 媒体与能力 | advanced |
| agent.mode.simple | bool | True | 允许简单任务 | ACTIVE | `agent/goal.py:129` | N | Y | raw | 媒体与能力 | advanced |
| agent.mode.tool_assisted | bool | True | 允许工具辅助 | ACTIVE | `goal.py:130` | N | Y | raw | 媒体与能力 | advanced |
| agent.mode.multi_step | bool | True | 允许多步任务 | ACTIVE | `goal.py:131` | N | Y | raw | 媒体与能力 | advanced |
| agent.mode.long_running | bool | False | 允许长期任务(预留) | ACTIVE | `goal.py:132` | N | Y | raw | 高级 | expert |
| agent.budget.max_steps | int | 8 | 最大步数 | ACTIVE | `agent/models.py:224` | N | Y | raw | 媒体与能力 | advanced |
| agent.budget.max_tool_calls | int | 6 | 最大工具调用 | ACTIVE | `agent/executor.py:187` | N | Y | raw | 媒体与能力 | advanced |
| agent.budget.max_replans | int | 2 | 最大重规划 | ACTIVE | `agent/runtime.py:360` | N | Y | raw | 媒体与能力 | advanced |
| agent.budget.max_execution_seconds | float | 60.0 | 任务总限时(秒) | ACTIVE | `executor.py:138` | N | Y | raw | 媒体与能力 | advanced |
| agent.budget.max_parallel_tools | int | 3 | 并行工具数 | ACTIVE | `executor.py:112` | N | Y | raw | 高级 | expert |
| agent.planner.model | str | "" | 规划模型 | ACTIVE | `agent/planner.py:259` | N | Y | raw | 媒体与能力 | advanced |
| agent.planner.timeout | float | 30.0 | 规划超时(秒) | ACTIVE | `planner.py:92` | N | Y | raw | 媒体与能力 | advanced |
| agent.evaluator.model | str | "" | 评估模型 | ACTIVE | `agent/evaluator.py:189` | N | Y | raw | 媒体与能力 | advanced |
| agent.evaluator.timeout | float | 20.0 | 评估超时(秒) | ACTIVE | `evaluator.py:193` | N | Y | raw | 媒体与能力 | advanced |
| agent.evaluator.use_llm | bool | False | LLM 评估 | ACTIVE | `evaluator.py:137` | N | Y | raw | 媒体与能力 | advanced |
| agent.background | dict | {"enabled": False} | 后台 Agent(未开放) | **DEFINED_BUT_UNUSED** | 仅 `agent/runtime.py:892` 展示，无功能读取 | — | — | raw | 高级 | expert |
| agent.max_observations_in_context | int | 6 | 回喂观察数 | ACTIVE | `planner.py:115` | N | Y | raw | 媒体与能力 | advanced |
| agent.cancel_phrases | list[str] | 7 条 | 取消话术 | ACTIVE | `agent/goal.py:66` | N | Y | raw | 媒体与能力 | expert |
| agent.pause_phrases | list[str] | 5 条 | 暂停话术 | ACTIVE | `goal.py:67` | N | Y | raw | 媒体与能力 | expert |
| agent.resume_phrases | list[str] | 5 条 | 继续话术 | ACTIVE | `goal.py:68` | N | Y | raw | 媒体与能力 | expert |
| agent.multi_step_markers | list[str] | 16 条 | 多步判定词 | ACTIVE | `goal.py:109` | N | Y | raw | 媒体与能力 | expert |
| media.enabled | bool | True | 启用媒体能力 | ACTIVE | `core/bot.py:666` | N | Y | raw | 媒体与能力 | basic |
| media.vision_model | str | "" | 视觉模型 | ACTIVE_WITH_RESTART | `media/runtime.py:106` | N | Y | raw | 媒体与能力 | advanced |
| media.sticker_dir | str | data/stickers | 表情库目录 | ACTIVE_WITH_RESTART | `runtime.py:111,313` | N | Y | raw | 媒体与能力 | advanced |
| media.media_dir | str | data/media | 媒体缓存目录 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 高级 | expert |
| media.expression_enabled | bool | True | 回复附加表情 | CONDITIONALLY_USED（chat 插件） | `plugins/chat/plugin.py:911` | N | Y | raw | 媒体与能力 | basic |
| media.sticker_cooldown_seconds | int | 60 | 表情冷却(秒) | ACTIVE | `runtime.py:117` | N | Y | raw | 媒体与能力 | advanced |
| media.max_stickers_per_turn | int | 1 | 单轮表情上限 | ACTIVE | `runtime.py:118` | N | Y | raw | 媒体与能力 | advanced |
| media.auto_collect | bool | True | 自动收藏表情 | ACTIVE | `runtime.py:157` | N | Y | raw | 媒体与能力 | advanced |
| media.max_library_size | int | 5000 | 表情库上限 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 媒体与能力 | advanced |
| media.acquisition_model | str | "" | 收藏判定模型 | **DEFINED_BUT_UNUSED** | 仅校验（`settings.py:915,973`），无消费方 | — | — | raw | 媒体与能力 | advanced |
| media.indexer_enabled | bool | True | 启动扫描索引 | ACTIVE | `core/bot.py:666` | N | Y | raw | 媒体与能力 | advanced |
| media.analysis_version | str | v1 | 分析版本 | ACTIVE_WITH_RESTART | `runtime.py:113` | N | Y | raw | 高级 | expert |
| media.background_vision_enabled | bool | True | 后台识图 | ACTIVE | `runtime.py:173` | N | Y | raw | 媒体与能力 | advanced |
| media.background_vision_max_per_hour | int | 20 | 后台识图/小时 | ACTIVE | `runtime.py:175` | N | Y | raw | 高级 | expert |
| media.recognition_timeout_seconds | float | 12.0 | 识别等待(秒) | CONDITIONALLY_USED（chat 插件） | `plugins/chat/plugin.py:390` | N | Y | raw | 高级 | expert |
| media.import_as_sticker | bool | True | 目录文件即表情 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 高级 | expert |
| conversation.debounce.enabled | bool | True | 启用消息合并 | **DEFINED_BUT_UNUSED** | `conversation/runtime.py:233-234` 只读 direct/group ms，窗口恒定生效 | — | — | raw | 行为 | basic |
| conversation.debounce.direct_message_ms | int | 1200 | 私聊合并窗口(ms) | ACTIVE | `conversation/runtime.py:234` | N | Y | raw | 行为 | basic |
| conversation.debounce.group_message_ms | int | 1800 | 群聊合并窗口(ms) | ACTIVE | `runtime.py:234` | N | Y | raw | 行为 | basic |
| continuity.enabled | bool | True | 启用角色延续 | ACTIVE_WITH_RESTART | `core/bot.py:209` | N | Y | raw | 记忆 | basic |
| continuity.recent_emotion_ttl_minutes | int | 90 | 近期情绪 TTL(分) | ACTIVE | `continuity/store.py:67` | N | Y | raw | 记忆 | advanced |
| continuity.current_interest_ttl_hours | int | 12 | 当前兴趣 TTL(时) | ACTIVE | `store.py:64-65` | N | Y | raw | 记忆 | advanced |
| continuity.unfinished_thought_ttl_hours | int | 6 | 未完成想法 TTL(时) | ACTIVE | `store.py:66` | N | Y | raw | 记忆 | advanced |
| continuity.open_loop_ttl_days | int | 14 | 未闭环 TTL(天) | ACTIVE | `continuity/manager.py:211` | N | Y | raw | 记忆 | advanced |
| continuity.micro_event_ttl_minutes | int | 90 | 小事记忆 TTL(分) | ACTIVE | `store.py:317` | N | Y | raw | 记忆 | advanced |
| continuity.max_recent_events | int | 8 | 近期事件上限 | ACTIVE | `manager.py:111` | N | Y | raw | 记忆 | advanced |
| continuity.max_open_loops | int | 12 | 未闭环上限 | ACTIVE | `manager.py:203` | N | Y | raw | 记忆 | expert |
| continuity.shared_experience_min_confidence | float | 0.60 | 共同经历置信下限 | ACTIVE | `manager.py:266` | N | Y | raw | 记忆 | expert |
| sandbox.enabled | bool | True | 启用生活沙盒 | ACTIVE_WITH_RESTART | `core/bot.py:223` | N | Y | raw | 角色 | basic |
| sandbox.tick_seconds | int | 600 | 世界步进上限(秒，legacy 语义) | **LEGACY**（仍被读作步进上限/播报步幅） | `sandbox/runtime.py:929,931,3105` | N | Y | raw | 角色 | expert |
| sandbox.simulation_seed | int | 0 | 模拟随机种子 | ACTIVE_WITH_RESTART | `runtime.py:230` | N | Y | raw | 高级 | expert |
| sandbox.bible_path | str | config/character_bible.md | 人物档案路径 | ACTIVE_WITH_RESTART | `core/bot.py:233` | N | Y | raw | 角色 | advanced |
| sandbox.allow_ai_decisions | bool | True | 允许 AI 裁决 | ACTIVE | `runtime.py:391`；`sandbox/intent.py:197` | N | Y | raw | 角色 | advanced |
| sandbox.reset_on_bible_change | bool | True | 档案变更重置 | ACTIVE_WITH_RESTART | `core/bot.py:406` | N | Y | raw | 角色 | advanced |
| sandbox.max_events_keep | int | 500 | 世界事件保留上限 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 高级 | expert |
| sandbox.snapshot_keep | int | 48 | 快照保留数 | ACTIVE | `runtime.py:2713` | N | Y | raw | 高级 | expert |
| sandbox.max_background_messages_per_day | int | 3 | 主动消息日额度 | ACTIVE | `behavior/scheduler.py:171` | Y | N | raw | 社交 | advanced |
| sandbox.sync_persona_from_bible | bool | True | 档案同步角色页 | ACTIVE_WITH_RESTART | `core/bot.py:428` | N | Y | raw | 角色 | advanced |
| sandbox.core_friend_ids | list/dict | [] | 核心好友 QQ | ACTIVE | `sandbox/relations.py:232` | N | Y | raw | 角色 | advanced |
| sandbox.core_friend_identities | dict | {} | 核心好友映射 | ACTIVE | `relations.py:235` | N | Y | raw | 角色 | advanced |
| sandbox.social_space_map | dict | {} | QQ 群→社交空间 | CONDITIONALLY_USED（gateway_enabled） | `integrations/onebot/gateway.py:397` | N | Y | raw | 高级 | expert |
| sandbox.memory_context_limit | int | 3 | 注入记忆条数 | ACTIVE | `sandbox/cognitive.py:280` | N | Y | raw | 记忆 | advanced |
| sandbox.memory_context_max_chars | int | 600 | 注入记忆字数 | ACTIVE | `cognitive.py:286` | N | Y | raw | 记忆 | advanced |
| sandbox.memory_context_min_score | float | 0.12 | 注入分数下限 | ACTIVE | `cognitive.py:285` | N | Y | raw | 记忆 | expert |
| sandbox.memory_context_require_evidence | bool | True | 注入需证据 | ACTIVE | `cognitive.py:287` | N | Y | raw | 记忆 | expert |
| sandbox.experience_context_limit | int | 3 | 经历注入条数 | ACTIVE | `cognitive.py:300` | N | Y | raw | 记忆 | advanced |
| sandbox.social_context_recent_window_minutes | float | 2880.0 | 共同经历时间窗(分) | ACTIVE | `cognitive.py:166` | N | Y | raw | 社交 | expert |
| sandbox.conversation_max_response_chars | int | 400 | 单条回复字数上限 | ACTIVE | `sandbox/conversation.py:274,384` | N | Y | raw | 角色 | basic |
| sandbox.conversation_model | str | "" | 对话钉用模型 | ACTIVE | `conversation.py:276` | N | Y | raw | AI与模型 | advanced |
| sandbox.decision_model | str | "" | 决策钉用模型 | ACTIVE | `sandbox/intent.py:455` | N | Y | raw | AI与模型 | advanced |
| sandbox.decision_timeout | float | 20.0 | 决策超时(秒) | ACTIVE | `intent.py:329` | N | Y | raw | 高级 | advanced |
| sandbox.decision_min_confidence | float | 0.35 | 决策置信下限 | ACTIVE | `intent.py:470`；`conversation.py:418` | N | Y | raw | 高级 | expert |
| runtime.enabled | bool | True | 启用 Runtime 调度 | ACTIVE_WITH_RESTART | `core/bot.py:289` | N | Y | raw | Runtime | basic |
| runtime.tick_interval_seconds | float | 1.0 | 调度间隔(秒) | ACTIVE | `runtime/scheduler.py:56` | N | Y | raw | Runtime | advanced |
| runtime.max_catchup_seconds | float | 300.0 | 停机追帧上限(秒) | ACTIVE | `scheduler.py:109` | N | Y | raw | Runtime | expert |
| runtime.shutdown_timeout_seconds | float | 5.0 | 关闭等待预算(秒) | ACTIVE_WITH_RESTART | `core/bot.py:851` | N | Y | raw | Runtime | expert |
| expression.enabled | bool | False | 启用口癖学习 | ACTIVE | `expression/learner.py:91`；`context.py:27` | N | Y | raw | 媒体与能力 | advanced |
| expression.learn_max_per_hour | int | 60 | 每小时学习上限 | **DEFINED_BUT_UNUSED** | no reference found | — | — | raw | 媒体与能力 | advanced |
| expression.min_speakers | int | 2 | 最少说话人数 | ACTIVE | `expression/store.py:169` | N | Y | raw | 媒体与能力 | advanced |
| expression.min_occurrences | int | 3 | 最少出现次数 | ACTIVE | `store.py:169` | N | Y | raw | 媒体与能力 | advanced |
| expression.max_patterns_per_group | int | 80 | 单群口癖上限 | ACTIVE | `store.py:212` | N | Y | raw | 媒体与能力 | advanced |
| expression.inject_max_items | int | 3 | 注入口癖条数 | ACTIVE | `expression/context.py:37` | N | Y | raw | 媒体与能力 | advanced |
| expression.inject_max_chars | int | 24 | 注入口癖字数 | ACTIVE | `context.py:39` | N | Y | raw | 媒体与能力 | advanced |
| expression.groups | list[str] | [] | 生效群白名单 | ACTIVE | `learner.py:94` | N | Y | raw | 媒体与能力 | advanced |

> `N*`：社交/媒体/工具/Agent/沙盒等段经 `/config?tab=raw` 保存只写 `overrides.yaml`，
> `ConfigAdminService.apply()`（`config_admin.py:214-230`）不推送这些段 → 需重启；经各自专用页
> （`/social/policy`、`/behavior`、`/tools`、`/models`）保存则写 DB 并热应用。

---

## 3. 汇总

### 3.1 DEFINED_BUT_UNUSED / LEGACY（v1.0 **不得**作为普通用户配置暴露）

| key | 说明 |
|---|---|
| memory.semantic.batch_size | 无任何读取方 |
| memory.consolidation.enabled | 巩固调度只由 `schedule` 驱动 |
| social.continuation.enabled | 页面可写，运行时不读 |
| social.continuation.max_messages | 同上 |
| social.observer.max_staleness_messages | 同上 |
| social.attention.enabled / social.fatigue.enabled / social.topic.enabled | 三开关仅被 `/social/policy` 写入，运行时不读 |
| social.observation_retention_days | 无清理逻辑读取 |
| behavior.initiative.active_activity_factor | 无读取方 |
| tools.permissions.default_enabled | 工具启用实际来自 DB/registry |
| media.media_dir | 目录未使用 |
| media.max_library_size | 无上限校验 |
| media.acquisition_model | 仅启动校验，无消费方 |
| media.import_as_sticker | 无读取方 |
| conversation.debounce.enabled | 窗口恒定生效，无开关判断 |
| sandbox.max_events_keep | 无读取方 |
| expression.learn_max_per_hour | 无读取方 |
| agent.background | 仅策略快照展示，无后台执行 |
| **LEGACY**：sandbox.tick_seconds | 十分钟世界刷新的旧语义；现仅作手动步进上限与播报步幅（`sandbox/runtime.py:929,931,3105`），建议改名或隐藏 |

### 3.2 现有 WebUI 可编辑键（含入口）

| 入口 | 可编辑键 |
|---|---|
| `GET/POST /config?tab=basic`（`pages/config_page.py:72-201`） | bot.name/debug/command_prefix；logging.level/color/narrate/narrate_world_ticks；permissions.superusers/admins；memory.enabled/extraction.enabled/model/timeout |
| `GET/POST /config?tab=ai`（`:204-359`） | ai.enabled/default_temperature/timeout；cooldown.rate_limit_seconds/server_error_seconds；providers.*(type/base_url/api_key_env)；models.*(name/provider/model/enabled) |
| `GET/POST /config?tab=onebot`（`:362-414`） | onebot.host/port/path/api_timeout；access_token→`.env` |
| `GET/POST /config?tab=web`（`:417-444`） | web.username；web.password（PBKDF2 存 DB）；web.host/port |
| `GET/POST /config?tab=raw`（`:447-464`） | 任意键（直接编辑 overrides.yaml；**唯一**能触碰多数专家键的入口） |
| `POST /behavior/settings`（`routes/sandbox.py:511` → `services/behavior.py:106-145`） | behavior.reply/chunking/schedule/group/initiative 主开关与常用阈值（DB 覆盖，热应用） |
| `POST /social/policy`（`services/social.py:213-255`） | social.enabled/decision_model；continuation/observer/participation/thresholds；attention/fatigue/topic.enabled（含 3 个死开关） |
| `/tools` + `POST /api/tools/config` | 单工具 enabled/timeout/cache/settings（存 DB `tool_configs`，非 `config.tools`） |
| `/credentials` + `POST /api/credentials` | 工具凭据 → `data/secrets.json`（与 AppConfig 无关；AI 引擎不读） |
| `/models` + `POST /api/models/override` | 运行时模型启停/优先级（DB `model_overrides`；priority 不被 `restore_model_overrides` 重放，`services/admin.py:259-266`） |
| `POST /character` | persona identity/personality/speaking_style/behavior_rules/system_prompt（DB `active_persona`；`/prompts` 另存 persona prompt 与 `prompt_overrides`） |

### 3.3 热重载 vs 重启

- **保存即生效**（`ConfigAdminService.apply`，`config_admin.py:214-230`）：`ai.*`（重建 AIEngine，`ai/engine.py:128`）、
  `logging.level/log_dir/color/narrate/narrate_thinking/narrate_world_ticks`（`config_admin.py:257-271`）、
  `permissions.superusers/admins`（`:273-276`）、`memory.*`（除 semantic.enabled/embedding.*，`:278-299`）、
  `behavior.*`（`:301-308`）、`character.timezone`（`:310-323`）。
  另有 DB 覆盖路径热应用：`/behavior`（`services/behavior.py:67-81`）、`/social/policy`（`services/social.py:270-281`）。
  动态读取 `bot.config` 的键即时生效：bot.name、bot.debug、social.feedback_retention_days、sandbox.max_background_messages_per_day。
- **必须重启**（组件在启动时捕获子配置，且 apply 不推送）：`onebot.host/port/path/access_token/api_timeout/enabled/
  gateway_enabled/self_ids/dedupe_*/max_pending_per_lane/outbound_max_retries/reconnect_max_seconds/shutdown_timeout`、
  `database.url`、`web.*`、`logging.watchdog_*/watch_config_*`、`tools.*`、`agent.*`、`media.*`、
  `conversation.debounce.*`、`continuity.*`、`sandbox.*`（除 max_background_messages_per_day）、`runtime.*`、
  `expression.*`、`character.identity/personality/speaking_style/behavior_rules/system_prompt`、
  `memory.semantic.enabled/embedding.*`。
  （`RESTART_FIELDS` 声明见 `config_admin.py:35-40`。）

### 3.4 普通用户最需要的键（v1.0 首屏顺序）

| # | key | 一句话说明 |
|---|---|---|
| 1 | ai.enabled | 打开后机器人才会调用大模型聊天 |
| 2 | ai.providers.<n>.base_url | 模型服务接口地址（如 `http://127.0.0.1:7864/v1`） |
| 3 | ai.providers.<n>.api_key_env → 凭据区 | Key 存放的变量名 + **在凭据区直接写 Key**（v1.0 必须支持） |
| 4 | ai.models[i].name/provider/model | 用哪个平台、哪个模型 ID、别名 |
| 5 | ai.default_temperature | 回答活泼程度（0.7~0.9） |
| 6 | ai.timeout | 请求超时（超时自动换备用模型） |
| 7 | onebot.host/port/path | 与 NapCat 反向连接完全一致 |
| 8 | onebot.access_token | QQ 接入令牌 |
| 9 | web.host/port | 后台监听地址与端口 |
| 10 | web.username / web.password | 后台账号（密码在 WebUI 内改） |
| 11 | bot.name | 日志与后台显示的机器人名字 |
| 12 | permissions.superusers | 超级管理员 QQ |
| 13 | memory.enabled | 是否长期记住用户 |
| 14 | memory.extraction.enabled | 是否自动把聊天提炼成记忆 |
| 15 | behavior.reply.enabled/min_delay/max_delay | 回复前等多久 |
| 16 | behavior.group.participation_enabled | 群里不 @ 是否允许偶尔插话 |
| 17 | behavior.initiative.enabled | 是否允许主动找你 |
| 18 | behavior.schedule.sleep_enabled/sleep_start/sleep_end | 睡眠时段 |
| 19 | social.enabled | 群聊“该不该说话”的总开关 |
| 20 | logging.level | 排查问题时的日志详细程度 |
| 21-23 | media.enabled / tools.enabled / agent.enabled | 媒体、工具、Agent 三大能力开关 |
