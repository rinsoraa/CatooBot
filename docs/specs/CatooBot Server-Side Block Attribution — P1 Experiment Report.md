# CatooBot Server-Side Block Attribution — P1 只读真实服务器实验报告

> 任务书：`docs/specs/CatooBot Minecraft P1 —— Read-Only Real-Server Attribution Experiment.md`
> （2485 bytes，SHA256 `5BC53A80786C899656A3208048055BB87B15CE4B2F3D10506D4232EC89705CD5`，归档 `identical=True`）。
> 实验日期：2026-10-18（Asia/Shanghai）。**P1 已授权，P2/P3 未授权、未开始。**

## 1. 环境与产物（实验 1 记录）

| 项 | 实测值 |
| --- | --- |
| Minecraft | **1.21.1**（`versions/1.21.1/server-1.21.1.jar`，服务端 intermediary 重映射：`.fabric/remappedJars/minecraft-1.21.1-0.19.5/server-intermediary.jar`） |
| Fabric Loader | **0.19.5**（`fabric-server-mc.1.21.1-loader.0.19.5-launcher.1.1.2.jar`） |
| fabric-api | **0.116.17+1.21.1**（`mods/fabric-api-0.116.17+1.21.1.jar`，2452735 bytes） |
| Java | **21.0.8**（`C:\Program Files\Java\jdk-21`，Oracle JDK） |
| 模组加载数 | 未装 stub 时 **42**；装 stub 时 **43** |
| Stub Mod 产物 | `catoobot-attribution-stub-p1-1.jar`，**2813 bytes**，SHA256 `589C184C8E7173DD492C095DEC193F4CB3321DF6DF5792B5CE2A2533F4E02259` |
| 服务器 | `127.0.0.1:25565`，`online-mode=false`、`gamemode=survival`、维度 `minecraft:overworld` |
| 测试会话 | Catodayo（bot，level 4 op）与 CatodayoMate（第二自动化客户端，level 4 op）；两者 entity 均在 `minecraft:overworld` |

### 启动与监听器注册证据（两次实例均一致）

```text
[11:57:31] [main/INFO]: Loading Minecraft 1.21.1 with Fabric Loader 0.19.5
[11:57:41] [main/INFO]: [STDOUT]: [catoo-attrib] INIT mod=catoobot-attribution-stub version=p1-stub-1
[11:57:41] [main/INFO]: [STDOUT]: [catoo-attrib] LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER
[11:57:46] [Server thread/INFO]: Done (1.313s)! For help, type "help"
```

独立文件 `logs/catoobot-attribution.log` 同步写入（证明不是只靠 stdout）：

```text
2026-10-18T03:44:07.514Z INIT mod=catoobot-attribution-stub version=p1-stub-1
2026-10-18T03:44:07.534Z LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER
2026-10-18T03:57:41.198Z INIT mod=catoobot-attribution-stub version=p1-stub-1
2026-10-18T03:57:41.208Z LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER
```

## 2. 实验 2 三组对照（含失败与重试，全部保留）

| 轮次 | 结果 | 说明 |
| --- | --- | --- |
| 尝试 1 | **无效** | 扫描 `y-1` 取到的三个目标全是 `air`，未发生任何破坏 → 没有任何事件。按任务书判**该轮无效**，不把「没有事件」当结论 |
| 尝试 2 | **中止** | 机器人站在白桦树叶上，`y-1..y-6` 找不到实心目标，脚本自报 `no solid target` 退出 |
| 尝试 3 | **有效** | 改为 7×7×6 体素扫描找真实可破坏方块，三组均实际执行 |

### 尝试 3 的实测记录（唯一有效轮次）

| 组 | 操作 | 目标坐标 | 破坏前（独立读取） | 破坏后（独立读取） | `AFTER` 事件 |
| --- | --- | --- | --- | --- | --- |
| A | op 执行 `/setblock -246 69 -145 minecraft:air` | `(-246, 69, -145)` | `birch_leaves` | `air` | **未触发（无事件）** |
| B | Catodayo 正常挖掘（玩家动作） | `(-246, 68, -145)` | `birch_leaves` | `air` | **触发** |
| C | CatodayoMate 正常挖掘（另一玩家） | `(-277, 67, -138)` | `oak_leaves` | `air` | **触发** |

事件原文（`logs/catoobot-attribution.log`）：

```text
2026-10-18T03:46:56.369Z AFTER player=Catodayo pos=class_2338{x=-246, y=68, z=-145} state_before=Block{minecraft:birch_leaves}[distance=3,persistent=false,waterlogged=false] state_now=Block{minecraft:air} blockEntity=null
2026-10-18T03:47:00.941Z AFTER player=CatodayoMate pos=class_2338{x=-277, y=67, z=-138} state_before=Block{minecraft:oak_leaves}[distance=3,persistent=false,waterlogged=false] state_now=Block{minecraft:air} blockEntity=null
```

**关键事实（实测，不是推断）**：A 组的世界变化真实发生（独立读取 `birch_leaves → air`）却**没有任何 `AFTER` 事件**；
B/C 两组的破坏分别以**正确的玩家身份**触发事件。这证实了设计 v2 §R4 的可判定性前提：
命令移除与玩家破坏在服务端是**可区分**的。

## 3. 证据边界（明确区分实测 / 推断 / 未验证）

* **实测**：`PlayerBlockBreakEvents.AFTER` 在玩家破坏后触发；事件携带玩家名、坐标、**破坏前**方块状态；
  回调内 `world.getBlockState(pos)` 读到的是 `air`（即 AFTER 时方块已移除）。
* **实测**：vanilla `/setblock ... air` **不产生**该事件 —— 因此「无事件」在 P1 条件下**足以**区别于玩家破坏。
* **推断（未做实验）**：其它模组/外部系统对世界的修改同样不会产生 `PLAYER_BREAK` 事件；但**未逐个验证**。
* **未验证**：多人同刻破坏同一坐标的顺序语义、`BEFORE` 取消路径、监听器异常路径、服务器重启瞬间的事件丢失窗口。
* **未验证**：事件与产品 `action_id` 的关联（P2 协议）——P1 **没有**实现任何登记/关联/HMAC，因此本轮**不能**产生 `SELF_CONFIRMED`，
  也**不能**据此关闭 N9-B。

## 4. 夹具恢复

* 测试目标全部是**自然生成的树叶**（白桦树叶、橡树树叶），破坏后未恢复 —— 如实记录，未尝试伪造恢复；
* 未改动地形、未放置人造夹具、未使用 `/tp` 移动被测 bot（B 组由 bot 自行挖掘自身附近方块）；
* 服务器在世界保存后正常停止（`Saving worlds` 日志可见）。

## 5. 约束遵守（P1 授权范围）

* 未接入 CatooBot 产品终态、未实现登记协议、未实现 HMAC 事件传输、未新增归因消费逻辑（P2/P3 未授权）；
* 未修改授权链、工具白名单、Policy、确认门、数据库 schema、生产执行路径；
* Stub Mod **只读**：只写自己的日志文件，不执行任何游戏动作、不修改世界。

## 6. 回滚结果（已验证）

1. 用 op 客户端 `/stop` 优雅停止服务器（世界保存完成）；
2. 删除 `mods/catoobot-attribution-stub-p1-1.jar`；
3. 重启服务器 → 日志显示 **`Loading 42 mods`**（相比 43 少一个）、**没有任何 `catoo-attrib` 行**，端口 25565 正常监听。

即：**回滚干净，无残留**。服务器当前处于**未装 stub** 的状态（保持生产干净）。

## 7. 结论

| 通过条件（任务书 §实验 3） | 结果 |
| --- | --- |
| Stub Mod 可靠启动并登记监听器 | **满足**（两实例日志一致 + 独立日志文件） |
| 三组对照均有可复核的测试前后状态与原始日志 | **满足**（含无效轮次与重试记录） |
| 准确记录所选事件是否在每组实际触发 | **满足**（A 无事件 / B、C 有事件，含原文） |
| 未修改产品行为、授权机制、世界动作执行链 | **满足**（零产品改动、模组只读） |
| 能解释每种结果的证据边界与未确定事项 | **满足**（§3 区分实测/推断/未验证） |

**P1 判定：PASS（只读实验）**。设计 v2 的核心前提被真机证实：`/setblock` 与玩家破坏在服务端可区分，
且事件携带可信玩家身份与破坏前状态。

**但 N9-B 仍为 BLOCKED**：P1 只证明「事件的可用性」，未证明「事件与该次 `action_id` 的唯一关联」——
后者需要 P2（登记 + 关联 + 影子模式）与 P3（接入终态 + 真实执行窗口复验），**均未获授权**。

## 附录：Stub Mod 完整源码（可复现，未入库为脚本）

```java
package catoo.attrib;

import java.io.File;
import java.io.FileWriter;
import java.time.Instant;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;

public final class AttributionStub implements ModInitializer {
    private static final String VERSION = "p1-stub-1";
    private static final String LOG_PATH = "logs/catoobot-attribution.log";

    private static void log(String line) {
        String text = Instant.now().toString() + " " + line;
        System.out.println("[catoo-attrib] " + line);
        try (FileWriter writer = new FileWriter(new File(System.getProperty(
"user.dir"), LOG_PATH), true)) {
            writer.write(text + System.lineSeparator());
        } catch (Throwable ignored) {
        }
    }

    @Override
    public void onInitialize() {
        log("INIT mod=catoobot-attribution-stub version=" + VERSION);
        PlayerBlockBreakEvents.AFTER.register((world, player, pos, state, blockEntity) -> {
            String stateNow;
            try {
                stateNow = String.valueOf(world.method_8320(pos));
            } catch (Throwable error) {
                stateNow = "ERROR:" + error.getClass().getName();
            }
            log("AFTER player=" + player.method_5477().getString()
                    + " pos=" + pos.method_10062()
                    + " state_before=" + state
                    + " state_now=" + stateNow
                    + " blockEntity=" + (blockEntity == null ? "null" : "present"));
        });
        log("LISTENER_REGISTERED PlayerBlockBreakEvents.AFTER");
    }
}
```

构建方式（无 Gradle，直接用 JDK 21 对 intermediary 编译；已验证可行）：

```text
javac -cp <server-intermediary.jar>;<fabric-loader-0.19.5.jar>;<fabric-api-base-0.116.17.jar>;<fabric-events-interaction-v0-0.116.17.jar>;<brigadier-1.3.10.jar> -d out AttributionStub.java
jar --create --file catoobot-attribution-stub-p1-1.jar -C out .
```

`fabric.mod.json`：`id=catoobot-attribution-stub`、`version=p1-1`、`environment=server`、
`entrypoints.main=[catoo.attrib.AttributionStub]`、`depends={fabricloader>=0.19.0, fabric-api:*, minecraft:~1.21.1}`。
（Loader 对 `p1-1` 报过一次“非扩展语义版本”WARN，不影响加载，如实记录。）
