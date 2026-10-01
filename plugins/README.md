# 用户插件目录

把你的插件（一个 `.py` 文件或一个包）放在本目录，CatooBot 启动时会自动加载。
**每个插件必须带一份 `plugin.json` 清单**——没有合法清单的插件会被拒绝加载。

## plugin.json

```json
{
  "id": "com.example.greet",
  "name": "greet",
  "version": "0.1.0",
  "api_version": "1.0",
  "capabilities": ["message.read", "send.private"],
  "author": "你的名字",
  "description": "打个招呼"
}
```

- `id`：反向域名风格（`com.example.greet`），全局唯一；
- `api_version`：本仓库实现的是 **1.0**，填其它值会被拒绝加载；
- `capabilities`：本插件允许碰的外部面，**没声明的用不了**（见下表）；
- 未知字段会被拒绝（防止拼写错误静静通过）。

## 能力（capabilities）

| 能力 | 允许做什么 |
|---|---|
| `message.read` | 订阅收到的消息：`bot.on_message(handler)` / `bot.event_bus.on(...)` |
| `send.private` | 私聊发送：`bot.send_private(user_id, "…")` / `bot.api.send_private_msg(...)` |
| `send.group` | 群聊发送：`bot.send_group(group_id, "…")` / `bot.api.send_group_msg(...)` |
| `api.call` | 任意 OneBot 动作：`bot.api.call_api(...)`、`bot.api.delete_msg(...)`、`bot.adapter` |
| `schedule` | 注册后台任务：`bot.schedule(job)` / `bot.scheduler` |
| `tools` | 工具运行时：`bot.tools` |
| `services` | 内部子系统：`bot.character`、`bot.memory`、`bot.sandbox`、`bot.response_delivery` 等 |

`log` / `metrics` / `self_id` 不需要声明。未声明就使用某个面，会被**当场拒绝**并写审计日志
（logger `CatooBot.Plugins.Audit`），同时计入 `plugin_capability_denied` 指标。

## 最小示例

`plugins/greet/plugin.json`（上面的清单）+ `plugins/greet/plugin.py`：

```python
from app.plugins.base import Plugin


class GreetPlugin(Plugin):
    name = "greet"
    version = "0.1.0"
    description = "打招呼示例"

    async def on_load(self, bot) -> None:
        bot.on_message(self._on_message)  # 需要 message.read

    async def _on_message(self, event) -> None:
        if event.message.text.strip() == "你好":
            if event.is_group:
                await self.bot.send_group(event.group_id, "你好呀")  # 需要 send.group
            else:
                await self.bot.send_private(event.user_id, "你好呀")  # 需要 send.private
```

## 说明

- `on_load(bot)` 收到的是**受能力约束的 bot 视图**（`app.plugins.api.PluginApi`），
  属性访问与调用都会被检查；同一个对象也挂在 `self.api` 上；
- 以 `_` 开头的模块不会被加载；
- 加载失败 / 卸载失败 / 能力被拒都会按插件计数（`PluginLoader.failure_counts`），
  加载失败的插件只记日志，不影响其他插件与 CatooBot 运行。
