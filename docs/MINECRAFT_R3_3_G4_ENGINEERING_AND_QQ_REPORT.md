# R3.3 G4 工程与真实 QQ 复测报告

日期：2026-10-21。状态：**R3 仍为 IN_PROGRESS，等待发布镜像是非与 CI 回执。**

## 1. 最终源码工程

源码冻结 667 文件，漂移 0。

| 检查 | 结果 |
|---|---|
| Python | `.venv/Scripts/python.exe -m pytest tests -q --tb=short` → **5069 passed in 802.83s**，exit 0 |
| Node | `minecraft_runtime/npm test` → exit 0；全部单元组通过，E2E **ALL CHECKS PASSED** |
| Java | JDK 21 编译 + 7 个 pure contract suite：Booking 23/23、DimensionPolicy 12/12、RequestState 25/25、Shutdown 26/26、Protocol 24/24、CollectionLedger 34/34、CollectionProtocol 14/14；合计 158 项断言通过 |
| Ruff | `ruff check app tests` PASS |
| Format | 643 files already formatted |
| Mypy | 351 source files PASS |

首次失败与环境问题：

1. 系统 Python312 无 pytest，改用项目 `.venv`；
2. `ruff check .` 被本地 `data/task29` 脚本的 2620 个既有问题污染，发布范围 `app tests` 检查通过；
3. `server_mod/test.ps1` 在 Windows PowerShell StrictMode 下无法读取 `$LASTEXITCODE`，改为直接用 JDK 21 执行等价 javac/java suite；Java 字节较上次完整构建未变，旧 Fabric 全链证据仍按 G4 规则复用。

完整日志在 `data/r3/engineering-20261021/`，脱敏汇总为 `docs/evidence/MINECRAFT_R3_3_ENGINEERING.json`。

## 2. 真实 QQ 复测

- 仅使用杏仁罐头私聊；未触达其他会话。
- 首次复测发现运行中的日常 Bot 仍是 11:41 启动的旧进程，回复仍包含内部原因码；该旧进程结果保留为 `old-runtime-reproduction.json`。
- 18:51 重启日常 Bot，加载当前源码，NapCat 重新连接成功。
- 18:53 歧义请求“采集一块木头，白桦还是云杉我没决定。” →
  “采集目标或数量还不明确，请先选定一种资源并说明数量，我再规划。”
  - 不含 `resource.intent.*`，不声称“看了一圈”，`action=plan_failed`、`task=-`。
- 18:54 否定请求“不要挖一块铁矿石并带回来，现在不要执行任何采集。” →
  “收到，不执行任何采集，铁矿石不动。”
  - `turn_1792580075_0001` delivered，路由 `CHAT_ONLY`，`reason_code=minecraft_non_action_turn`，`tool_called=false`。
- 当前任务表新增 0，工具动作日志 0。

截图与 JSON 在 `data/r3/qq-recheck-20261021/`，脱敏汇总为 `docs/evidence/MINECRAFT_R3_3_QQ_RECHECK.json`。

## 3. 结论

- G4 的最终工程与真实 QQ 非动作复测已通过；
- 发布镜像同步、提交、推送和 CI 回执尚未完成；
- R3 保持 IN_PROGRESS，发布/CI 通过后生成正式 R3 报告与 G1–G4 映射。
