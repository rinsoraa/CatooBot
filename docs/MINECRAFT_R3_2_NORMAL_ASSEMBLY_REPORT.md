# R3.2 正常装配验收阶段记录

状态：PARTIAL。记录日期：2026-10-21。R3 尚未完成，不进入 R4 开发。

> 后续补证：最终源码实服矩阵 84/84 USER 负向与 3/3 LIFE/USER 采集结果见 [R3.2 最终源码实服矩阵补充报告](MINECRAFT_R3_2_LIVE_FINAL_REPORT.md)；本阶段记录中的未完成项以该报告和路线状态为准。

## 已完成

- 通过真实 Bot.start() 装配发现并修复 SkillAwarePlanner 未接收 shape_hint 的接口不兼容问题；相关技能、意图和编排测试 272 项通过。
- 发现并修复任务事件并发造成 Activity Episode 终态先于启动处理的问题；活动与正常装配测试 498 项通过。
- 正常装配专项现有 7 项测试；修改文件 lint、格式检查通过，应用 mypy 351 个文件通过。
- 第三轮真实 Minecraft 测试完成 12 类资源、两种初始库存共 24 项任务，24 项通过；实际数据库有 24 个 COMPLETED Episode，记忆事实 24 条，ACTIVE 技能 12 个。
- 三轮测试分别保留原始失败、修复及复测记录。每轮均独立备份并恢复测试服。第三轮恢复 107 个文件，重启前全部哈希一致，恢复后服务器已启动（当时 PID 47900）。

## 证据位置

- 任务书：docs/specs/CatooBot Minecraft R3.2 —— Normal Bot Assembly and User Channels.md
- 离线日志：data/r3/normal-assembly-20261021/logs/
- 最后一轮原始数据、数据库、备份及恢复回执：data/r3/normal-assembly-20261021/live-03/
- 上一阶段报告：docs/MINECRAFT_R3_1_TERMINAL_RECOVERY_REPORT.md

上述真实服任务使用正常服务装配和受控输入，并非真实 QQ 消息或 LIFE 来源验收；不能据此认定这些门禁通过。备份、数据库及本地配置含运行信息，不直接作为公开发布材料。

## 待完成

- 旧聊天服务 127.0.0.1:7864 无法连接，真实 Provider 验收未通过。用户准备改用 https://api.rinsora.dpdns.org/v1；已在本地 .env 新增空的 RINSORA_API_KEY，等待用户填写。端点尚未切换，旧密钥未迁移。
- 填写后检查新服务模型列表、现有模型别名兼容性和实际调用，再继续 Provider 门禁。
- 真实 QQ 指定会话尚未验收；此前客户端启动授权等待超时，未发送测试消息。
- LIFE/Goal 来源、双门禁、技能与重规划链路、剩余负面矩阵及最终完整回归、发布和 CI 仍待按 R3 任务书逐项核验。
- R3 全部门禁完成后先汇报 R3，再制定 R4 任务书。未经证据确认的历史完成状态不补写。

## 暂停点

用户要求先告知密钥填写位置，填好回复后再继续。当前保留原有未提交内容及全部既有测试证据；此记录不宣告 R3 或后续阶段完成。

## 恢复后的核验（2026-10-21）

用户已要求继续。新环境变量存在且非空，但以应用同类 HTTP 客户端请求新服务模型列表返回 HTTP 401 / invalid_api_key；因此尚未切换日常 Provider，等待用户本地修正密钥。失败响应脱敏保存在 data/r3/normal-assembly-20261021/provider-02/connection-result.json。首次标准库请求返回 403，亦未算作通过。

第三轮冻结的 665 个源码文件重新逐项核对，无字节变化；原始 24 个结果均为 PASS，实际 SQLite 24 个 Episode 为 COMPLETED，记忆事实 24 条、ACTIVE 技能 12 个。脱敏文件哈希已新增归档至 docs/evidence/MINECRAFT_R3_2_NORMAL_ASSEMBLY.json，原始证据不改写。完整 Python 回归与 Node 单元/E2E 已启动独立新日志，运行中不预写结果。

以当前 Python 纯消费者重新读取第三轮全部原始任务记录，24/24 证据链通过；独立验证文件为 live-03/verification.json。此复核不声称重新验证传输签名，原运行时负责签名验证。

Node 完整 npm test 已退出 0，严格归因 84/84，E2E ALL CHECKS PASSED，日志为 logs/node-resume-provider02.log。用户补充服务协议为 OpenAI Chat Completions；项目 openai_compatible 正是该协议。按 Bearer 密钥直接 POST /v1/chat/completions 仍返回 401 / invalid_api_key，响应脱敏归档在 provider-02/chat-connection-result.json，未切换日常服务。

新增 tests/test_r3_life_normal_admission.py：使用实际 Bot.start 和 SQLite，确认缺失、其他角色、抑制、过期四种 LIFE 意图来源在规划前被拒绝，不能批准、建立任务或访问世界。4 passed，最终日志 life-origin-final.log；外部传输受控，不算真实 LIFE 执行链通过。首轮测试也为4 passed；首次 lint 发现共享夹具导入命名冲突，调整导入后重跑4 passed且lint通过。此新增文件晚于完整回归收集，须与该轮完整回归结果分别报告。

全仓 ruff check app tests 通过，ruff format --check 为642文件通过，mypy app 为351文件通过；对应独立日志使用 resume-provider02 后缀。产品代码在本次恢复中未修改。

## 用户更新后的实际供应商验证

用户说明已将 Workbuddy2API 的地址和 WORKBUDDY_API_KEY 替换，并在 WebUI 重建 SiliconFlow。重新读取合并后的实际配置确认生效；不再使用的 RINSORA_API_KEY 及本次新增注释已按用户要求移除。此前针对旧 RINSORA 变量的401不代表当前 Workbuddy2API 鉴权状态。

- 当前 Workbuddy2API：四个不同聊天模型均经实际应用 Provider 成功调用，返回非空回复；primary/secondary/fallback/vision/small 的四种模型ID可用。仅验证文字回复，不宣称视觉图片理解通过。
- 当前 SiliconFlow：实际 EmbeddingService 将别名 Embedding 解析为 BAAI/bge-m3，三条合成文本得到三条1024维有限非零向量；相近文本余弦0.9284，无关文本0.4881。语义向量接口通过；没有向日常数据库写入测试记忆。
- R3解释器九例均 decision_invalid_json，确定性合同九例通过，无世界动作。四个聊天模型对相同结构化任务请求均返回普通文本。最小system指定JSON也返回普通问候，显式response_format=json_object仍返回普通文字；保存为 provider-03/protocol-diagnostic.json。此为输出兼容性门禁失败，不能以聊天连通代替通过，也不能据此断言网关内部实现。
- 日常配置未由本次测试改换模型或风险设置；正在等待用户核对网关的system/结构化输出行为。

完整Python首轮5049 passed、1 teardown error：会话级真实.env字节保护发现运行期间文件变化，与用户手动配置更新重叠。保留原始失败，不禁用保护；原日志含断言展示的环境片段，必须排除在公开导出之外。完成清理后已启动独立 full-resume-provider03.log 重跑，此轮包含新增4项来源测试，运行中不预写PASS。

当前凭据读取 /models 为200，四种已配置聊天模型均列在可用模型中；system最小复现另存 provider-03/system-role-reproduction.json（不含密钥）。供应商仍未取得R3结构化决策PASS，等待用户核查，不把原因擅自归为具体网关实现。

下一轮反向矩阵驱动草稿归档于 negative-prepare/：从旧逐ID七类反例驱动继承夹具/干预，接入当前正常 Bot.start 装配，调整技能包装的风险开关访问并统一关闭流程。只通过AST语法检查，尚未运行、没有新世界动作，不能计入任何门禁；正式执行前仍须重新核验现场、制作新的当前服务器备份、部署候选并扩展原规格剩余负向类别。生成器首次在函数签名断言处停止，未改写原脚本，后续正确生成的来源哈希与状态均在 preparation-receipt.json。

最终独立完整Python重跑：**5053 passed in 922.12s，exit 0**，包含新增4项正常LIFE来源测试，真实.env会话保护也通过。日志 full-resume-provider03.log 已冻结散列归档。此前5049 passed / 1 teardown error保留原结果，不覆盖为通过。当前没有遗留的本轮测试进程。

本轮工程回归通过不等于R3 COMPLETE。下一步仍为：解决新网关结构化决策兼容性；完成正常LIFE/Goal/Memory真实双门禁及允许QQ/MC通道；按原规格执行最终逐ID负向矩阵；完成发布备份、白名单同步与对应CI。R3全PASS后汇报，再制定R4任务书。

## 最小复测恢复通过（2026-10-21 12:09，北京时间）

按用户要求使用当前配置重新运行，未修改产品代码或供应商配置：system指定JSON、显式JSON模式、实际TurnDecisionInterpreter三项全部通过；解释器第一次调用即decision_ok。证据 minimal-retest-20261021-120940/results.json。

随后原九类真实Provider语义与确定性准入复验全部通过：五类资源请求为task_proposal，询问为chat，否定为cancel，歧义为clarify，停止为stop；各类别均符合原先固定接受集合。日志 provider-semantic-fourth.log 和 provider-04/provider-and-guard.json。无世界动作；不算真实QQ或LIFE实服门禁。此前失败原始记录保留，当前结构化决策子门禁改为PASS；恢复原因未从仓库证实，待确认。原R3其余剩余项保持不变。
