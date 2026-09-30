# CatooBot

CatooBot 是一个从零自研的 QQ 虚拟角色运行时：QQ 用户面对的是一个拥有稳定人格、
长期记忆、情绪与关系状态、自己的作息与日常生活的**角色**；所有管理、配置、记忆维护
只通过 **WebUI 后台**完成，QQ 端没有任何命令。

它不依赖 NoneBot / NcatBot / MaiBot / AstrBot / Koishi 等任何现成 Bot 框架——
事件系统、消息模型、AI 调度、行为引擎、长期记忆、世界模拟、WebUI 全部自己实现，
只通过 **OneBot 11 协议**与 QQ 侧对接，只通过 **OpenAI 兼容接口**与模型服务对接。

> developer: **Rinsora**

---

## 整体架构

```
QQ 客户端 ⇄ NapCat（QQ 接入层）
                │  OneBot 11 反向 WebSocket（NapCat 主动连入，CatooBot 是 Server）
                ▼
┌─────────────────────────── CatooBot Core ───────────────────────────┐
│  OneBot Adapter（解析/鉴权/API） → Event Bus → Plugins（唯一 QQ 入口）  │
│                                                                      │
│  Behavior Engine ── 决定「要不要说 / 何时说 / 说几条」                  │
│  Character Runtime ─ 决定「说什么」（人设 + 状态 + 关系 + 记忆 + 世界）  │
│  AI Engine / Router ─ 模型故障转移 + 冷却 + 多模态                     │
│  Response Planner / Delivery ─ 延迟、分段、表情附件、发送               │
│                                                                      │
│  Memory（语义长期记忆）  World（持久世界/后台生活）  Social（社交认知）   │
│  Media（视觉 + 表情包运行时）  Tools（工具运行时）  Agent（多步任务）     │
│                                                                      │
│  WebUI（唯一管理入口，http://127.0.0.1:8500）                          │
│  SQLite（版本化迁移，全部异步）                                        │
└──────────────────────────────────────────────────────────────────────┘
```

**职责边界**（写死在架构里的规则）：

- NapCat 只做 QQ 接入层；Core 不感知 NapCat，只认 OneBot 11 协议。
- OneBot JSON 不进业务代码：适配层把它解析成强类型 Event / Segment（pydantic）。
- AI 不住在 Message Handler 里：插件只做「触发 → 交给行为引擎/角色运行时 → 投递」。
- QQ 端零管理命令；一切管理（配置、记忆修正、群开关、世界作息、表情库……）在 WebUI。
- 敏感值（API Key / Token / 密码）只进 `.env`，永不进 YAML、永不进代码。
- 涉及「要不要参与群聊 / 要不要收藏表情」这类决策是**结构化规则与评分**，不是 `random() < p`。
- 角色的后台生活只存在于她自己的虚拟世界，不声称现实世界中可验证的行为；也从不保存思维链。

### 目录结构

```
CatooBot/
├── run.py                  # 启动入口：python run.py
├── pyproject.toml
├── .env.example            # 环境变量模板（复制为 .env 后填写）
├── config/
│   └── config.example.yaml # 配置模板（首次启动自动复制为 config.yaml）
├── app/
│   ├── adapters/onebot_v11/  # OneBot 11 适配层：WS Server、事件解析、API 客户端
│   ├── message/              # 强类型消息模型（text/at/image/face/mface/reply…）
│   ├── core/                 # Bot 聚合根、事件总线、路由、生命周期
│   ├── plugins/              # 插件系统基类与加载器
│   ├── commands/ permissions/# 命令系统与权限（QQ 端当前零命令）
│   ├── config/               # pydantic 配置模型 + YAML/.env/覆盖 合并加载
│   ├── database/             # SQLite + 版本化迁移
│   ├── ai/                   # Provider/模型路由/上下文（多模态 ChatMessage）
│   ├── behavior/             # 行为引擎：回复节奏、分段、作息、调度器
│   ├── character/            # 角色运行时：人设、状态、关系、prompt 组装、出站审查
│   ├── response/             # 回复计划/定时/分段/投递
│   ├── memory/               # 语义长期记忆：embedding、检索、巩固、抽取
│   ├── world/                # 持久世界：时钟、作息、活动 Episode、目标、时间线
│   ├── social/               # 社交认知：观察、续话识别、参与策略、疲劳/注意
│   ├── media/                # 多模态：图片理解、表情库、收藏、表情决策
│   ├── tools/ agent/         # 工具运行时 / 多步 Agent
│   ├── web/                  # WebUI（aiohttp，服务端渲染，中英双语）
│   └── utils/                # 日志、CJK 宽度感知控制台、叙述器（narrator）
├── plugins/chat/             # 唯一的 QQ 侧插件：私聊/群聊触发入口
└── tests/                    # pytest 全量测试（含模拟 NapCat / 模拟 AI Provider）
```

---

## 环境要求

- **Python 3.12+**（Windows / Linux 均可）
- **NapCat**（作为 QQ 接入层；CatooBot 与 QQ 之间的一切都经它转发）
- 至少一个 **OpenAI 兼容**的模型服务（本地或云端均可）

## 安装

```bash
# 1. 克隆并进入目录
git clone <你的仓库地址> CatooBot
cd CatooBot

# 2. 创建虚拟环境并安装依赖（Windows 示例）
python -m venv .venv
.venv\Scripts\pip install -e .

# 3. 准备环境变量
copy .env.example .env      # 然后编辑 .env，填入各项 Key（见下方「配置」）

# 4. 准备配置文件
copy config\config.example.yaml config\config.yaml
# （跳过这步也行：首次启动会自动从 example 复制一份）
```

> Windows 下建议确认 `tzdata` 已安装（`pip install tzdata`），否则时区相关的作息
> 判断会退化为 UTC+8 并在日志里提示。

### NapCat 侧配置

在 NapCat 的「网络配置 → WebSocket 客户端」新建一个连接：

| 项 | 值 |
|---|---|
| URL | `ws://127.0.0.1:8080/onebot/v11/ws` |
| Token | 与 `.env` 里的 `CATOOBOT_ONEBOT_ACCESS_TOKEN` 保持一致（两边都填才生效） |

CatooBot 是 WebSocket **Server**，启动后等待 NapCat 连入；NapCat 没启动时程序只会
等待，不会退出。断线重连、旧连接接管都是自动的。

## 使用

```bash
python run.py
```

启动后控制台会打印初始化摘要（模型、角色、世界状态、后台任务）以及角色
「内心播报」——感知、判断、思考、碎碎念、世界心跳，全部只输出到终端与日志文件，
QQ 用户永远看不到。

- **QQ 侧**：私聊直接说话；群里 @ 她必回；她还会自己判断要不要接话（社交认知）。
  发送图片她会"看懂"，发送表情包她可能收藏并回敬一个。没有任何 `/` 管理命令。
- **WebUI**：浏览器打开 `http://127.0.0.1:8500`（账号密码见下方配置），
  是唯一管理入口：配置热编辑、记忆检索与修正、群聊参与开关、世界作息与时间线、
  表情包库、工具与 Agent 运行记录、模型连通性测试等。
- **测试**：`pytest`（829 个测试；无需真实 QQ 或真实模型，全部用模拟器）。

## 配置

优先级从高到低：`CATOOBOT_*` 环境变量 → `config/overrides.yaml`（WebUI 写入，
勿手工维护）→ `config/config.yaml` → 内置默认值。
**密钥只放 `.env`**（已被 gitignore），配置文件里只写"读哪个环境变量名"。

### `.env`（密钥与凭据）

```ini
# AI 模型 API Key：与 config.yaml 里 models.providers 的 api_key_env 对应
WORKBUDDY_API_KEY=
SILICONFLOW_API_KEY=
# 工具凭据（联网搜索）
TAVILY_API_KEY=
# 安全凭据
CATOOBOT_ONEBOT_ACCESS_TOKEN=    # 与 NapCat 一致
CATOOBOT_WEB_PASSWORD=           # WebUI 初始密码（首次启动建 admin 账号用）
```

### `config.yaml` —— 模型总表 `models:`（所有 AI 模型只在这里配）

```yaml
models:
  # 服务商：地址 + 读哪个环境变量的 Key
  providers:
    Workbuddy2API:
      type: openai_compatible
      base_url: "http://127.0.0.1:7864/v1"
      api_key_env: "WORKBUDDY_API_KEY"
  # 聊天模型，按顺序故障转移（429/5xx/超时 → 下一个）
  chat:
    - { name: primary,   provider: Workbuddy2API, model: "xxx" }
    - { name: secondary, provider: Workbuddy2API, model: "yyy" }
  # 额外模型（供下面的 vision 等按 name 引用）
  extra: []
  vision: ""            # 视觉模型（图片/表情分析）；留空用 chat 第一个
  embedding:            # 语义记忆的向量模型
    { provider: Workbuddy2API, model: "BAAI/bge-m3", dimensions: null, timeout: 10 }
  extraction: ""        # 记忆抽取
  decision: ""          # 社交认知快模型
  planner: ""           # Agent 规划
  evaluator: ""         # Agent 评估
  acquisition: ""       # 表情收藏判断
```

其余配置段一览（每段都有中文注释，见 `config.example.yaml`）：

| 配置段 | 作用 |
|---|---|
| `bot` / `onebot` / `logging` / `database` | 名称、命令前缀、反向 WS 监听、日志（含内心播报开关）、SQLite |
| `permissions` | 超级管理员 QQ 号 |
| `ai` | 总开关、System Prompt、超时温度、上下文长度、模型冷却 |
| `character` | 人设初始值（正式内容建议在 WebUI /character 填写） |
| `memory` | 混合检索权重、语义开关、巩固、保留策略 |
| `behavior` | 回复延迟模型、消息分段、作息（睡眠/DND/夜间）、群参与兜底、主动聊天门控 |
| `web` | WebUI 开关、地址端口、管理员账号 |
| `tools` | 工具开关、决策模式（json/native）、预算与限流、单工具覆盖 |
| `agent` | 自主等级、任务类别、硬预算（步数/调用/时长）、取消暂停短语 |
| `world` | 世界心跳、活动 Episode、作息、目标、环境小事、事件保护、快照恢复 |
| `social` | 观察批次、续话窗口、参与上限与冷却、结构化决策阈值、注意/疲劳 |
| `media` | 视觉开关、表情库目录、表情使用冷却、自动收藏、启动索引器 |

### WebUI 覆盖机制

在 WebUI「配置」页保存的修改会写入 `config/overrides.yaml` 并即时热加载；
`config.yaml` 永远不会被程序改写。删除 `overrides.yaml` 即可完整回退到
`config.yaml` 的状态。

## 设计上值得一提的行为边界

- **回复节奏拟人**：延迟是区间 + 抖动 + 状态因子（忙/睡/夜间/亲密），不是固定值；
  「先看看时间……再说结果」这类时序内容会自动拆成两条消息，中间隔真实的打字间隔。
- **历史有时效**：过期的聊天记录进入 prompt 前会被打上 `[今天凌晨4点]` 式的时间标记，
  出站内容会剥掉模型模仿输出的标记——她不会在晚上八点接着凌晨四点的"晚安"聊。
- **图片 ≠ 表情包**：普通图片永远只做视觉理解；只有 QQ 原生表情 / mface /
  带表情元数据的图片 / 手动导入目录里的文件才算表情资产。
- **失败如实说**：工具查不到就说查不到，语义检索不可用自动降级关键词，
  视觉不可用明确告知——绝不编造。
