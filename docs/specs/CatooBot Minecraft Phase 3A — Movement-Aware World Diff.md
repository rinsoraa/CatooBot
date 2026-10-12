# CatooBot Minecraft Phase 3A
## Movement-Aware World Diff

### 阶段定位

Phase 2 / 2.1 已完成。

本阶段是 Phase 3 的第一个前置阶段。

本阶段唯一目标：

> 让罐头未来开始移动后，Minecraft 感知系统能够区分“观察窗口发生位移”和“Minecraft 世界真的发生变化”。

完成本阶段之前：

**禁止加入任何 Minecraft 移动能力。**

---

# 一、当前问题

当前近距离方块签名：

```text
near_signature
```

使用 Minecraft 世界坐标 `(x, y, z)` 作为 key。

但是 Near 扫描窗口以罐头自身位置为中心。

Phase 3 加入移动后：

```text
玩家位置变化
↓
near 扫描窗口平移
↓
大量方块进入 / 离开窗口
```

如果直接进行：

```text
previous_signature
vs
current_signature
```

会把：

```text
观察窗口位移
```

错误识别为：

```text
Minecraft 世界方块发生变化
```

因此当前 diff 不能直接用于 Phase 3 移动。

---

# 二、目标行为

### 情况 A：玩家原地不动

世界：

```text
A B C
D E F
G H I
```

下一次：

```text
A B C
D E F
G H I
```

不得产生 world.changed。

---

### 情况 B：玩家旋转

玩家位置不变，只改变 yaw。

不得产生：

```text
minecraft.world.changed
```

因为世界没有改变。

观察方向变化只是感知视角变化。

---

### 情况 C：玩家向东移动 1 格

旧窗口：

```text
[ A B C D E ]
```

新窗口：

```text
  [ B C D E F ]
```

B/C/D/E 是同一批世界方块。

不得因为：

```text
A 离开窗口
F 进入窗口
```

产生 world.changed。

系统必须自动进行窗口对齐。

---

### 情况 D：移动过程中重叠区域真的发生变化

例如：

```text
旧：
A B C D
E F G H

玩家移动一格

新：
B C D E
F X G H
```

其中：

```text
X != F
```

对齐以后：

```text
旧 F
新 X
```

必须被认为是真实世界变化。

如果真实变化数量达到 threshold：

```text
minecraft.world.changed
```

正常触发。

---

### 情况 E：玩家发生大跨度传送

例如：

```text
X=100
↓
X=1000
```

旧窗口和新窗口没有有效重叠。

这种情况：

不得把整个新区域当成“世界改变”。

应该：

1. 识别为 observation window shift / rebase；
2. 放弃旧 near baseline；
3. 用新窗口建立新的 near baseline；
4. 不触发 world.changed。

---

# 三、推荐实现方案

优先使用：

## Block-coordinate anchor

根据：

```text
floor(self.position.x)
floor(self.position.z)
```

得到：

```text
observation_anchor
```

例如：

```python
(
    floor(x),
    floor(z)
)
```

Near 扫描本质上是水平柱面窗口，因此第一版主要使用 X/Z 对齐。

不要使用连续浮点坐标直接作为位移量。

---

# 四、增加 Near Observation Anchor

在 WorldPerception 中维护：

```python
_prev_near_anchor: tuple[int, int] | None
```

当前 baseline 同时包含：

```text
_prev_near_signature
_prev_near_anchor
```

---

# 五、实现对齐 Diff

假设：

```text
previous_anchor = (100, 200)

current_anchor = (102, 200)
```

说明观察窗口向东移动 2 blocks。

把 previous signature 根据 anchor delta 映射到当前世界坐标系。

然后：

```text
只比较两个观察窗口的重叠区域
```

不要把：

```text
窗口新进入区域
窗口离开区域
```

计为世界变化。

---

# 六、Diff 分类

最终 near diff 至少区分：

```text
NO_MOVEMENT
WINDOW_SHIFT
WORLD_CHANGE
TELEPORT_REBASE
```

建议内部结构：

```python
WorldDiff(
    kind=...,
    shifted_blocks=...,
    changed_blocks=...,
    overlap_blocks=...,
)
```

不要求全部暴露给 LLM。

主要用于：

```text
WorldPerception
debug
event system
```

---

# 七、事件行为

## NO_MOVEMENT

正常检查世界变化。

---

## WINDOW_SHIFT

只说明：

```text
罐头移动导致观察窗口发生变化。
```

默认：

**不发送 `minecraft.world.changed`。**

如确实需要调试，可以内部记录：

```text
observation_window.shifted
```

但不要进入普通 LLM 上下文。

---

## WORLD_CHANGE

保持 Phase 2.1 规则：

```text
changed_blocks >= threshold
```

或：

```text
environment signature changed
```

则：

```text
minecraft.world.changed
```

---

## TELEPORT_REBASE

旧窗口与新窗口没有足够重叠。

行为：

```text
clear old near baseline
prime new baseline
```

不得发送 world.changed。

---

# 八、与 Phase 2.1 的兼容性

不得破坏已有规则：

### 分层缓存

必须继续：

```text
near
local
extended
```

独立 merge。

### 环境签名

继续使用：

```text
biome
time_phase
weather
dimension
```

不重新加入：

```text
time_of_day_ticks
light
```

### 方块增删改

在“有效重叠区域”内：

```text
add
modify
remove
```

仍然全部计算。

---

# 九、旋转测试

必须明确增加：

```text
test_yaw_change_does_not_emit_world_changed
```

位置完全不变：

```text
position = same
yaw = 0

↓

yaw = 90
```

不得触发：

```text
minecraft.world.changed
```

---

# 十、移动测试

至少增加：

### Test 1

移动 1 格：

无世界变化。

结果：

```text
minecraft.world.changed == false
```

### Test 2

移动 3 格：

无世界变化。

结果：

```text
minecraft.world.changed == false
```

### Test 3

移动后 overlap 区域 1 个方块变化：

threshold=1 时：

```text
minecraft.world.changed == true
changed_blocks == 1
```

### Test 4

移动后 overlap 区域 10+ 方块变化：

threshold=10 时正常触发。

### Test 5

移动窗口产生大量新区域：

不得被计成 changed_blocks。

### Test 6

玩家 teleport 到远处：

不得触发 world.changed。

新位置建立 baseline。

### Test 7

yaw 改变：

不得触发 world.changed。

### Test 8

垂直移动 / 跳跃：

不得因为玩家自身高度变化把世界判定为大量方块变化。

---

# 十一、Runtime 不加入任何移动接口

本阶段：

禁止新增：

```text
/minecraft/move
/minecraft/move_to
/minecraft/follow
```

禁止：

```text
bot.setControlState(...)
```

禁止：

```text
pathfinder
```

禁止任何实际移动能力。

本阶段只修改：

```text
WorldPerception
WorldStateCache
world diff
tests
必要文档
```

---

# 十二、性能要求

Movement-aware diff 不得导致：

```text
O(N²)
```

或大范围全世界扫描。

当前 near signature 最大约 220 列。

允许：

```text
O(N)
```

或：

```text
O(N + M)
```

级别的 key alignment。

---

# 十三、验收标准

必须满足：

```text
[ ] 静止 + 无变化 → 无 world.changed
[ ] 原地旋转 → 无 world.changed
[ ] 移动 1 格 + 无变化 → 无 world.changed
[ ] 移动 3 格 + 无变化 → 无 world.changed
[ ] 移动 + overlap 区域真实变化 → 正确计数
[ ] 移动 + 新窗口区域大量进入 → 不计为世界变化
[ ] teleport → baseline rebase，不触发 world.changed
[ ] vertical movement → 不产生虚假大规模变化
[ ] environment signature 规则保持不变
[ ] Phase 2.1 全部回归测试通过
[ ] 全量 CI 通过
```

---

# 十四、完成定义

只有满足：

> 玩家未来可以开始移动，而 WorldPerception 不会因为观察窗口移动产生大量虚假的 `minecraft.world.changed`。

才能标记：

```text
PHASE 3A COMPLETE
```

注意：

本阶段完成后，罐头仍然不能移动。

下一阶段才进入：

```text
Phase 3B
Safe Action Layer
```

输出：

1. 修改文件
2. movement-aware diff 算法说明
3. baseline / anchor 设计
4. 新增测试
5. 边界案例
6. 全量测试结果
7. Phase 3B readiness