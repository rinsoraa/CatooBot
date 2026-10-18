# CatooBot R1 残余缺口统一收敛任务书

## 目标

基于当前 CatooBot R1 残余闭环实现，完成 N5、N6-C/N7、N8、N9-B、P1 和 Coverage UX 六项工作的一次性诊断、修复、验证、文档更新和最终裁定。

本任务不是重新实现 R1，也不是无条件追求全绿。应保留已经通过的 RC1–RC5 和已有真实证据，修复可证实的问题，补齐缺失证据，并对客观无法证明的能力如实保留 PARTIAL/BLOCKED。

**执行要求：一个完整工作周期，不在每个工作包结束后等待用户再次批准，不为本任务范围内新发现的问题创建新的 Follow-up。**

## 0. 开工基线

1. 重新核验最新 `origin/main`、本地 HEAD、dirty 状态和实际部署版本。已知历史基线为 `c3d31cbb49233ae6fb70b4022a90b9f54c78934a`，不得假定它仍是最新远端 HEAD。
2. 核验当前代码是否包含实现提交 `cbdecc8cc26ab6f4e08f16b2528fe3aaca665dec` 对应的 R1 修复。
3. 只读审计相关实现、测试、依赖锁文件和当前 R1 报告。先运行受影响的窄测试，保存修复前证据。
4. 保护已有未提交工作，不使用破坏性 reset，不清理无关文件。
5. 严禁为制造 PASS 而修改 `MOVE_TO_RADIUS=1.5`、`MOVE_MAX_DISTANCE=64`、`MOVE_TIMEOUT_MS=30000`、生产默认 `thinkTimeout`、安全策略、确认门、权限、冻结计划语义或测试断言。
6. 被测 bot 不得通过 `/tp` 移动。op 只可搭建/还原测试夹具。
7. `scripts/` 保持 0 tracked；临时脚本、数据库、备份、密钥和隐私日志不得提交。

## 1. N6-C/N7：不可站立目标的保守快速诊断

先审计 `move_to.validate()`、目标坐标语义、维度、`bot.blockAt()`、chunk 加载状态、碰撞箱和 `GoalNear`。

在证据充分时，增加只读预检，使用稳定原因码，例如 `target_not_standable`。只有当目标在当前导航契约下确实不可用，且相关邻域数据完整、已加载、维度明确时，才能提前拒绝。

不得仅凭“目标格实心且六邻格不符合简化规则”就拒绝。`GoalNear` 允许 bot 到达目标附近的合法位置。未加载、`blockAt()` 返回 null、维度未知或碰撞形状无法可靠判断时，返回 UNKNOWN/既有安全结果，不得猜测不可达。

测试必须覆盖：
- 存在合法邻近落脚点，不误拒；
- 完整加载区域内确实不可站立，快速失败；
- 未加载/未知邻域，不被误判为不可站立；
- 楼梯、半砖、头部空间和碰撞形状边界；
- dimension guard；
- STOP/CANCEL 与预检竞态。

N6-C/N7 各运行一次真实服务器验证。记录请求目标、维度、加载证明、原因码、动作是否启动、终态及独立世界快照。目标是正确诊断，不是让所有失败都提前变成 `action.invalid`。

## 2. P1：第三方独立观察客户端

修正观察端启动与传送时序：
1. observer 启动并完成登录；
2. 等待其 join 事件；
3. 通过 observer 自身日志及 `/list` 等独立信息确认它已加入服务器；
4. 等待目标玩家进入 `bot.players` 并建立 `entity`；
5. 必要时才由 op 执行定位；
6. 读取 observer 独立取得的目标坐标。

记录 join、玩家列表出现、entity 出现、坐标采样的时间和坐标。证明数据不是从产品 `world_view()` 或同一 Runtime 缓存转发得到的。

如果 observer 已入服但仍因视距或 entity tracking 无法取得目标，保留 BLOCKED，记录原始错误和已完成验证。不得将 `/status` 与 `world_view()` 两层同源数据冒充 E4。

## 3. N8：受控 timeout 契约测试

可在 `minecraft_runtime/runtime.js` 增加测试专用的 `MC_TEST_THINK_TIMEOUT_MS` 覆盖，但必须同时满足：
- 只有 `MC_TEST_MODE=1` 且 timeout 为合法正整数时生效；
- 生产默认行为不改变；
- CI 常规/生产任务不设置该变量，并增加隔离守卫；
- 非法、缺失或越界值不能造成生产配置意外变化；
- 测试进程隔离，不能污染共享 bot 或后续测试。

使用锁定版本的真实 `mineflayer-pathfinder` 进行至少 5 次受控触发，要求 5/5 经过 timeout 分支。验证：
- timeout 且仍有可用路径：不立即错误判失败；
- timeout 且无可用路径：安全失败；
- 后续路径更新、到达事件、STOP/CANCEL 和迟到事件：终态唯一、不可复活。

保持既有 `move_to.test.js` 全量检查通过。报告必须标记为 E2（真实依赖、测试专用参数），不可称为自然世界 E3 成功。

## 4. N9-B：执行窗口内目标变化

使用可控 barrier/event 同步，不以固定 sleep 作为唯一时序保证。

必须证明：
1. 计划已确认且冻结；
2. 对应 step 进入 `WAITING_ACTION`，具有真实 `action_id`；
3. dispatch 后、后置条件确认前，夹具改变目标方块；
4. 事件可关联 `task_id`、`plan_hash`、`step_id`、`action_id`；
5. 终态从未 `SUCCEEDED`，不自动换邻近目标，不重写计划或授权。

E2 测试覆盖事件先到/后到、重复事件、目标变化与 STOP/CANCEL 竞态。E3 至少一次真实服务器运行命中该时序，并独立核验世界状态。

N9-A 与 N9-B 必须分别记账。若 E3 因环境限制无法完成，保留 E2 结果及 E3 deviation，不得声称 N9-B 已完全验证。

## 5. N5：复杂绕障先 trace，后决定恢复

### 阶段 A：有界诊断，不改变恢复行为

在 pathfinder 更新与 ActionRuntime 生命周期中加入有界诊断摘要：
- 最近最多 20 条路径事件：相对时间、status、path_len、是否有可用路径；
- 每秒一次的位置进展/位移采样；
- updates、partials、timeouts、last_status、last_path_len、idle_ms；
- 必要的起点/目标、维度、动作剩余预算。

只在诊断/测试模式启用或在失败时输出摘要。不得记录聊天原文、凭据或无关玩家信息。trace 有硬上限，错误不能改变动作结果。覆盖 timeout、STOP/CANCEL、并发动作隔离、日志脱敏、终态唯一性。

### 阶段 B：固定路线重复验证

使用相同、可复现、可恢复的绕障夹具，至少进行 5 次尝试。每次记录夹具尺寸、起点/目标、路径事件、位置进展、终态和独立位置核验。

历史 30 秒超时必须保留。报告需区分历史夹具是否有效与当前固定路线的重复表现，不得删除失败或把不一致夹具混入统计。

### 阶段 C：基于证据决定是否重算

默认不加入 `setGoal(sameGoal, true)`。先依据 trace 分类：

- 非空有效路径但持续无进展：继续排除碰撞、实体阻挡、物理状态、路径振荡及目标失效。
- 反复 timeout 且无可用路径：保持 fail-closed，不重复重置空路径。
- 路径频繁重算：调查目标抖动或事件源，不通过强制重算加剧振荡。
- 有持续进展但 30 秒内未抵达：不得直接用重算绕过总预算。
- 证据不足：只交付诊断，不修改恢复逻辑。

只有证据明确支持“有可用路径但可恢复地停滞”，且排除 STOP/CANCEL、目标过期、维度变化、未知 coverage 和不可达几何后，才可实施最多 2 次有界重算。

任何重算必须：
- 复用既有动作生命周期，不引入第二状态机；
- 使用同一仍有效的冻结目标；
- 不重置 30 秒总预算；
- 不刷新授权、确认或 plan_hash；
- 重算前检查 STOP/CANCEL、终态、目标 freshness、dimension 和 coverage；
- 阻止旧路径更新或迟到事件覆盖新状态；
- 有明确原因码、硬上限和幂等终态。

固定路线至少 5 次，目标 5/5 独立核验成功。若少于 5/5，R1.2 保持 PARTIAL，附每次 trace 和失败类型。即使 5/5，也只能声称该受测几何通过，不能推论所有复杂地形均可靠。

## 6. Coverage UX

实现并测试以下契约：
- `COMPLETE + matches=[]`：只可说“在本次完整检查的范围内没有找到目标”；
- `PARTIAL`：明确说明只检查了部分区域，不能确认目标是否存在；
- `UNKNOWN`：明确说明目前无法确认覆盖范围；
- 不得把 PARTIAL/UNKNOWN 说成“附近没有”。

建议话术：
“我还没能确认这片区域是否都检查到了，所以不能确定这里有没有目标。可以先移动到一个已加载的区域再重新观察。”

该话术中的移动只是建议，不自动执行。不得无条件固定移动 8–16 格；任何移动建议必须符合现有距离限制、世界边界、危险区、任务状态与 Policy。用户同意后仍必须走原有 proposal/approval/confirm 链。

测试 COMPLETE/PARTIAL/UNKNOWN 与空/非空 matches、过期观察、维度不匹配等组合。真实服务器至少验证一次不完整 coverage 时，用户不会收到虚假的“没有目标”结论。

## 7. 完整回归与最终裁定

执行：
- `move_to.test.js`；
- `find_blocks` 和 coverage 测试；
- N6-C/N7、N8、N9-B、N5 trace 与恢复竞态测试；
- coverage UX 测试；
- `pytest tests -q`；
- `ruff check .`、`ruff format --check .`、`mypy app`、`git diff --check`；
- 相关 CI 和真实服务器门禁。

CI 首轮失败必须如实保留提交、job、失败步骤和重跑结果。重跑通过不等于根因已修复。

最终裁定：
- R1.1 PASS：coverage 契约正确，不完整覆盖不会被误报为空。
- R1.2 只有在 N5 固定路线 5/5 独立成功、N6-C/N7 预检无误拒、N8 timeout 两分支通过且 STOP/CANCEL/迟到事件不变量保持时，才可 PASS。
- R1.3 PASS：N9-A/N9-B 分别有证据，目标变化不会使旧计划误成功或切换目标。
- R1.4 PASS：证据等级真实、回归/CI/卫生通过。
- N5 仍失败、N9-B 证据不足或预检会误拒可达目标时，总体不得 PASS。
- P1 若因环境限制仍 BLOCKED，需说明它是否阻断技术裁定及理由，不得伪称 E4 PASS。

不得为获得 PASS 而放宽门槛、删除历史失败、使用传送、注入假成功事件或改变安全策略。

## 8. 归档与报告

将本任务书归档至：

`docs/specs/CatooBot Minecraft R1.2 —— N5 N6 N7 N8 N9 P1 Coverage UX.md`

更新 R1 报告时采用追加式记录，保留历史 PARTIAL、失败和 deviation。报告不应把自身提交 SHA 固化在正文；用“本文件所在提交，SHA 见 git log -1”。

最终报告必须记录：
- 总体及 R1.1–R1.4 分项裁定；
- baseline/implementation/final HEAD SHA、dirty；
- N5 每次尝试及独立核验结果；
- N6-C/N7 的预检与误拒防护证据；
- N8 受控 timeout 次数、两分支及生产默认隔离；
- N9-A/N9-B 分别的 E2/E3 结果；
- P1 observer join/entity/E4 状态；
- coverage UX 结果矩阵；
- STOP/CANCEL/迟到事件不变量；
- pytest、Node、静态检查、CI 运行及首轮失败；
- 受保护区 diff、凭据扫描、scripts tracked 数量；
- 任务书归档字节数、SHA256、identical；
- 遗留项、影响、证据与归属；
- `FOLLOW-UP CREATED: NO`。

执行时不要只输出计划，也不要在工作包之间等待再次批准。完成整个周期后一次性提交最终报告与裁定。可靠、可复现、证据诚实优先于强行全绿。
