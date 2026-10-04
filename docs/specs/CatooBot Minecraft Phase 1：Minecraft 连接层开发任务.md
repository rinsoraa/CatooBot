# CatooBot Minecraft Phase 1

## 阶段目标

为 CatooBot 增加 Minecraft Java Edition 连接能力。

本阶段唯一目标：

> 让“罐头”能够通过 QQ / WebUI 接收 Minecraft Java 服务器地址，使用配置好的 Minecraft 账号加入服务器，并将连接状态、进入世界、Minecraft Chat、被踢、断开等基础事件反馈给 CatooBot。

本阶段完成后，必须能够实际看到“罐头”作为一个 Minecraft 玩家出现在服务器中。

---

## 严格范围

本阶段只实现 Minecraft Connection / Bridge Layer。

必须实现：

1. Minecraft Java Edition 连接
2. host + port 解析
3. Minecraft 账号认证
4. 加入服务器
5. 进入世界后的基础状态获取
6. connected / spawned / chat / kicked / disconnected / error 等事件
7. 主动 disconnect
8. CatooBot 与 Minecraft Bridge 之间的稳定通信
9. WebUI 基础连接控制
10. QQ 侧能够触发 join / leave

本阶段禁止实现：

- LLM Minecraft Agent
- 自动探索
- World Semantic Scan
- Chunk Semanticization
- 自动寻路
- 自动挖矿
- 自动建造
- 自动战斗
- Minecraft 长期记忆
- 自主任务系统
- 复杂 Minecraft UI
- 视觉识别
- 截图理解

不要因为未来需求而提前实现上述功能。

---

## 架构要求

不要把 Mineflayer 与 CatooBot 核心业务逻辑强耦合。

推荐架构：

CatooBot Core
↓
Minecraft Adapter / Bridge
↓
Minecraft Runtime
↓
Mineflayer
↓
Minecraft Server

Minecraft Bridge 必须独立封装 Minecraft 生命周期。

未来 Phase 2/3 将在 Bridge 之上增加：

World Perception
Semantic World Model
Action Tools
Navigation
Minecraft Agent

因此本阶段接口需要考虑未来扩展，但不要提前实现这些功能。

---

## 推荐技术

Minecraft Runtime 优先使用 Mineflayer。

使用独立 Node.js Runtime 管理 Minecraft bot。

如果 CatooBot 主体不是 Node.js，不要为了 Minecraft 强行改造现有 CatooBot 核心语言。

两者通过明确的 API / WebSocket / Event Bridge 通信。

---

## 账号要求

支持：

### Offline Server

允许使用：

auth = offline

### Online / Microsoft Authentication

支持 Microsoft authentication。

Minecraft 账号认证信息不得硬编码到 Git。

账号 token / 私密认证数据必须保存在本地安全目录，并加入 Git ignore。

禁止通过 QQ / WebUI 接收 Microsoft 密码。

---

## CatooBot → Minecraft Bridge API

至少设计：

POST /minecraft/connect

请求：

{
  "host": "...",
  "port": 25565
}

返回：

{
  "session_id": "...",
  "status": "connecting"
}

---

GET /minecraft/status

返回当前：

- connection status
- server host
- server port
- minecraft username
- dimension
- position
- health

---

POST /minecraft/chat

请求：

{
  "message": "..."
}

---

POST /minecraft/disconnect

主动退出 Minecraft。

---

## Minecraft → CatooBot Events

至少支持：

minecraft.connecting
minecraft.connected
minecraft.spawned
minecraft.chat
minecraft.player_joined
minecraft.player_left
minecraft.kicked
minecraft.disconnected
minecraft.error

事件应包含：

- session_id
- timestamp
- event type
- 必要上下文信息

Minecraft Chat 示例：

{
  "event": "minecraft.chat",
  "session_id": "...",
  "username": "空凛",
  "message": "罐头过来"
}

---

## 生命周期

必须有明确状态：

DISCONNECTED
CONNECTING
AUTHENTICATING
CONNECTED
SPAWNING
ONLINE
DISCONNECTING
ERROR

不要通过多个 boolean 拼凑状态。

必须避免：

- 重复 connect 导致多个 bot session
- 重复 disconnect
- 连接失败后进程崩溃
- Minecraft 异常导致 CatooBot 主进程崩溃
- 网络断线后状态卡死

---

## WebUI 第一阶段

只提供：

Minecraft Server

Host:
[________________]

Port:
[____]

[加入服务器]

连接后显示：

Status
Server
Username
Dimension
X
Y
Z

[离开服务器]

不要加入地图、背包、AI 控制台等未来功能。

---

## QQ 第一阶段

允许通过 CatooBot 现有消息系统触发：

“罐头，加入 Minecraft”

以及服务器地址。

成功：

“我进来啦！”

失败：

“我进不去这个服务器……原因是 XXX”

离开：

“好啦，我回来啦。”

Minecraft Chat 暂时可以通过现有 CatooBot 消息系统进入后续处理链，但不要在本阶段实现完整的 Minecraft 对话 Agent。

---

## 验收标准

### Test 1
启动 CatooBot。

### Test 2
通过 WebUI 输入测试服务器 host + port。

### Test 3
罐头成功加入 Minecraft。

### Test 4
Minecraft 客户端可以看到罐头实体。

### Test 5
获取：

- username
- dimension
- position
- health

### Test 6
玩家在 Minecraft 中发送聊天：

“罐头”

CatooBot 能收到 minecraft.chat event。

### Test 7
CatooBot 调用 Minecraft Chat API：

“我在这里！”

Minecraft 中可以看到罐头发言。

### Test 8
服务器主动关闭 / 踢出玩家。

CatooBot 能正确收到事件并恢复到 DISCONNECTED / ERROR 状态。

### Test 9
主动调用 disconnect。

Minecraft 中罐头正常离开。

### Test 10
完整连接 → 进入 → 聊天 → 退出流程重复至少 3 次，不产生残留 session，不产生僵尸 Minecraft Bot。

---

## 完成定义

只有以下条件全部满足，才宣布 Phase 1 COMPLETE：

> CatooBot 可以稳定启动 Minecraft Runtime。
>
> 用户可以提供一个 Minecraft Java 服务器地址。
>
> 罐头可以使用配置好的 Minecraft 身份进入服务器。
>
> CatooBot 能知道罐头是否已经进入世界。
>
> CatooBot 能获得罐头基础状态。
>
> Minecraft Chat 可以双向传递。
>
> 断线 / 被踢 / 错误不会破坏 CatooBot。
>
> 可以主动让罐头离开服务器。
>
> 全流程可重复运行。
>
> 不提前实现 Phase 2+ 的 Minecraft AI 功能。

完成后输出：

1. 修改文件清单
2. 架构说明
3. API / Event Schema
4. 启动方式
5. Minecraft 登录配置方法
6. 测试记录
7. 已知限制
8. Phase 2 建议

不要在未通过上述验收标准之前自行扩展功能。