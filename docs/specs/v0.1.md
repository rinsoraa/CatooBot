CatooBot v0.1 开发任务：自研 QQ Bot 核心框架 + NapCat 接入



你现在要协助我从零开发一个自研 QQ Bot，项目暂命名为：



CatooBot



这是一个长期维护、可扩展的个人 QQ Bot 项目。



一、项目目标



第一阶段目标不是做 AI 聊天，而是先完成一个稳定、干净、可扩展的 Bot Runtime。



当前整体架构：



QQ

&#x20;│

&#x20;▼

NapCatQQ

&#x20;│

&#x20;│ OneBot 11 / WebSocket

&#x20;│

&#x20;▼

CatooBot

&#x20;│

&#x20;├─ Event System

&#x20;├─ Message System

&#x20;├─ Router

&#x20;├─ Command System

&#x20;├─ Permission System

&#x20;├─ Plugin System

&#x20;├─ Bot API Client

&#x20;├─ Config

&#x20;└─ Logger



NapCatQQ 是外部 QQ 接入层，CatooBot 不负责 QQ 登录，也不直接操作 QQ 客户端。



NapCat 当前支持 OneBot 11，并支持反向 WebSocket。推荐采用：



NapCat

&#x20;  │

&#x20;  │ WebSocket

&#x20;  ▼

CatooBot



也就是：



CatooBot 在本机启动 WebSocket Server，NapCat 主动连接 CatooBot。



默认开发配置：



Host: 127.0.0.1

Port: 8080

Path: /onebot/v11/ws



完整地址：



ws://127.0.0.1:8080/onebot/v11/ws



同时支持 access token。



二、非常重要的开发原则



这是一个“自研 Bot 框架”项目。



不要把 CatooBot 建立在以下框架之上：



NoneBot

NcatBot

MaiBot

AstrBot

Koishi

其他完整 Bot Framework



这些项目可以作为协议和架构参考，但 CatooBot 必须自己实现核心逻辑。



允许使用：



Python 官方标准库

asyncio

websockets

pydantic

python-dotenv

PyYAML

SQLite

SQLAlchemy（如果确实有必要）

pytest

ruff

mypy



不要为了简单而引入大型框架。



核心目标：



“协议层与 CatooBot 核心解耦。”



以后即使不用 NapCat，CatooBot 也应该可以更换其他 Adapter。



三、技术栈



当前开发环境：



OS:

Windows 11



Language:

Python 3.12+



Architecture:

asyncio + modular architecture



Database:

SQLite



Protocol:

OneBot 11



Transport:

WebSocket



Config:

.env + YAML



Testing:

pytest



优先保证：



Windows 本地运行

Python 3.12

UTF-8

异步

模块化

类型提示

清晰日志

可测试

四、项目目录



请建立如下项目结构：



CatooBot/

│

├─ app/

│  ├─ \_\_init\_\_.py

│  ├─ main.py

│  │

│  ├─ core/

│  │  ├─ \_\_init\_\_.py

│  │  ├─ bot.py

│  │  ├─ event\_bus.py

│  │  ├─ router.py

│  │  ├─ context.py

│  │  ├─ exceptions.py

│  │  └─ lifecycle.py

│  │

│  ├─ adapters/

│  │  ├─ \_\_init\_\_.py

│  │  └─ onebot\_v11/

│  │     ├─ \_\_init\_\_.py

│  │     ├─ server.py

│  │     ├─ client.py

│  │     ├─ event.py

│  │     ├─ api.py

│  │     ├─ models.py

│  │     └─ parser.py

│  │

│  ├─ message/

│  │  ├─ \_\_init\_\_.py

│  │  ├─ event.py

│  │  ├─ segment.py

│  │  └─ message.py

│  │

│  ├─ commands/

│  │  ├─ \_\_init\_\_.py

│  │  ├─ registry.py

│  │  └─ router.py

│  │

│  ├─ permissions/

│  │  ├─ \_\_init\_\_.py

│  │  └─ manager.py

│  │

│  ├─ plugins/

│  │  ├─ \_\_init\_\_.py

│  │  ├─ loader.py

│  │  ├─ base.py

│  │  └─ builtin/

│  │     ├─ \_\_init\_\_.py

│  │     ├─ ping.py

│  │     └─ help.py

│  │

│  ├─ config/

│  │  ├─ \_\_init\_\_.py

│  │  └─ settings.py

│  │

│  ├─ database/

│  │  ├─ \_\_init\_\_.py

│  │  └─ database.py

│  │

│  └─ utils/

│     ├─ \_\_init\_\_.py

│     └─ logger.py

│

├─ plugins/

│  └─ README.md

│

├─ data/

│

├─ logs/

│

├─ tests/

│  ├─ test\_parser.py

│  ├─ test\_message.py

│  ├─ test\_router.py

│  ├─ test\_commands.py

│  └─ test\_onebot.py

│

├─ config/

│  └─ config.example.yaml

│

├─ .env.example

├─ .gitignore

├─ pyproject.toml

├─ README.md

└─ run.py



如果实际实现过程中发现目录需要调整，可以优化，但必须保持：



Core

Adapter

Message

Plugin

Command

Config

Database



这些概念彼此分离。



五、实现 OneBot 11 Adapter



这是当前最重要的部分。



实现：



NapCat

&#x20;  ↓

WebSocket

&#x20;  ↓

CatooBot OneBot 11 Adapter



要求：



1\. WebSocket Server



CatooBot 启动后：



127.0.0.1:8080



监听：



/onebot/v11/ws



支持：



ws://127.0.0.1:8080/onebot/v11/ws

2\. Token 验证



支持配置：



onebot:

&#x20; enabled: true

&#x20; host: 127.0.0.1

&#x20; port: 8080

&#x20; path: /onebot/v11/ws

&#x20; access\_token: ""



如果 token 不为空：



必须验证：



Authorization: Bearer <token>



或者兼容 OneBot/NapCat 的 token 传递方式。



如果 token 错误，应拒绝连接并输出清晰日志。



六、不要把 OneBot JSON 直接传到业务层



这是整个项目非常重要的设计要求。



不要出现：



if event\["post\_type"] == "message":

&#x20;   ...



这种代码大量散落在业务层。



应该：



OneBot JSON

&#x20;    ↓

Parser

&#x20;    ↓

Typed Event

&#x20;    ↓

CatooBot Event Bus

&#x20;    ↓

Business Logic



例如：



MessageEvent(

&#x20;   message\_type="group",

&#x20;   user=User(...),

&#x20;   group=Group(...),

&#x20;   message=Message(...)

)



核心业务层完全不应该依赖 OneBot 原始 JSON。



七、设计统一 Event System



第一阶段至少支持：



MessageEvent

NoticeEvent

RequestEvent

MetaEvent



但当前只需要重点实现：



MessageEvent



消息事件需要能够表达：



私聊

private

群聊

group



至少包含：



self\_id

user\_id

group\_id

message\_id

message

raw\_event

timestamp



同时保留：



sender



信息，例如：



user\_id

nickname

card

role

sex

age



如果事件中存在。



八、设计 Message / Segment



不要把消息永远当成纯字符串。



应该设计：



Message

&#x20;├─ TextSegment

&#x20;├─ AtSegment

&#x20;├─ ImageSegment

&#x20;├─ ReplySegment

&#x20;└─ ...



例如：



Message(\[

&#x20;   TextSegment("你好 "),

&#x20;   AtSegment(user\_id=123456),

&#x20;   TextSegment(" 今天怎么样？")

])



同时支持：



message.text



获取纯文本。



以及：



message.segments



获取结构化消息。



第一阶段至少实现：



text

at

image

reply



即使 image/reply 暂时不实现全部 API，也要把数据结构设计好。



九、实现 Bot API Client



CatooBot 不仅要“收消息”，还要能够调用 OneBot API。



设计：



await bot.send\_private\_message(...)

await bot.send\_group\_message(...)

await bot.delete\_message(...)

await bot.get\_login\_info()

await bot.get\_group\_info(...)

await bot.get\_group\_member\_info(...)



底层统一：



await bot.call\_api(

&#x20;   action="send\_group\_msg",

&#x20;   params={...}

)



API 请求需要：



request\_id / echo



从而可以匹配：



请求

&#x20;↓

OneBot API

&#x20;↓

response

&#x20;↓

Future

&#x20;↓

原始 await 调用



同时支持：



timeout

exception

reconnect

pending request cleanup

十、WebSocket 生命周期



必须实现：



启动

&#x20;↓

等待 NapCat

&#x20;↓

NapCat 连接

&#x20;↓

认证

&#x20;↓

连接成功

&#x20;↓

开始接收事件

&#x20;↓

处理 API 请求

&#x20;↓

连接断开

&#x20;↓

清理 pending requests

&#x20;↓

等待重新连接



日志至少有：



\[CatooBot] Starting...

\[OneBot] WebSocket server started at ...

\[OneBot] NapCat connected

\[OneBot] Bot authenticated

\[OneBot] Connection closed



不能因为 NapCat 暂时没启动导致 CatooBot 整个程序退出。



也不能因为 WebSocket 断开导致 pending Future 永远挂起。



十一、Event Bus



设计简单的异步事件总线：



await event\_bus.emit(event)



允许：



event\_bus.on(MessageEvent, handler)



以及：



event\_bus.on("message", handler)



事件监听器必须支持 async。



需要考虑：



handler exception

handler 顺序

handler 是否阻塞其他 handler

日志



第一版以简单可靠为优先，不要做过度复杂的事件优先级系统。



十二、Command System



实现基本命令系统。



例如：



/ping

/help

/about



命令格式设计为：



/ping

/help

/help ping



设计：



@command("ping")

async def ping(ctx):

&#x20;   ...



或者类似的注册机制。



要求：



命令名称

别名

参数

帮助说明

权限



至少支持：



name

aliases

description

usage

permission

十三、默认内置命令



实现：



/ping



回复：



Pong! 🐱

/about



回复：



CatooBot

A self-developed QQ Bot.

/help



列出当前可用命令。



例如：



CatooBot Commands



/ping

/about

/help

十四、@机器人触发能力



即使第一阶段没有 AI，也需要支持：



@CatooBot 你好



能够解析：



AtSegment



判断是否 @ 自己。



同时：



你好 @CatooBot



也应能识别。



设计一个统一方法：



message.is\_mentioned(bot.self\_id)



这样未来 AI 插件可以直接：



if event.message.is\_mentioned(bot.self\_id):

&#x20;   ...

十五、插件系统



第一阶段不要做复杂插件市场。



只需要设计：



plugins/



自动加载：



Plugin



例如：



class PingPlugin:

&#x20;   name = "ping"

&#x20;   version = "0.1.0"



&#x20;   async def on\_load(self, bot):

&#x20;       ...



&#x20;   async def on\_unload(self):

&#x20;       ...



插件能够：



注册事件

注册命令

调用 Bot API

访问 Context

读取配置



未来再扩展动态加载、热重载。



十六、权限系统



第一阶段做最简单的权限模型：



superuser

admin

user

guest



配置：



permissions:

&#x20; superusers:

&#x20;   - "123456789"



支持：



ctx.is\_superuser()

ctx.is\_admin()



暂时不要设计复杂 ACL。



十七、数据库



第一版只使用：



SQLite



先保存：



bot metadata

user

group



例如：



users

groups

bot\_state



不要提前设计 AI Memory 数据库。



以后再增加：



conversation

memory

personality

十八、配置设计



配置尽量集中。



示例：



bot:

&#x20; name: CatooBot

&#x20; debug: true



onebot:

&#x20; enabled: true

&#x20; host: 127.0.0.1

&#x20; port: 8080

&#x20; path: /onebot/v11/ws

&#x20; access\_token: ""



logging:

&#x20; level: INFO



database:

&#x20; url: sqlite:///data/catoobot.db



敏感数据不要硬编码。



支持：



.env



例如：



CATOOBOT\_ONEBOT\_ACCESS\_TOKEN=

十九、日志系统



日志必须区分：



INFO

DEBUG

WARNING

ERROR



建议输出：



\[12:30:01] \[INFO] \[CatooBot] Starting

\[12:30:01] \[INFO] \[OneBot] WebSocket server started

\[12:30:15] \[INFO] \[OneBot] NapCat connected

\[12:30:16] \[INFO] \[Message] group=123456 user=789012 message=你好



DEBUG 模式下可以显示原始 OneBot JSON。



但：



token、密码等敏感数据绝对不能写入日志。



二十、错误处理



重点处理：



WebSocket disconnect

Invalid JSON

Invalid OneBot event

Unknown API

API timeout

API error

Plugin exception

Command exception

Database exception



原则：



单个事件出错不能导致整个 CatooBot 崩溃。



二十一、启动方式



希望最终可以：



python run.py



启动。



启动后输出：



╔════════════════════════════════════╗

║          CatooBot v0.1             ║

║       QQ Bot Core Runtime          ║

╚════════════════════════════════════╝



\[INFO] Loading configuration...

\[INFO] Loading plugins...

\[INFO] Starting OneBot adapter...

\[INFO] WebSocket:

&#x20;      ws://127.0.0.1:8080/onebot/v11/ws

\[INFO] CatooBot is ready.

二十二、NapCat 配置说明



README 中必须写清楚如何配置 NapCat。



当前建议使用：



网络配置

→ WebSocket 客户端

→ 新建



配置：



URL:

ws://127.0.0.1:8080/onebot/v11/ws



如果配置 Token：



Token:

与 CatooBot 配置保持一致



README 必须明确：



NapCat = QQ 接入层

CatooBot = Bot 核心

OneBot 11 = 中间协议



不要让 README 把三者混为一谈。



二十三、测试要求



必须写基础测试。



至少：



test\_message.py



测试：



TextSegment

AtSegment

Message.text

Message.is\_mentioned()

test\_parser.py



测试：



OneBot JSON

→ MessageEvent

test\_router.py



测试：



MessageEvent

→ correct handler

test\_commands.py



测试：



/ping

/help

/about

test\_onebot.py



至少测试：



连接

认证

事件接收

API request/response

timeout

断开清理

二十四、验收标准



完成之后必须满足：



A



运行：



python run.py



程序正常启动。



B



NapCat 可以连接：



ws://127.0.0.1:8080/onebot/v11/ws

C



CatooBot 可以看到连接成功日志。



D



QQ 私聊 CatooBot：



/ping



收到：



Pong! 🐱

E



群聊中：



/ping



以及：



@CatooBot /ping



都可以正确处理。



F



发送普通消息：



你好



即使当前没有 AI，也应该在日志中看到：



MessageEvent

G



NapCat 重启后：



CatooBot 不需要重启



NapCat重新连接以后能够继续工作。



H



CatooBot 重启后：



SQLite 数据不会丢失

I



任何单条错误消息、插件异常、API timeout 都不能让整个 Bot 崩溃。



二十五、重要架构约束



请严格遵守下面这些原则。



1



不要把 OneBot JSON 结构散落到业务代码。



2



不要把 NapCat 写死在 Core。



Core 应该不知道 NapCat 的存在。



3



不要把 AI 写进 Message Handler。



以后 AI 应该作为 Plugin / Service 使用。



4



不要提前实现复杂 Agent。



5



不要过度设计。



当前版本只追求：



稳定

清晰

可扩展

容易调试

6



所有公共类和关键方法添加类型注解。



7



关键代码添加必要注释，但不要写无意义注释。



8



如果发现需求存在架构冲突，优先保护核心模块解耦。



二十六、未来规划



代码结构需要为以下功能预留空间，但当前不要实现：



CatooBot v0.2

├─ 更完整的 Plugin System

├─ Scheduler

├─ Event Filter

└─ Permission



CatooBot v0.3

├─ AI Provider

├─ Model Router

├─ SiliconFlow

├─ DeepSeek

├─ GLM

└─ Kimi



CatooBot v0.4

├─ Conversation

├─ Memory

├─ User Profile

└─ Personality



CatooBot v0.5

├─ Web Dashboard

├─ Plugin Management

└─ Runtime Monitoring



CatooBot v1.0

├─ QQ Adapter

├─ Web Adapter

├─ Minecraft Adapter

├─ AI Agent

└─ Advanced Memory

二十七、开发方式



不要一次性生成大量无法验证的代码。



按照这个顺序执行：



1\. 创建项目骨架

2\. 配置系统

3\. Logger

4\. Message / Segment

5\. OneBot Parser

6\. OneBot WebSocket Server

7\. Bot API Client

8\. Event Bus

9\. Command System

10\. Plugin System

11\. Permission

12\. SQLite

13\. Built-in plugins

14\. Tests

15\. README



每完成一个阶段：



检查 import

检查类型错误

运行测试

修复问题

再进入下一阶段



不要为了赶进度跳过验证。



二十八、最终要求



最终交付一个可以直接运行的：



CatooBot v0.1



它必须是：



Python 3.12+

Windows 可运行

NapCat 可接入

OneBot 11

WebSocket

异步

模块化

可扩展

带基础测试

带 README



并且：



CatooBot 不依赖 NoneBot、MaiBot、NcatBot、AstrBot 等现成 Bot Framework。



它们最多只能作为架构参考。



最终代码应该让我后续能够在这个基础上继续开发真正属于自己的：



CatooBot AI

CatooBot Memory

CatooBot Plugin

CatooBot Agent



现在请开始实现 CatooBot v0.1，并优先完成可以与 NapCat 建立 OneBot 11 反向 WebSocket 连接的最小可运行版本。

