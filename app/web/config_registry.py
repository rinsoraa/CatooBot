"""配置注册表：把真实 Config schema 描述给 WebUI（WebUI v1.0 · W2 §11-§17）。

它不是第二套配置——值永远来自 `AppConfig`；这里只有"如何理解这个字段"的
元数据（标签/分区/级别/是否热重载/是否需要重启/使用状态），并且：

* 结构从 pydantic 模型推导（``AppConfig.model_fields``），不会与 settings.py 漂移；
* 只有 UI 元数据是显式的（``_BASIC``/``_ADVANCED``/``_UNUSED`` …），
  它们与 W1 的 ``docs/WEBUI_CONFIG_MATRIX.md`` 一一对应；
* ``DEFINED_BUT_UNUSED`` / ``LEGACY`` 默认不出现在普通查询里，但从不隐藏事实。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from app.config.settings import (
    OVERRIDES_PATH,
    PROJECT_ROOT,
    AppConfig,
    read_overrides,
    resolve_models_block,
)

BASIC = "basic"
ADVANCED = "advanced"
EXPERT = "expert"

ACTIVE = "ACTIVE"
ACTIVE_WITH_RESTART = "ACTIVE_WITH_RESTART"
CONDITIONALLY_USED = "CONDITIONALLY_USED"
DEFINED_BUT_UNUSED = "DEFINED_BUT_UNUSED"
LEGACY = "LEGACY"

#: 区域名与 WebUI v1.0 的八个分区一致
AREAS = (
    "总览",
    "角色",
    "AI与模型",
    "社交",
    "记忆",
    "媒体与能力",
    "系统",
    "运行",
)

_SECTION_AREA = {
    "bot": "系统",
    "onebot": "系统",
    "logging": "运行",
    "database": "系统",
    "permissions": "系统",
    "ai": "AI与模型",
    "character": "角色",
    "memory": "记忆",
    "web": "系统",
    "behavior": "角色",
    "social": "社交",
    "media": "媒体与能力",
    "conversation": "角色",
    "continuity": "记忆",
    "runtime": "运行",
    "sandbox": "角色",
    "tools": "媒体与能力",
    "agent": "媒体与能力",
    "expression": "媒体与能力",
}

#: 普通用户第一屏就会用到的键（W1 矩阵 §3.4 的顺序即优先级）
_BASIC = (
    "ai.enabled",
    "ai.providers.",
    "ai.models[",
    "ai.default_temperature",
    "ai.timeout",
    "bot.name",
    "bot.debug",
    "onebot.",
    "logging.level",
    "logging.color",
    "logging.narrate",
    "logging.narrate_world_ticks",
    "permissions.",
    "memory.enabled",
    "memory.extraction.",
    "memory.consolidation.schedule",
    "character.timezone",
    "web.host",
    "web.port",
    "web.username",
    "behavior.reply.enabled",
    "behavior.reply.min_delay",
    "behavior.reply.max_delay",
    "behavior.chunking.enabled",
    "behavior.chunking.chunk_probability",
    "behavior.chunking.max_chunks",
    "behavior.schedule.sleep_",
    "behavior.schedule.dnd_enabled",
    "behavior.schedule.dnd_start",
    "behavior.schedule.dnd_end",
    "behavior.group.participation_enabled",
    "behavior.initiative.enabled",
    "behavior.initiative.min_interval_minutes",
    "behavior.initiative.daily_limit",
    "behavior.initiative.hourly_limit",
    "social.enabled",
    "social.continuation.window_minutes",
    "social.participation.",
    "media.enabled",
    "tools.enabled",
    "agent.enabled",
    "runtime.enabled",
    "continuity.enabled",
    "sandbox.conversation_max_response_chars",
    "sandbox.max_background_messages_per_day",
    "conversation.debounce.",
    "ai.context.max_messages",
)

#: 需要理解后果才改的键（其余默认 expert）
_ADVANCED = (
    "ai.providers.<n>.type",
    "ai.context.enabled",
    "ai.max_tokens",
    "ai.cooldown.rate_limit_seconds",
    "ai.cooldown.server_error_seconds",
    "ai.concurrency.",
    "ai.usage.",
    "onebot.api_timeout",
    "database.url",
    "logging.log_dir",
    "logging.narrate_thinking",
    "logging.watch_config_",
    "memory.retrieval.top_k",
    "memory.retrieval.min_relevance",
    "memory.semantic.embedding.model",
    "memory.consolidation.duplicate_threshold",
    "character.identity",
    "character.personality",
    "character.speaking_style",
    "character.behavior_rules",
    "character.system_prompt",
    "sandbox.bible_path",
    "sandbox.allow_ai_decisions",
    "sandbox.reset_on_bible_change",
    "sandbox.sync_persona_from_bible",
    "sandbox.core_friend",
    "sandbox.conversation_model",
    "sandbox.decision_model",
    "sandbox.memory_context_limit",
    "sandbox.memory_context_max_chars",
    "sandbox.experience_context_limit",
    "sandbox.snapshot_keep",
    "social.decision_model",
    "social.group_context.",
    "social.feedback_retention_days",
    "continuity.",
    "media.sticker_",
    "media.max_stickers_per_turn",
    "media.auto_collect",
    "media.indexer_enabled",
    "media.background_vision_enabled",
    "media.expression_enabled",
    "minecraft.agent.tools",
    "minecraft.agent.confirmation",
    "minecraft.agent.trusted_players",
    "tools.rate_limit.",
    "tools.candidate_tools",
    "tools.decision_mode",
    "tools.default_timeout",
    "tools.max_calls_per_turn",
    "tools.max_execution_time",
    "tools.max_retries",
    "tools.cache_enabled",
    "agent.autonomy",
    "agent.mode.",
    "agent.budget.",
    "agent.planner.",
    "agent.evaluator.",
    "agent.max_observations_in_context",
    "expression.min_speakers",
    "expression.min_occurrences",
    "expression.max_patterns_per_group",
    "expression.inject_",
    "behavior.initiative.min_relationship_stage",
    "behavior.initiative.idle_hours",
    "behavior.initiative.max_unanswered",
    "behavior.schedule.dnd_blocks_replies",
    "behavior.reply.busy_factor",
    "behavior.reply.sleeping_factor",
    "behavior.reply.night_factor",
    "behavior.reply.close_relationship_factor",
    "behavior.reply.jitter",
    "runtime.tick_interval_seconds",
    "web.enabled",
)

#: 无真实读取方（W1 矩阵 §3.1）：默认隐藏，事实仍可查询
_UNUSED = frozenset(
    {
        "memory.semantic.batch_size",
        "memory.consolidation.enabled",
        "social.continuation.enabled",
        "social.continuation.max_messages",
        "social.observer.max_staleness_messages",
        "social.attention.enabled",
        "social.fatigue.enabled",
        "social.topic.enabled",
        "social.observation_retention_days",
        "behavior.initiative.active_activity_factor",
        "tools.permissions.default_enabled",
        "media.media_dir",
        "media.max_library_size",
        "media.acquisition_model",
        "media.import_as_sticker",
        "conversation.debounce.enabled",
        "sandbox.max_events_keep",
        "expression.learn_max_per_hour",
        "agent.background",
    }
)

#: 语义已废弃（仍被读作步进上限）
_LEGACY = frozenset({"sandbox.tick_seconds"})

#: 仅在某个开关打开时才被读取 → (flag key, 人话说明)
_CONDITIONAL = {
    "onebot.enabled": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.self_ids": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.dedupe_ttl": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.dedupe_max_size": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.max_pending_per_lane": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.outbound_max_retries": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.reconnect_max_seconds": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "onebot.shutdown_timeout": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "sandbox.social_space_map": ("onebot.gateway_enabled", "真实外部网关开启后生效"),
    "media.expression_enabled": ("", "聊天插件加载后生效"),
    "media.recognition_timeout_seconds": ("", "聊天插件加载后生效"),
}

#: 保存即生效的键（ConfigAdminService.apply 的真实覆盖面，见 W1 矩阵 §3.3）
_HOT = (
    "ai.",
    "logging.level",
    "logging.color",
    "logging.narrate",
    "logging.narrate_thinking",
    "logging.narrate_world_ticks",
    "logging.log_dir",
    "permissions.",
    "memory.",
    "behavior.",
    "character.timezone",
    "bot.name",
    "bot.debug",
)
_HOT_EXCEPT = ("memory.semantic.", "logging.watch", "logging.watchdog_")

#: 需要重启才生效（组件在启动时捕获了配置）
_RESTART_NOTE = "保存后需要重启 CatooBot 才会生效"

_SENSITIVE_LEAVES = frozenset(
    {"access_token", "password", "token", "secret", "api_key", "webhook_secret"}
)

#: 常见叶子名 → 中文标签
_LABELS = {
    "enabled": "启用",
    "name": "名称",
    "host": "监听地址",
    "port": "端口",
    "path": "路径",
    "url": "地址",
    "base_url": "接口地址",
    "api_key_env": "API Key 环境变量名",
    "model": "模型",
    "provider": "服务商",
    "type": "接口类型",
    "level": "日志级别",
    "timeout": "超时（秒）",
    "debug": "调试模式",
    "default_temperature": "默认温度",
    "max_tokens": "最大输出 tokens",
    "max_messages": "上下文条数",
    "top_k": "召回条数",
    "schedule": "周期",
    "timezone": "时区",
    "username": "登录用户名",
    "password": "登录密码",
    "superusers": "超级管理员",
    "admins": "管理员",
    # ---------------------------------------------------------------- 系统 / 运行
    "command_prefix": "命令前缀",
    "access_token": "访问令牌（在 .env 里设置）",
    "api_timeout": "接口超时（秒）",
    "gateway_enabled": "由沙盒接管 QQ 消息",
    "self_ids": "机器人自己的 QQ 号",
    "dedupe_max_size": "去重记录条数上限",
    "dedupe_ttl": "去重记录存活（秒）",
    "outbound_max_retries": "发送失败重试次数",
    "reconnect_max_seconds": "重连最大退避（秒）",
    "max_pending_per_lane": "单通道待发上限",
    "shutdown_timeout": "关闭等待（秒）",
    "color": "彩色日志",
    "log_dir": "日志目录",
    "narrate": "旁白输出",
    "narrate_thinking": "旁白包含思考过程",
    "narrate_world_ticks": "旁白包含世界心跳",
    "watch_config_enabled": "监听配置文件改动",
    "watch_config_interval_seconds": "配置监听间隔（秒）",
    "watchdog_enabled": "主循环看门狗",
    "watchdog_interval_seconds": "看门狗检查间隔（秒）",
    "watchdog_threshold_ms": "看门狗卡顿阈值（毫秒）",
    "max_catchup_seconds": "重启后最多补算（秒）",
    "tick_interval_seconds": "心跳间隔（秒）",
    "shutdown_timeout_seconds": "关闭等待（秒）",
    "version": "前端版本（v1 / v0.8 回退）",
    "password_env": "密码环境变量名",
    "database": "数据库",
    # ---------------------------------------------------------------- 角色 / 行为
    "behavior_rules": "行为规则",
    "identity": "基础身份",
    "personality": "性格",
    "speaking_style": "说话风格",
    "system_prompt": "系统提示词",
    "min_delay": "最小延迟（秒）",
    "max_delay": "最大延迟（秒）",
    "per_char_delay": "每字打字时间（秒）",
    "busy_factor": "忙碌时倍数",
    "sleeping_factor": "睡着时倍数",
    "night_factor": "深夜时倍数",
    "close_relationship_factor": "亲近关系倍数",
    "fast_exchange_factor": "来回快聊倍数",
    "fast_exchange_window": "快聊判定窗口（秒）",
    "reading_floor": "最短阅读时间（秒）",
    "jitter": "抖动比例",
    "chunk_probability": "分条概率",
    "max_chunks": "最多分几条",
    "min_chunk_length": "分条最小长度",
    "paragraph_always_split": "段落必分条",
    "inter_chunk_delay_min": "分条间隔下限（秒）",
    "inter_chunk_delay_max": "分条间隔上限（秒）",
    "sleep_enabled": "启用睡眠时段",
    "sleep_start": "睡眠开始",
    "sleep_end": "睡眠结束",
    "dnd_enabled": "启用免打扰",
    "dnd_start": "免打扰开始",
    "dnd_end": "免打扰结束",
    "dnd_blocks_replies": "免打扰时连回复也压住",
    "night_start": "深夜开始",
    "night_end": "深夜结束",
    "participation_enabled": "启用群聊参与",
    "participation_probability": "群聊参与概率",
    "min_message_length": "最短可参与消息长度",
    "daily_limit": "每日上限",
    "hourly_limit": "每小时上限",
    "min_interval_minutes": "最小间隔（分）",
    "idle_hours": "闲置多久才主动（小时）",
    "min_relationship_stage": "最低关系门槛",
    "max_unanswered": "未回复上限",
    "base_probability": "基础概率",
    "relationship_bonus": "关系加成",
    "topic_bonus": "话题加成",
    "active_activity_factor": "活动期权重",
    "duplicate_similarity": "重复相似度阈值",
    "allow_ai_decisions": "允许 LLM 参与决策",
    "allow_safe": "允许安全动作",
    "allow_low": "允许低风险动作",
    "allow_medium": "允许中风险动作（会真实修改世界；仍需用户确认）",
    "allow_high": "允许高风险动作",
    "allow_destructive": "允许破坏性动作",
    "ttl_seconds": "有效时长（秒）",
    "max_pending": "上限条数",
    "max_reply_chars": "回复长度上限",
    "trusted_players": "可信玩家名单",
    "bible_path": "人物档案路径",
    "reset_on_bible_change": "档案变更后重建世界",
    "sync_persona_from_bible": "启动时同步人设",
    "simulation_seed": "模拟随机种子",
    "snapshot_keep": "快照保留份数",
    "max_events_keep": "事件保留条数",
    "tick_seconds": "世界心跳（秒，旧语义）",
    "decision_model": "决策模型",
    "decision_timeout": "决策超时（秒）",
    "decision_min_confidence": "决策最低置信度",
    "conversation_model": "对话模型",
    "conversation_max_response_chars": "单条回复字数上限",
    "core_friend_ids": "核心好友 QQ（旧写法）",
    "core_friend_identities": "核心好友 QQ → 档案名",
    "social_space_map": "QQ 群 → 社交空间",
    "max_background_messages_per_day": "背景消息每日额度",
    "trust": "信任",
    "familiarity": "熟悉度",
    "closeness": "亲密感",
    "social_comfort": "社交舒适度",
    "memory_context_limit": "注入记忆条数",
    "memory_context_max_chars": "注入记忆字数上限",
    "memory_context_min_score": "记忆相关性下限",
    "memory_context_require_evidence": "记忆需话题证据",
    "experience_context_limit": "注入经历条数",
    "social_context_recent_window_minutes": "社交上下文窗口（分）",
    # ---------------------------------------------------------------- AI 与模型
    "rate_limit_seconds": "限流冷却（秒）",
    "server_error_seconds": "服务端错误冷却（秒）",
    "retry_backoff_seconds": "重试基础退避（秒）",
    "retry_backoff_max_seconds": "重试最大退避（秒）",
    "max_parallel_per_provider": "单服务商并发上限",
    "retention_days": "用量记录保留天数",
    # ---------------------------------------------------------------- 记忆
    "batch_size": "批处理条数",
    "cache_ttl_seconds": "缓存有效期（秒）",
    "keyword_candidates": "关键词候选条数",
    "semantic_candidates": "语义候选条数",
    "min_relevance": "最低相关性",
    "min_final_score": "最低最终得分",
    "min_content_length": "最短可提取长度",
    "max_active_per_group": "单个话题组上限",
    "max_active_per_user": "单个用户上限",
    "episodic_days": "情景记忆保留天数",
    "compression_min_cluster": "压缩最小簇",
    "compression_use_llm": "用 LLM 做压缩",
    "conflict_threshold": "冲突判定阈值",
    "duplicate_threshold": "重复判定阈值",
    "max_scan": "单轮扫描上限",
    "dimensions": "向量维度",
    "importance": "权重·重要度",
    "recency": "权重·新近度",
    "confidence": "权重·可信度",
    "relationship": "权重·关系",
    "keyword": "权重·关键词",
    "semantic": "权重·语义",
    # ---------------------------------------------------------------- 社交
    "window_minutes": "跟随窗口（分）",
    "max_consecutive_defer": "连续让位上限",
    "cooldown_seconds": "冷却（秒）",
    "min_context_messages": "最少上下文条数",
    "max_staleness_messages": "最大过期条数",
    "topic_relevance": "话题相关性阈值",
    "social_fit": "社交契合阈值",
    "contribution_value": "发言价值阈值",
    "follow_up": "跟进阈值",
    "feedback_retention_days": "反馈保留天数",
    "observation_retention_days": "观察记录保留天数",
    "interaction_episode_timeout_seconds": "互动片段超时（秒）",
    # ---------------------------------------------------------------- 媒体与能力
    "sticker_dir": "表情包目录",
    "media_dir": "媒体文件目录",
    "max_library_size": "库存上限",
    "auto_collect": "自动收集表情",
    "import_as_sticker": "图片自动入库",
    "sticker_cooldown_seconds": "表情冷却（秒）",
    "max_stickers_per_turn": "单轮最多发几张",
    "indexer_enabled": "启用索引器",
    "analysis_version": "图像分析版本",
    "vision_model": "视觉模型",
    "acquisition_model": "获取图片的模型",
    "recognition_timeout_seconds": "识别超时（秒）",
    "background_vision_enabled": "后台视觉扫描",
    "background_vision_max_per_hour": "后台视觉每小时上限",
    "expression_enabled": "口癖学习",
    "learn_max_per_hour": "每小时最多学习",
    "min_occurrences": "最少出现次数",
    "min_speakers": "最少说话人数",
    "max_patterns_per_group": "每组模式上限",
    "inject_max_items": "最多注入条数",
    "inject_max_chars": "最多注入字数",
    "groups": "分组",
    "cache_enabled": "启用工具缓存",
    "candidate_tools": "候选工具",
    "decision_mode": "工具决策模式",
    "max_calls_per_turn": "单轮最多调用",
    "max_concurrent": "并发上限",
    "max_execution_time": "单次执行上限（秒）",
    "max_retries": "重试次数",
    "default_timeout": "默认超时（秒）",
    "default_enabled": "默认允许",
    "allowed_risk_levels": "允许的风险级别",
    "global_per_minute": "全局每分钟上限",
    "per_user_per_minute": "单用户每分钟上限",
    "per_group_per_minute": "单群每分钟上限",
    "rate_limit": "速率限制",
    "settings": "参数",
    "autonomy": "自主程度",
    "background": "后台执行",
    "max_steps": "最多步数",
    "max_replans": "最多重规划次数",
    "no_progress_limit": "连续几次没进展就暂停",
    "max_tool_calls": "最多工具调用",
    "max_parallel_tools": "并行工具上限",
    "max_execution_seconds": "任务总时长上限（秒）",
    "max_observations_in_context": "上下文观察条数",
    "multi_step_markers": "多步触发词",
    "pause_phrases": "暂停口令",
    "resume_phrases": "继续口令",
    "cancel_phrases": "取消口令",
    "simple": "简单任务模式",
    "multi_step": "多步任务模式",
    "tool_assisted": "工具辅助模式",
    "long_running": "长任务模式",
    "use_llm": "用 LLM 评估",
    "auto_start_runtime": "自动启动运行时",
    "runtime_dir": "运行时目录",
    "node_executable": "Node 可执行文件",
    "runtime_port": "运行时端口",
    "perception_enabled": "世界感知",
    "poll_interval_seconds": "轮询间隔（秒）",
    "near_interval_seconds": "近处刷新间隔（秒）",
    "local_interval_seconds": "本地刷新间隔（秒）",
    "extended_interval_seconds": "远处刷新间隔（秒）",
    "connect_timeout_seconds": "连接超时（秒）",
    "startup_timeout_seconds": "启动超时（秒）",
    "request_timeout_seconds": "请求超时（秒）",
    "max_runtime_restarts": "最多重启次数",
    "external_callback_url": "回调地址",
    "external_callback_token": "回调令牌",
    "world_change_block_threshold": "世界变化阻塞阈值",
    "max_distance": "最大距离（格）",
    "max_chase_distance": "最大追逐距离（格）",
    "world_event_cooldown_seconds": "世界事件冷却（秒）",
    # ---------------------------------------------------------------- 连续性 / 会话
    "current_interest_ttl_hours": "当前兴趣有效期（小时）",
    "unfinished_thought_ttl_hours": "未说完的话有效期（小时）",
    "max_open_loops": "悬而未决上限",
    "open_loop_ttl_days": "悬而未决保留天数",
    "max_recent_events": "最近事件条数",
    "micro_event_ttl_minutes": "微事件有效期（分）",
    "recent_emotion_ttl_minutes": "近期情绪有效期（分）",
    "shared_experience_min_confidence": "共同经历最低置信度",
    "direct_message_ms": "私聊合并窗口（毫秒）",
    "group_message_ms": "群聊合并窗口（毫秒）",
}


def _leaf_label(leaf: str) -> str:
    return _LABELS.get(leaf, leaf.replace("_", " "))


#: 每个配置项的说明（整键优先 → 叶子名兜底）。写法：一句话讲清"它控制什么、
#: 什么时候需要动它"，不要复述字段名。
_NOTES: dict[str, str] = {
    # ------------------------------------------------------------ 系统 / 运行
    "bot.name": "机器人在日志与界面里的名字",
    "onebot.enabled": "是否启动 OneBot 反向 WebSocket 服务（QQ 接入）",
    "runtime.enabled": "是否启动主心跳调度器（关闭后没有自主行为）",
    "ai.max_tokens": "单次回复的最大输出长度（部分模型不支持）",
    "ai.context.enabled": "是否给模型附带上下文预算控制",
    "ai.context.max_messages": "送进模型的历史消息条数上限",
    "ai.usage.enabled": "记录 token 用量与调用统计",
    "ai.providers.<n>.type": "服务商协议类型（openai 兼容 / anthropic / …）",
    "ai.providers.<n>.base_url": "服务商 API 地址（自建或中转时改这里）",
    "ai.providers.<n>.api_key_env": "存放该服务商 API Key 的环境变量名（密钥写进 .env）",
    "ai.models[i].name": "模型在链里的唯一名字（其它配置引用它）",
    "ai.models[i].provider": "该模型属于哪个服务商",
    "ai.models[i].model": "服务商侧的真实模型 id（如 deepseek-chat）",
    "ai.models[i].enabled": "是否参与故障转移链（越靠前优先级越高）",
    "memory.retrieval.top_k": "每次检索最多取回几条记忆",
    "memory.semantic.enabled": "是否启用语义（向量）检索——需要配置嵌入模型",
    "memory.semantic.embedding.provider": "嵌入模型所属服务商",
    "memory.semantic.embedding.model": "嵌入模型 id（注意填模型 id，不是别名）",
    "memory.semantic.embedding.timeout": "嵌入调用超时",
    "memory.semantic.embedding.base_url": "嵌入接口地址（留空=用服务商默认）",
    "memory.semantic.embedding.api_key_env": "嵌入服务的密钥环境变量名",
    "memory.consolidation.schedule": "记忆整理的运行周期",
    "memory.policy.enabled": "是否限制活跃记忆总量",
    "behavior.enabled": "对话行为引擎总开关（关=不分条、不延迟、不主动）",
    "social.group_context.max_messages": "每个群保留多少条消息作为上下文",
    "tools.configs.<n>.enabled": "该工具是否启用",
    "tools.configs.<n>.timeout": "该工具的调用超时",
    "tools.configs.<n>.cache_ttl_seconds": "该工具结果的缓存时长",
    "tools.configs.<n>.rate_limit": "该工具自身的速率限制",
    "tools.configs.<n>.settings": "该工具的专有参数（按工具文档填写）",
    "agent.planner.model": "任务规划用的模型",
    "agent.planner.timeout": "规划调用超时",
    "agent.evaluator.model": "任务评估用的模型",
    "agent.evaluator.timeout": "评估调用超时",
    "bot.command_prefix": "命令前缀，默认 /（QQ 侧已不用命令，仅保留兼容）",
    "bot.debug": "调试模式：日志更啰嗦，便于排查",
    "logging.level": "日志级别，越大越安静（DEBUG/INFO/WARNING/ERROR）",
    "logging.color": "控制台是否输出彩色日志",
    "logging.log_dir": "日志文件目录",
    "logging.narrate": "把她的内心与动作旁白打到日志（调试观感用）",
    "logging.narrate_thinking": "旁白里包含模型的思考过程",
    "logging.narrate_world_ticks": "旁白里包含生活世界的心跳",
    "logging.watch_config_enabled": "监听 config.yaml 改动并热加载",
    "logging.watch_config_interval_seconds": "检测配置改动的间隔",
    "logging.watchdog_enabled": "主循环看门狗：卡住时记录归因",
    "logging.watchdog_interval_seconds": "看门狗检查间隔",
    "logging.watchdog_threshold_ms": "超过这个卡顿时长才告警",
    "permissions.superusers": "超级管理员 QQ：拥有全部命令权限",
    "permissions.admins": "管理员 QQ：群管理类命令的权限来源",
    "onebot.host": "OneBot 反向 WebSocket 监听地址（NapCat 连过来）",
    "onebot.port": "OneBot 反向 WebSocket 监听端口",
    "onebot.path": "OneBot 反向 WebSocket 路径",
    "onebot.access_token": "访问令牌；建议放 .env（CATOOBOT_ONEBOT_ACCESS_TOKEN）",
    "onebot.api_timeout": "调用 OneBot 接口的超时",
    "onebot.gateway_enabled": "打开后 QQ 消息直接进入生活沙盒，由她的世界决定回不回",
    "onebot.self_ids": "机器人自己的 QQ 号，用于识别 @自己",
    "onebot.dedupe_max_size": "消息去重表容量，防止 NapCat 重发导致重复回复",
    "onebot.dedupe_ttl": "去重记录的存活时间",
    "onebot.outbound_max_retries": "发送失败时的重试次数",
    "onebot.reconnect_max_seconds": "断线重连的最大退避时间",
    "onebot.max_pending_per_lane": "同一会话通道积压的待发消息上限",
    "onebot.shutdown_timeout": "关闭时等待发送完成的时长",
    "runtime.tick_interval_seconds": "主心跳间隔：所有后台任务按它轮转",
    "runtime.max_catchup_seconds": "重启后最多补算多长时间的世界状态",
    "runtime.shutdown_timeout_seconds": "优雅关闭的总等待时长",
    "web.enabled": "是否启动内置 Web 控制台",
    "web.host": "Web 控制台监听地址",
    "web.port": "Web 控制台端口",
    "web.username": "登录用户名（改名请用「凭据」页，那里会同步数据库账号）",
    "web.password": "登录密码（敏感项：请用「凭据」页或登录后改密）",
    "web.password_env": "密码存放的环境变量名",
    "web.version": "前端版本：v1 为新版；仅排障时回退",
    "database.url": "SQLite 数据库地址；换库等于换一份人格记忆",
    "media.media_dir": "图片/媒体缓存目录",
    "media.sticker_dir": "表情包库目录",
    # ------------------------------------------------------------ 角色 / 行为
    "character.timezone": "她的时区：作息、免打扰、深夜语气的判断基准",
    "character.name": "角色名（旧字段，现以人物档案为准）",
    "character.system_prompt": "人设系统提示词（现由人物档案同步生成）",
    "character.identity": "基础身份（由人物档案生成，改档案即可）",
    "character.personality": "性格（由人物档案生成）",
    "character.speaking_style": "说话风格（由人物档案生成）",
    "character.behavior_rules": "行为规则（由人物档案生成）",
    "behavior.reply.enabled": "是否按人的节奏延迟回复（关=秒回）",
    "behavior.reply.min_delay": "回复前的最短停顿",
    "behavior.reply.max_delay": "回复前的最长停顿",
    "behavior.reply.per_char_delay": "每个字增加的打字时间",
    "behavior.reply.reading_floor": "再短的消息也要读这么久",
    "behavior.reply.busy_factor": "她正在忙（生活里的动作）时回复变慢的倍数",
    "behavior.reply.sleeping_factor": "她睡着时回复变慢的倍数",
    "behavior.reply.night_factor": "深夜时回复变慢的倍数",
    "behavior.reply.close_relationship_factor": "对亲近/核心好友回复更快的倍数（越小越快）",
    "behavior.reply.fast_exchange_factor": "连续快聊时的加速倍数",
    "behavior.reply.fast_exchange_window": "多久算连续快聊",
    "behavior.reply.jitter": "延迟的随机浮动比例，避免机械感",
    "behavior.chunking.enabled": "长回复是否拆成多条发",
    "behavior.chunking.chunk_probability": "触发分条的概率",
    "behavior.chunking.max_chunks": "一条回复最多分几条",
    "behavior.chunking.min_chunk_length": "短于这个长度不分条",
    "behavior.chunking.paragraph_always_split": "有段落时必定分条",
    "behavior.chunking.inter_chunk_delay_min": "分条之间最短停顿",
    "behavior.chunking.inter_chunk_delay_max": "分条之间最长停顿",
    "behavior.schedule.sleep_enabled": "启用睡眠时段（沙盒开启时以沙盒里的睡/醒为准）",
    "behavior.schedule.sleep_start": "睡眠开始时间",
    "behavior.schedule.sleep_end": "睡眠结束时间",
    "behavior.schedule.dnd_enabled": "启用免打扰时段",
    "behavior.schedule.dnd_start": "免打扰开始时间",
    "behavior.schedule.dnd_end": "免打扰结束时间",
    "behavior.schedule.dnd_blocks_replies": "免打扰期间连回复也压住（默认只压主动消息）",
    "behavior.schedule.night_start": "深夜语气段的开始",
    "behavior.schedule.night_end": "深夜语气段的结束",
    "behavior.group.participation_enabled": "是否允许她在群里主动搭话",
    "behavior.group.participation_probability": "每条群消息被搭话的概率（社交认知还会再筛）",
    "behavior.group.min_message_length": "太短的消息不参与",
    "behavior.initiative.enabled": "普通主动聊天总开关（默认关，按需打开）",
    "behavior.initiative.min_interval_minutes": "两次主动之间的最短间隔",
    "behavior.initiative.daily_limit": "普通对象每天最多主动几条",
    "behavior.initiative.hourly_limit": "普通对象每小时最多主动几条",
    "behavior.initiative.idle_hours": "对方安静多久才算「好久没聊」",
    "behavior.initiative.min_relationship_stage": "关系熟到这个档位才会主动找他",
    "behavior.initiative.max_unanswered": "主动发出后对方一直没回，达到这个数就停",
    "behavior.initiative.base_probability": "通过全部硬限制后，真正发出的概率",
    "behavior.initiative.relationship_bonus": "关系越亲近，概率加成",
    "behavior.initiative.topic_bonus": "有没聊完的话题时的概率加成",
    "behavior.initiative.active_activity_factor": "她正忙时主动概率的缩放（1=不受影响）",
    "behavior.initiative.duplicate_similarity": "与上一条主动太像就不发（相似度阈值）",
    "behavior.initiative.core_friend.enabled": "核心好友那一路的独立开关（可与上面的总开关无关）",
    "behavior.initiative.core_friend.min_interval_minutes": "对核心好友两次主动之间的最短间隔",
    "behavior.initiative.core_friend.daily_limit": "对每个核心好友每天最多主动几条",
    "behavior.initiative.core_friend.hourly_limit": "对每个核心好友每小时最多主动几条",
    "behavior.initiative.core_friend.idle_hours": "核心好友安静多久就算「好久没聊」",
    "behavior.initiative.core_friend.max_unanswered": "核心好友一直没回时，发到几条就停",
    "behavior.initiative.core_friend.base_probability": "对核心好友的基础发出概率",
    "behavior.initiative.core_friend.relationship_bonus": "核心好友的关系加成（自然拉满）",
    "behavior.initiative.core_friend.topic_bonus": "核心好友有未聊完话题时的加成",
    "behavior.initiative.core_friend.duplicate_similarity": "对核心好友的重复相似度阈值",
    "sandbox.enabled": "生活沙盒总开关：她自己的世界、作息与自主行为",
    "sandbox.tick_seconds": "世界心跳（旧语义，已废弃，仅保留兼容）",
    "sandbox.simulation_seed": "世界随机种子；固定值让行为可复现",
    "sandbox.bible_path": "人物档案路径（真实档案已被 .gitignore 排除）",
    "sandbox.reset_on_bible_change": "档案变化时重建世界（否则沿用旧世界）",
    "sandbox.sync_persona_from_bible": "启动时用人物档案覆盖 WebUI 人设",
    "sandbox.max_events_keep": "世界事件保留条数",
    "sandbox.snapshot_keep": "世界快照保留份数（重启恢复用）",
    "sandbox.allow_ai_decisions": "允许用 LLM 处理模糊选择（确定性优先）",
    "sandbox.decision_model": "决策用的模型（留空=路由默认）",
    "sandbox.decision_timeout": "决策调用超时",
    "sandbox.decision_min_confidence": "模型置信度低于此值走确定性回退",
    "sandbox.conversation_model": "聊天回复钉住的模型（留空=路由默认）",
    "sandbox.conversation_max_response_chars": "单条回复字数上限，超长直接拒绝重来",
    "sandbox.core_friend_ids": "核心好友 QQ（旧写法：列表仅当档案只有一个核心好友时可用）",
    "sandbox.core_friend_identities": "核心好友 QQ → 档案里的名字（推荐写法，关系表也认它）",
    "sandbox.core_friend_relationship.trust": "核心好友的初始信任（0–1，留空用档案默认）",
    "sandbox.core_friend_relationship.familiarity": "核心好友的初始熟悉度",
    "sandbox.core_friend_relationship.closeness": "核心好友的初始亲密感",
    "sandbox.core_friend_relationship.social_comfort": "核心好友的初始社交舒适度",
    "sandbox.social_space_map": "QQ 群号 → 社交空间 id；未映射的群自动成为 qq:<群号>",
    "sandbox.max_background_messages_per_day": "分享生活类背景消息的每日额度（与主动聊天两套预算）",
    "sandbox.memory_context_limit": "聊天时最多注入几条沙盒记忆（0=关闭该层）",
    "sandbox.memory_context_max_chars": "注入记忆的总字数上限",
    "sandbox.memory_context_min_score": "低于这个相关性的记忆不注入",
    "sandbox.memory_context_require_evidence": "必须有话题证据（关键词/实体）才允许注入",
    "sandbox.experience_context_limit": "聊天时最多注入几条经历",
    "sandbox.social_context_recent_window_minutes": "把最近多久的群聊当作社交上下文",
    # ------------------------------------------------------------ AI 与模型
    "ai.enabled": "是否启用 AI（关闭后她不会说话，只记录）",
    "ai.system_prompt": "全局系统提示词（人设提示是另一层）",
    "ai.default_temperature": "默认温度：越高越随机",
    "ai.timeout": "单次模型调用超时",
    "ai.cooldown.rate_limit_seconds": "命中限流后暂停调用的时长",
    "ai.cooldown.server_error_seconds": "服务端报错后暂停调用的时长",
    "ai.cooldown.retry_backoff_seconds": "失败重试的基础退避",
    "ai.cooldown.retry_backoff_max_seconds": "重试退避的上限",
    "ai.concurrency.max_parallel_per_provider": "同一服务商最多同时几个请求",
    "ai.usage.retention_days": "调用用量记录的保留天数",
    # ------------------------------------------------------------ 记忆
    "memory.enabled": "长期记忆总开关",
    "memory.extraction.enabled": "是否自动从对话里提取记忆",
    "memory.extraction.model": "提取记忆用的模型",
    "memory.extraction.timeout": "提取调用超时",
    "memory.extraction.min_content_length": "太短的内容不提取",
    "memory.retrieval.cache_ttl_seconds": "检索结果缓存时长",
    "memory.retrieval.keyword_candidates": "关键词检索候选条数",
    "memory.retrieval.semantic_candidates": "语义检索候选条数",
    "memory.retrieval.min_relevance": "候选进入排序的最低相关性",
    "memory.retrieval.min_final_score": "最终得分低于此值不注入",
    "memory.retrieval.topic_bonus": "与当前话题重合时的加分",
    "memory.retrieval.weights.importance": "排序权重：重要度",
    "memory.retrieval.weights.recency": "排序权重：新近度",
    "memory.retrieval.weights.confidence": "排序权重：可信度",
    "memory.retrieval.weights.relationship": "排序权重：与该用户的关系",
    "memory.retrieval.weights.keyword": "排序权重：关键词命中",
    "memory.retrieval.weights.semantic": "排序权重：语义相似",
    "memory.semantic.embedding.dimensions": "向量维度（留空用服务商默认；bge 系不传）",
    "memory.semantic.batch_size": "向量化批处理条数",
    "memory.consolidation.enabled": "记忆整理（合并/冲突处理）开关",
    "memory.consolidation.duplicate_threshold": "相似度超过它视为重复并合并",
    "memory.consolidation.conflict_threshold": "相似度超过它视为冲突并处理",
    "memory.consolidation.compression_min_cluster": "至少几簇才做压缩",
    "memory.consolidation.compression_use_llm": "压缩是否用 LLM（关=纯规则）",
    "memory.consolidation.max_scan": "单轮最多扫描多少条",
    "memory.policy.max_active_per_user": "单个用户最多保留多少条活跃记忆",
    "memory.policy.max_active_per_group": "单个群/话题最多保留多少条活跃记忆",
    "memory.retention.episodic_days": "情景记忆保留天数",
    # ------------------------------------------------------------ 社交
    "social.enabled": "社交认知总开关（群里判断要不要搭话）",
    "social.decision_model": "社交判断用的模型",
    "social.continuation.enabled": "是否在对话后继续跟进",
    "social.continuation.window_minutes": "跟进窗口：多久之内算同一轮对话",
    "social.continuation.max_messages": "一轮里最多跟进几条",
    "social.observer.batch_size": "每积累多少条群消息做一次观察",
    "social.observer.min_context_messages": "至少有多少条上下文才观察",
    "social.observer.max_staleness_messages": "观察依据太旧就放弃",
    "social.participation.daily_limit": "她在群里每天最多主动说几句",
    "social.participation.cooldown_seconds": "群聊参与的冷却时间",
    "social.participation.max_consecutive_defer": "连续让位几次后强制允许一次",
    "social.thresholds.topic_relevance": "话题相关性达到多少才考虑参与",
    "social.thresholds.social_fit": "社交契合度阈值",
    "social.thresholds.contribution_value": "发言价值阈值",
    "social.thresholds.follow_up": "跟进所需的阈值",
    "social.attention.enabled": "是否判断「她现在会不会被注意到」",
    "social.fatigue.enabled": "是否考虑群聊疲劳",
    "social.topic.enabled": "是否维护群话题",
    "social.observation_retention_days": "观察记录保留天数",
    "social.feedback_retention_days": "参与反馈保留天数",
    "social.interaction_episode_timeout_seconds": "多久没互动就结束当前互动片段",
    # ------------------------------------------------------------ 媒体与能力
    "media.enabled": "媒体能力总开关",
    "media.indexer_enabled": "图片索引器：为图片生成描述/向量",
    "media.auto_collect": "群里图片自动收集进媒体库",
    "media.import_as_sticker": "收集的图片是否直接进表情包库",
    "media.max_library_size": "媒体库条数上限",
    "media.sticker_cooldown_seconds": "同群发表情的冷却",
    "media.max_stickers_per_turn": "一次回复最多带几张表情",
    "media.vision_model": "识图用的视觉模型",
    "media.acquisition_model": "主动获取图片时用的模型",
    "media.recognition_timeout_seconds": "识图超时",
    "media.analysis_version": "图像分析版本（改变分析口径时才动）",
    "media.background_vision_enabled": "后台扫描群图并理解内容",
    "media.background_vision_max_per_hour": "后台视觉每小时的调用上限",
    "media.expression_enabled": "从对话里学习口癖/表达",
    "media.min_occurrences": "一个说法至少出现几次才学习",
    "media.min_speakers": "至少几个人用才学习",
    "media.learn_max_per_hour": "每小时最多学习多少个表达",
    "media.max_patterns_per_group": "每组最多保留多少表达",
    "media.inject_max_items": "回复时最多注入几条口癖",
    "media.inject_max_chars": "注入口癖的总字数上限",
    "media.groups": "口癖分组定义",
    "tools.enabled": "工具能力总开关",
    "tools.decision_mode": "工具决策模式（规则/模型/混合）",
    "tools.candidate_tools": "进入候选的工具名单（留空=全部启用中的工具）",
    "tools.default_timeout": "工具调用默认超时",
    "tools.max_concurrent": "同时执行几个工具",
    "tools.max_calls_per_turn": "一轮里最多调用几次工具",
    "tools.max_execution_time": "单个工具最长执行时间",
    "tools.max_retries": "工具失败重试次数",
    "tools.cache_enabled": "是否缓存工具结果",
    "tools.rate_limit.global_per_minute": "全部工具的全局速率上限",
    "tools.rate_limit.per_user_per_minute": "单用户速率上限",
    "tools.rate_limit.per_group_per_minute": "单群速率上限",
    "tools.permissions.default_enabled": "没有显式授权时默认允许（关=默认拒绝，更安全）",
    "tools.permissions.allowed_risk_levels": "允许自动执行的风险级别",
    "agent.enabled": "任务代理总开关（多步任务与工具编排）",
    "agent.autonomy": "自主程度：越高越少请示",
    "agent.background": "允许在后台跑任务",
    "agent.decision_model": "代理规划用的模型",
    "agent.max_observations_in_context": "代理上下文里最多几条观察",
    "agent.multi_step_markers": "出现这些词就按多步任务处理",
    "agent.pause_phrases": "聊天里让她暂停任务的说法",
    "agent.resume_phrases": "让她继续任务的说法",
    "agent.cancel_phrases": "让她取消任务的说法",
    "agent.mode.simple": "简单任务模式：一步完成",
    "agent.mode.multi_step": "多步任务模式",
    "agent.mode.tool_assisted": "工具辅助模式",
    "agent.mode.long_running": "长任务模式：可后台持续",
    "task.ttl_seconds": "一个任务最多活多久（秒）",
    "task.max_steps": "计划里最多几步",
    "task.max_replans": "最多重规划几次",
    "task.no_progress_limit": "连续几次没进展就暂停任务",
    "agent.budget.max_steps": "一个任务最多几步",
    "agent.budget.max_replans": "最多重规划几次",
    "agent.budget.max_tool_calls": "一个任务最多工具调用次数",
    "agent.budget.max_parallel_tools": "并行工具上限",
    "agent.budget.max_execution_seconds": "一个任务的最长墙钟时间",
    "agent.evaluator.use_llm": "任务完成度用 LLM 评估",
    "minecraft.enabled": "Minecraft 集成总开关",
    "minecraft.perception_enabled": "世界感知：把游戏世界读成她的感知",
    "minecraft.runtime_dir": "Node 桥接运行时目录",
    "minecraft.node_executable": "Node 可执行文件路径",
    "minecraft.runtime_port": "桥接运行时端口",
    "minecraft.auto_start_runtime": "启动机器人时自动拉起桥接",
    "minecraft.poll_interval_seconds": "世界轮询间隔",
    "minecraft.near_interval_seconds": "她附近区域的刷新间隔",
    "minecraft.local_interval_seconds": "本地范围刷新间隔",
    "minecraft.extended_interval_seconds": "远处范围刷新间隔",
    "minecraft.connect_timeout_seconds": "连接游戏服务器超时",
    "minecraft.startup_timeout_seconds": "桥接启动超时",
    "minecraft.request_timeout_seconds": "桥接请求超时",
    "minecraft.max_runtime_restarts": "桥接最多自动重启几次",
    "minecraft.external_callback_url": "桥接回调地址（Bearer 校验）",
    "minecraft.external_callback_token": "桥接回调令牌（敏感）",
    "minecraft.world_event_cooldown_seconds": "同类世界事件的冷却",
    "minecraft.world_change_block_threshold": "世界变化多大算「被打断」",
    "minecraft.agent.confirmation.ttl_seconds": "一次确认的有效秒数（到点自动失效）",
    "minecraft.agent.confirmation.max_pending": "同时挂起的确认上限（有界内存）",
    "minecraft.agent.chat.enabled": "游戏内玩家说话时，罐头在游戏里接话（USER 回合）",
    "minecraft.agent.chat.max_reply_chars": "游戏内回复最长多少字符",
    "minecraft.agent.trusted_players": "可信 Minecraft 玩家名（LOW 及以上动作只对这些人执行）",
    "minecraft.agent.tools.enabled": "LLM 能不能用 Minecraft 工具（关掉 = 模型完全碰不到游戏）",
    "minecraft.agent.tools.allow_safe": (
        "允许 SAFE 动作：查世界 / 说话 / 朝向 / 停止 / 看背包 / 看掉落物 / 查挖掘能力 / 找方块"
    ),
    "minecraft.agent.tools.allow_low": "允许 LOW 动作：非破坏性移动与跟随（仍需用户明确要求）",
    "minecraft.agent.tools.allow_medium": (
        "允许 MEDIUM 动作：minecraft_dig（挖单方块）/ minecraft_place（放单方块）/ "
        "minecraft_equip（换主手）/ minecraft_inventory_move（搬一格自己的背包）/ "
        "minecraft_container_transfer（搬一格箱子或桶）/ minecraft_craft（做一次配方，"
        "2×2 或给出明确坐标的工作台 3×3）/ minecraft_pickup_item（捡一个明确指定的掉落物，"
        "会自己走过去），"
        "都会改状态且一次只动一个明确物品/槽位/配方/实体；打开后仍需用户在对话里确认"
    ),
    "minecraft.agent.tools.allow_high": "允许 HIGH 动作（还没有 HIGH 级动作；保持关闭）",
    "minecraft.agent.tools.allow_destructive": (
        "允许 DESTRUCTIVE 动作（还没有这一级动作；保持关闭）"
    ),
    "world.timezone": "她那个世界的时区（活动时段与「今天」按它算；默认 Asia/Singapore）",
    "world.activity.enabled": "世界活动（Episode 生命周期）：关掉 = 不再记录/推进她在做什么",
    "world.activity.persistence_interval_seconds": "活动观察多久落一次盘（重大状态变化随时落盘）",
    "world.activity.recovery_grace_seconds": "重启宽限：计划结束时间离现在这么近就不算过期",
    "world.activity.recent_episode_limit": "最近活动读几条（对话上下文与界面默认窗口）",
    "world.activity.transition_window_minutes": (
        "活动结束前多久进入「准备换活动」窗口（窗口内只待命，不提前切）"
    ),
    "world.activity.max_extensions_per_episode": "一条活动最多自动延长几次（防无限续命）",
    "world.activity.bounce_cooldown_minutes": "刚做过的活动多久内不许立刻回来（防来回跳）",
    "world.activity.planning_horizon_minutes": (
        "规划视野（分钟，60~720）：只保证未来 1~12 小时有计划，绝不排满全天"
    ),
    "world.activity.planner_refresh_min_minutes": "软触发重新规划的最短间隔（分钟）",
    "world.activity.max_future_episodes": "计划里最多排几条 future proposal（默认 6）",
    "world.activity.model_advisor.enabled": (
        "模型顾问（默认关：关着就是纯规则，行为与 6B/6C 逐字一致）"
    ),
    "world.activity.model_advisor.timeout_ms": "单次顾问调用的墙钟上限：500~5000ms，默认 1500",
    "world.activity.model_advisor.provider": "供应商标签：只写进日志与 trace，不参与选路",
    "world.activity.model_advisor.model": "用哪个模型：既有 router 里的模型名",
    "world.initiative.enabled": (
        "Initiative / LifeIntent（只**产生意图**：提出 / 评估 / 记录 / 抑制 / 过期；"
        "执行层恒为 NONE）"
    ),
    "world.initiative.cooldown_minutes": "同类 Initiative 的冷却（分钟，默认 20）",
    "world.initiative.max_proposals_per_hour": "每小时最多产生几条意图（防爆上限，默认 3）",
    "world.initiative.recent_interaction_suppress_minutes": (
        "用户刚说过话之后的抑制窗口（分钟，默认 10；复用既有的 user_interaction_at）"
    ),
    "world.proposals.enabled": (
        "任务提案（TaskProposal · Phase 7C）：只**产生提案**（记录 / 检查能力 / 判可行 / 过期），"
        "执行层恒为 NONE —— 关掉 = 不再把意图与用户请求记成提案"
    ),
    "world.proposals.recent_limit": "提案只读视图一次显示几条（默认 10）",
    "world.proposals.max_per_pass": "一次 pass 最多把几条新意图变成提案（防爆上限，默认 3）",
    "world.proposals.ttl_hours": "提案存活小时数，到点即 EXPIRED 终态（默认 6）",
    "agent_plans.enabled": (
        "AgentPlan（Phase 7D）：把提案/请求变成**结构化计划**（只规划；"
        "USER 与待确认任务同建，LIFE 需用户「批准」后才建任务）"
    ),
    "agent_plans.view_limit": "计划只读视图一次显示几条（默认 10）",
    "minecraft.memory.enabled": "Minecraft 身份桥 + 世界记忆（关掉只是「不记得」，权限照旧）",
    "minecraft.memory.reconcile_interval_seconds": "多久拿当前世界核对一次记忆（对账）",
    "minecraft.memory.context_items": "每次对话最多带几条 Minecraft 记忆（≤5）",
    "minecraft.memory.linked_players": "运维直接写死的「QQ 号 → 玩家名」（只认人，不给权限）",
    "minecraft.action.place.timeout": "单次放置最长多少秒（到点按 TIMEOUT 收尾并清理）",
    "minecraft.action.equip.timeout": "单次换手（把物品拿到主手）最长多少秒",
    "minecraft.action.inventory_move.timeout": "单次背包搬运（单物品单槽位）最长多少秒",
    "minecraft.action.container.timeout": "单次容器动作（读 Chest/Barrel 或搬一格）最长多少秒",
    "minecraft.action.container.max_distance": "最大容器交互距离（格）；超出的直接拒绝",
    "minecraft.action.find_blocks.max_distance": (
        "找方块的默认搜索半径（格）；硬上限 32 —— 不允许让模型做「扫全世界」的大范围扫描"
    ),
    "minecraft.action.find_blocks.max_results": (
        "找方块默认最多返回几条；硬上限 16（超出会标 truncated）"
    ),
    "minecraft.action.pickup.timeout": "拾取一个掉落物最长多少秒（到点按 TIMEOUT 收尾并收掉导航）",
    "minecraft.action.pickup.max_distance": (
        "掉落物最远多少格就去捡；启动时太远直接拒绝，追的过程中被拉远也会停下"
    ),
    "minecraft.action.recipe_lookup.timeout": "查 2×2 配方（配方表 + 当前背包）最长多少秒",
    "minecraft.action.craft.timeout": "单次合成（玩家 2×2 或指定工作台的 3×3）最长多少秒",
    "minecraft.action.craft.crafting_table.max_distance": (
        "指定工作台时的最大交互距离（格）；超出的直接拒绝"
    ),
    "minecraft.action.place.max_distance": "最大交互距离（格）；超出的目标会被拒绝（不自己走过去）",
    "minecraft.action.dig.timeout": "单次挖掘最长多少秒（到点按 TIMEOUT 收尾并停止挖掘）",
    "minecraft.action.dig.max_distance": "最大挖掘距离（格）；超出的方块会被拒绝（不自己走过去）",
    "minecraft.action.move_to.max_distance": "单次移动最远走多少格；超过它会被拒绝（防长距离请求）",
    "minecraft.action.follow_player.timeout": "跟随最长持续多少秒（10~600；到点自动停下并清 Goal）",
    "minecraft.action.follow_player.max_chase_distance": "离目标超过多少格就放弃（不追到世界尽头）",
    # ------------------------------------------------------------ 连续性 / 会话
    "continuity.enabled": "连续性层：记住未说完的话、兴趣与共同经历",
    "continuity.current_interest_ttl_hours": "「当前兴趣」多久后过期",
    "continuity.unfinished_thought_ttl_hours": "「没说完的话」多久后过期",
    "continuity.max_open_loops": "最多同时挂几件悬而未决的事",
    "continuity.open_loop_ttl_days": "悬而未决的事保留几天",
    "continuity.max_recent_events": "最近事件保留条数",
    "continuity.micro_event_ttl_minutes": "微事件（碎碎念）的存活时间",
    "continuity.recent_emotion_ttl_minutes": "近期情绪的存活时间",
    "continuity.shared_experience_min_confidence": "共同经历的最低置信度（低于它不写）",
    "conversation.debounce.enabled": "消息合并：短时间多条消息等一等再回",
    "conversation.debounce.direct_message_ms": "私聊的合并等待窗口",
    "conversation.debounce.group_message_ms": "群聊的合并等待窗口",
    "expression.enabled": "表达（口癖）系统开关",
    "expression.min_occurrences": "一个说法出现几次才算口癖",
    "expression.min_speakers": "至少几个人用过",
    "expression.learn_max_per_hour": "每小时学习上限",
    "expression.max_patterns_per_group": "每组保留上限",
    "expression.inject_max_items": "回复时最多注入几条",
    "expression.inject_max_chars": "注入字数上限",
    "expression.groups": "口癖分组",
}


def _note(key: str, leaf: str) -> str:
    """Per-item explanation, most specific key first."""
    return _NOTES.get(key) or _NOTES.get(leaf, "")


#: 配置类（点分路径）→ 中文类名。设置页的导航栏用它分组，缺省回退到路径本身。
_SECTION_LABELS: dict[str, str] = {
    "bot": "机器人",
    "logging": "日志",
    "permissions": "权限",
    "database": "数据库",
    "web": "Web 控制台",
    "runtime": "运行时",
    "onebot": "QQ 连接（OneBot）",
    "character": "角色人设",
    "behavior": "对话行为",
    "behavior.reply": "回复节奏",
    "behavior.chunking": "消息分条",
    "behavior.schedule": "作息与免打扰",
    "behavior.group": "群聊参与",
    "behavior.initiative": "主动聊天",
    "behavior.initiative.core_friend": "主动聊天（核心好友）",
    "ai": "AI 总览",
    "ai.context": "上下文预算",
    "ai.cooldown": "失败与限流冷却",
    "ai.concurrency": "并发",
    "ai.usage": "用量记录",
    "ai.providers.<n>": "服务商",
    "ai.models[i]": "模型链",
    "memory": "记忆总览",
    "memory.extraction": "记忆提取",
    "memory.retrieval": "记忆检索",
    "memory.retrieval.weights": "检索权重",
    "memory.semantic": "语义检索",
    "memory.semantic.embedding": "向量嵌入",
    "memory.consolidation": "记忆整理",
    "memory.policy": "活跃记忆上限",
    "memory.retention": "记忆保留",
    "social": "社交认知",
    "social.continuation": "对话跟进",
    "social.observer": "群聊观察",
    "social.participation": "群聊发言额度",
    "social.thresholds": "参与阈值",
    "social.attention": "注意力",
    "social.fatigue": "社交疲劳",
    "social.topic": "群话题",
    "social.group_context": "群上下文缓冲",
    "sandbox": "生活沙盒",
    "sandbox.core_friend_relationship": "核心好友初始关系",
    "media": "媒体与表情",
    "expression": "表达（口癖）",
    "tools": "工具能力",
    "tools.rate_limit": "工具速率限制",
    "tools.permissions": "工具权限",
    "tools.configs.<n>": "单个工具的配置",
    "task": "多步骤任务（Task Runtime · Phase 5A）",
    "agent": "任务代理",
    "agent.budget": "任务预算",
    "agent.mode": "任务模式",
    "agent.evaluator": "任务评估",
    "agent.planner": "任务规划",
    "continuity": "连续性",
    "conversation.debounce": "消息合并",
    "world": "角色世界（世界时钟 · 活动）",
    "world.activity": "世界活动（Activity Episode · Phase 6A）",
    "world.activity.model_advisor": "模型顾问（软判断的参谋 · Phase 6D）",
    "world.initiative": "意图（Initiative / LifeIntent · Phase 7A）",
    "world.proposals": "任务提案（TaskProposal · Phase 7C）",
    "agent_plans": "任务计划（AgentPlan · Phase 7D）",
    "minecraft": "Minecraft 集成",
    "minecraft.agent": "Minecraft 智能体（LLM 工具）",
    "minecraft.agent.tools": "模型可以做什么（风险分级）",
    "minecraft.agent.confirmation": "高风险动作的用户确认（Phase 4A）",
    "minecraft.agent.chat": "游戏内聊天（Phase 4A）",
    "minecraft.memory": "身份桥 + 世界记忆（Phase 5C）",
    "minecraft.action.dig": "Minecraft 挖掘（dig · 破坏单方块）",
    "minecraft.action.place": "Minecraft 放置（place · 放单方块）",
    "minecraft.action.equip": "Minecraft 换主手（equip · 单个物品）",
    "minecraft.action.inventory_move": "Minecraft 背包搬运（inventory_move · 单槽位）",
    "minecraft.action.container": "Minecraft 容器（Chest / Barrel · 单方块）",
    "minecraft.action.find_blocks": "Minecraft 找方块（find_blocks · 只读定位）",
    "minecraft.action.pickup": "Minecraft 拾取掉落物（pickup · 一次一个明确实体）",
    "minecraft.action.recipe_lookup": "Minecraft 查配方（玩家 2×2）",
    "minecraft.action.craft": "Minecraft 合成（一次一个配方）",
    "minecraft.action.craft.crafting_table": "Minecraft 工作台（3×3 · 明确坐标）",
    "minecraft.action.move_to": "Minecraft 导航（move_to）",
    "minecraft.action.follow_player": "Minecraft 跟随（follow_player）",
}


def _section_of(key: str) -> str:
    """The config class a field belongs to (dotted path without the leaf)."""
    return key.rsplit(".", 1)[0] if "." in key else key


def _section_label(section: str) -> str:
    return _SECTION_LABELS.get(section, section)


@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    description: str
    type: str
    default: Any
    constraints: dict[str, Any] = field(default_factory=dict)
    area: str = "系统"
    level: str = ADVANCED
    hot_reload: bool = False
    restart_required: bool = True
    usage_status: str = ACTIVE_WITH_RESTART
    sensitive: bool = False
    choices: tuple[str, ...] = ()
    hidden: bool = False
    #: 所属配置类（点分路径）与其中文名 —— 设置页按类分组的导航栏用它
    section: str = ""
    section_label: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "type": self.type,
            "default": self.default,
            "constraints": self.constraints,
            "area": self.area,
            "level": self.level,
            "hot_reload": self.hot_reload,
            "restart_required": self.restart_required,
            "usage_status": self.usage_status,
            "sensitive": self.sensitive,
            "choices": list(self.choices),
            "hidden": self.hidden,
            "section": self.section,
            "section_label": self.section_label,
        }


# ------------------------------------------------------------------ derivation


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return "list"
    if origin is dict:
        return "dict"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "obj"
    return "str"


def _constraints(field_info: Any) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for rule in field_info.metadata:
        kind = type(rule).__name__
        if kind == "Ge":
            found["min"] = rule.ge
        elif kind == "Gt":
            found["exclusive_min"] = rule.gt
        elif kind == "Le":
            found["max"] = rule.le
        elif kind == "Lt":
            found["exclusive_max"] = rule.lt
        elif kind in ("MinLen", "MinLength"):
            found["min_length"] = rule.min_length
        elif kind in ("MaxLen", "MaxLength"):
            found["max_length"] = rule.max_length
        elif kind == "Pattern":
            found["pattern"] = rule.pattern
    return found


def _matches(key: str, prefix: str) -> bool:
    """Prefix match that understands the ``<n>`` / ``[i]`` wildcard forms."""
    if prefix.endswith("."):
        return key.startswith(prefix)
    return key == prefix or key.startswith(prefix + ".") or key.startswith(prefix + "[")


def _usage_status(key: str) -> str:
    if key in _UNUSED:
        return DEFINED_BUT_UNUSED
    if key in _LEGACY:
        return LEGACY
    for prefix, (_flag, _why) in _CONDITIONAL.items():
        if _matches(key, prefix):
            return CONDITIONALLY_USED
    return ACTIVE if _hot(key) else ACTIVE_WITH_RESTART


def _hot(key: str) -> bool:
    if any(_matches(key, exception) for exception in _HOT_EXCEPT):
        return False
    return any(_matches(key, prefix) for prefix in _HOT)


def _level(key: str) -> str:
    if any(_matches(key, prefix) for prefix in _BASIC):
        return BASIC
    if any(_matches(key, prefix) for prefix in _ADVANCED):
        return ADVANCED
    return EXPERT


def _area(key: str) -> str:
    section = key.split(".", 1)[0]
    return _SECTION_AREA.get(section, "系统")


def _sensitive(key: str) -> bool:
    leaf = key.rsplit(".", 1)[-1]
    if leaf == "api_key_env":  # a variable *name* is not itself a secret
        return False
    return leaf in _SENSITIVE_LEAVES


def _describe(key: str, leaf: str) -> str:
    """One human sentence per item: what it controls + when to touch it."""
    hot = _hot(key)
    head = f"{_leaf_label(leaf)}（{key}）"
    note = _note(key, leaf)
    text = f"{head}：{note}" if note else head
    if key in _UNUSED:
        return text + "。当前版本没有任何读取方，保留仅为了兼容旧配置。"
    if key in _LEGACY:
        return text + "。旧语义（每 10 分钟世界刷新）已废弃，现在只作为手动步进的上限。"
    return text + ("。保存后立即生效。" if hot else "。" + _RESTART_NOTE + "。")


def _walk(model: type[BaseModel], prefix: str, out: list[ConfigField]) -> None:
    for name, info in model.model_fields.items():
        key = f"{prefix}.{name}" if prefix else name
        annotation = info.annotation
        origin = get_origin(annotation)
        args = get_args(annotation)
        if origin is dict and args and isinstance(args[1], type) and issubclass(args[1], BaseModel):
            _walk(args[1], f"{key}.<n>", out)
            continue
        if (
            origin in (list, tuple, set, frozenset)
            and args
            and isinstance(args[0], type)
            and issubclass(args[0], BaseModel)
        ):
            _walk(args[0], f"{key}[i]", out)
            continue
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            _walk(annotation, key, out)
            continue
        try:
            default = info.get_default(call_default_factory=True)
        except Exception:  # noqa: BLE001 - a factory that needs context
            default = None
        if isinstance(default, BaseModel):
            continue
        leaf = name
        section = _section_of(key)
        out.append(
            ConfigField(
                key=key,
                label=_leaf_label(leaf),
                description=_describe(key, leaf),
                type=_type_name(annotation),
                default=_jsonable(default),
                constraints=_constraints(info),
                area=_area(key),
                level=_level(key),
                hot_reload=_hot(key),
                restart_required=not _hot(key),
                usage_status=_usage_status(key),
                sensitive=_sensitive(key),
                hidden=key in _UNUSED or key in _LEGACY,
                section=section,
                section_label=_section_label(section),
            )
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


_CACHE: list[ConfigField] | None = None


def fields() -> list[ConfigField]:
    """Every config field, derived from the real ``AppConfig`` schema."""
    global _CACHE
    if _CACHE is None:
        out: list[ConfigField] = []
        _walk(AppConfig, "", out)
        _CACHE = out
    return _CACHE


def field_for(key: str) -> ConfigField | None:
    for item in fields():
        if item.key == key:
            return item
    return None


def schema(
    *,
    area: str = "",
    level: str = "",
    include_unused: bool = False,
    include_sensitive: bool = True,
) -> list[dict[str, Any]]:
    """Field metadata for form rendering (values come from ``effective``)."""
    rows: list[dict[str, Any]] = []
    for item in fields():
        if item.hidden and not include_unused:
            continue
        if not include_sensitive and item.sensitive:
            continue
        if area and item.area != area:
            continue
        if level and item.level != level:
            continue
        rows.append(item.as_dict())
    return rows


# -------------------------------------------------------------------- layers

_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_layer() -> dict[str, Any]:
    from app.config.settings import _ENV_OVERRIDES  # noqa: PLC0415 - internal map, read-only

    layer: dict[str, Any] = {}
    for env_name, (section, key) in _ENV_OVERRIDES.items():
        if env_name in os.environ:
            layer[f"{section}.{key}"] = os.environ[env_name]
    return layer


def _yaml_layers() -> tuple[dict[str, Any], dict[str, Any]]:
    """``(raw, expanded)`` — expanded is what ``models:`` produced."""
    import yaml

    path = PROJECT_ROOT / "config" / "config.yaml"
    if not path.exists():
        return {}, {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    import copy

    expanded = resolve_models_block(copy.deepcopy(raw))
    return raw, expanded


def source_map(overrides_path: Path | None = None) -> dict[str, str]:
    """Per-key provenance: env / overrides / models / yaml."""
    from app.web.api.common import flatten

    env_flat = _env_layer()
    overrides_flat = flatten(read_overrides(overrides_path or OVERRIDES_PATH))
    raw, expanded = _yaml_layers()
    raw_flat = flatten(raw)
    expanded_flat = flatten(expanded)
    result: dict[str, str] = {}
    for key in set(env_flat) | set(overrides_flat) | set(expanded_flat) | set(raw_flat):
        if key in env_flat:
            result[key] = "env"
        elif key in overrides_flat:
            result[key] = "overrides"
        elif key in expanded_flat and key not in raw_flat:
            result[key] = "models"
        elif key in raw_flat:
            result[key] = "yaml"
    return result


def _mask(value: Any) -> dict[str, Any]:
    from app.tools.credentials import CredentialManager

    text = "" if value is None else str(value)
    return {"configured": bool(text), "masked": CredentialManager.mask(text) if text else ""}


def _concrete(field_: ConfigField, config: AppConfig) -> list[tuple[str, Any]]:
    """Expand ``<n>`` / ``[i]`` wildcards into the instances that exist now."""
    if "<n>" not in field_.key and "[i]" not in field_.key:
        return [(field_.key, _read(config, field_.key))]
    rows: list[tuple[str, Any]] = []
    for concrete in _instances(field_.key, config):
        rows.append((concrete, _read(config, concrete)))
    return rows


def _instances(key: str, config: AppConfig) -> list[str]:
    from app.web.api.common import split_key

    head = key.split(".<n>", 1)[0].split("[i]", 1)[0]
    parts = split_key(head)
    node: Any = config
    for part in parts:
        if isinstance(part, int):
            node = node[part] if isinstance(node, list) and part < len(node) else None
        else:
            node = getattr(node, str(part), None)
    if isinstance(node, dict):
        names = list(node.keys())
        return [key.replace("<n>", str(name)) for name in names]
    if isinstance(node, list):
        return [key.replace("[i]", f"[{index}]") for index in range(len(node))]
    return []


def _read(config: Any, key: str) -> Any:
    from app.web.api.common import split_key

    node: Any = config
    for part in split_key(key):
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
            node = node[part]
        else:
            model_dump = getattr(node, "model_dump", None)
            if model_dump is not None:
                node = getattr(node, str(part), None)
            elif isinstance(node, dict):
                node = node.get(str(part))
            else:
                return None
    return node


def effective(
    config: AppConfig,
    *,
    area: str = "",
    level: str = "",
    include_unused: bool = False,
    keys: list[str] | None = None,
    overrides_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Effective values with their provenance — the "what is it *now*" view."""
    from app.web.api.common import split_key

    _ = split_key  # keys are parsed by _read/_instances; kept for symmetry
    wanted = set(keys) if keys else None
    wanted_wild = [key for key in (keys or []) if "<n>" in key or "[i]" in key]
    sources = source_map(overrides_path)
    env_layer = _env_layer()
    rows: list[dict[str, Any]] = []
    for item in fields():
        if item.hidden and not include_unused:
            continue
        if area and item.area != area:
            continue
        if level and item.level != level:
            continue
        for concrete, value in _concrete(item, config):
            if (
                wanted is not None
                and concrete not in wanted
                and not any(wildcard_match(concrete, pattern) for pattern in wanted_wild)
            ):
                continue
            row = {
                "key": concrete,
                "label": item.label,
                "area": item.area,
                "level": item.level,
                "type": item.type,
                "usage_status": item.usage_status,
                "hot_reload": item.hot_reload,
                "restart_required": item.restart_required,
                "hidden": item.hidden,
            }
            if item.sensitive:
                row["sensitive"] = True
                # the live environment wins over the boot-time config object:
                # a key written through the credentials API must show up here.
                live = env_layer.get(concrete, value)
                row.update(_mask(live))
            else:
                row["value"] = _jsonable(value)
                row["source"] = sources.get(concrete, "default")
            rows.append(row)
    return rows


def wildcard_match(concrete: str, pattern: str) -> bool:
    """``ai.models[0].name`` matches ``ai.models[i].name``; dict keys match ``<n>``."""
    if "<n>" not in pattern and "[i]" not in pattern:
        return concrete == pattern
    regex = re.escape(pattern)
    regex = regex.replace(re.escape("<n>"), r"[^.]+").replace(re.escape("[i]"), r"\[\d+\]")
    return bool(re.fullmatch(regex, concrete))


def usage_note(key: str) -> str:
    for prefix, (_flag, why) in _CONDITIONAL.items():
        if _matches(key, prefix):
            return why
    return ""


def hot_keys(keys: list[str]) -> list[str]:
    return [key for key in keys if (item := field_for(key)) is not None and item.hot_reload]


def restart_keys(keys: list[str]) -> list[str]:
    return [key for key in keys if (item := field_for(key)) is None or item.restart_required]
