# 用户插件目录

把你的插件模块（`.py` 文件或包）放在本目录，CatooBot 启动时会自动加载。

## 最小示例

`plugins/greet.py`：

```python
from app.commands.registry import command
from app.core.context import Context
from app.plugins.base import Plugin


class GreetPlugin(Plugin):
    name = "greet"
    version = "0.1.0"
    description = "打招呼示例"


@command("greet", description="打个招呼")
async def greet(ctx: Context) -> None:
    await ctx.reply(f"你好，{ctx.sender_name}！")
```

## 说明

- 模块内的 `Plugin` 子类会被实例化并调用 `on_load(bot)` / `on_unload()`；
- 用 `@command(...)` 标记的函数会被自动注册为命令（无需手动注册）；
- 插件内可通过 `bot.event_bus.on(...)` 订阅事件、`bot.api` 调用
  OneBot API、`bot.config` 读取配置；
- 以 `_` 开头的模块不会被加载；
- 加载失败的插件只记日志，不影响其他插件与 CatooBot 运行。
