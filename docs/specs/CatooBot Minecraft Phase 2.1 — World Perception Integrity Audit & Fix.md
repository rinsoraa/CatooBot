# CatooBot Minecraft Phase 2.1
## World Perception Integrity Audit & Fix

先说明：

Phase 2 功能主体已经完成。

本阶段不是重新设计 Minecraft 感知架构，而是修复当前 `main` 中 World Perception 的三个数据一致性问题，并补齐针对性测试。

完成后才能进入 Phase 3。

---

# 一、严格范围

本阶段只允许修改：

- WorldStateCache
- WorldPerception diff
- 对应单元测试
- 必要的 Phase 2 文档说明

禁止新增：

- Minecraft 移动
- move_to
- Pathfinder
- dig
- place
- follow
- combat
- craft
- Agent Loop
- Minecraft Action Tool

本阶段仍然只有“眼睛”。

---

# 二、修复 1：分层 Cache 必须真正 Merge

当前问题：

`WorldStateCache.update()` 直接：

```python
self._raw = raw
```

导致 partial snapshot 会覆盖其它 layer。

目标：

不同 layer 必须独立维护。

推荐结构：

```text
WorldStateCache
├── online
├── self
├── players
├── entities
├── environment
├── near
├── local
├── extended
├── interesting
├── layer timestamps
└── global metadata
```

规则：

### near 更新

只能覆盖：

```text
near
```

其余：

```text
local
extended
```

必须保留。

### local 更新

只能覆盖：

```text
local
interesting
```

near / extended 必须保留。

### extended 更新

只能覆盖：

```text
extended
```

near / local 必须保留。

动态状态：

```text
self
players
entities
environment
```

可以使用最新成功 snapshot。

---

# 三、Layer Age

必须保证：

```text
更新 near
```

不会让：

```text
local age
extended age
```

被错误刷新。

例如：

```text
t=0
near=0
local=0
extended=0

t=1
near 更新

结果：

near=0
local=1
extended=1
```

---

# 四、补充 Cache Regression Tests

必须增加：

```text
test_partial_near_update_preserves_local_and_extended

test_partial_local_update_preserves_near_and_extended

test_partial_extended_update_preserves_near_and_local
```

必须实际断言 semantic model / raw view 中仍然存在其它 layer。

---

# 五、修复 2：Environment Diff

当前不能直接：

```python
environment != previous_environment
```

因为：

```text
time_of_day_ticks
```

持续变化。

定义：

```python
environment_signature(environment)
```

只包含真正的语义环境变化：

```text
biome
time_phase
weather
dimension
```

不要比较：

```text
time_of_day_ticks
```

不要直接比较精确 light。

如果未来需要 light event，另行设计阈值。

---

# 六、Environment Tests

增加：

### Test A

只改变：

```text
time_of_day_ticks
```

不得产生：

```text
minecraft.world.changed
```

### Test B

改变：

```text
time_phase
```

必须产生事件。

### Test C

改变：

```text
weather
```

必须产生事件。

### Test D

改变：

```text
biome
```

必须产生事件。

---

# 七、修复 3：Block Removal Diff

当前 changed 只遍历 current signature。

必须比较：

```python
previous.keys() | current.keys()
```

变化规则：

```python
previous.get(key) != current.get(key)
```

这样必须检测：

```text
stone -> missing
```

或：

```text
block -> air / removed
```

---

# 八、Block Removal Tests

增加：

```text
test_world_changed_detects_removed_block

test_world_changed_counts_multiple_removed_blocks
```

至少覆盖：

```text
previous:
A stone
B stone
C stone

current:
A stone
B stone
```

结果：

```text
changed_blocks == 1
```

以及：

```text
删除 >= threshold
```

必须产生：

```text
minecraft.world.changed
```

---

# 九、Phase 3 Movement Boundary

不要实现移动。

但在文档中明确记录：

当前 near signature 使用世界坐标。

Phase 3 加入玩家移动以后：

```text
观察窗口位移
```

不能直接被解释为：

```text
世界方块发生变化
```

Phase 3 开始时需要重新设计 movement-aware world diff。

---

# 十、测试要求

必须通过：

```text
pytest 全量
ruff
ruff format
mypy
vitest
vue-tsc
Playwright
Node E2E
```

并新增上述至少：

- 3 个 partial cache tests
- 4 个 environment tests
- 2 个 removal tests

---

# 十一、完成定义

只有以下全部满足：

```text
[ ] partial layer 不会覆盖其它 layer
[ ] layer_age 正确
[ ] time_of_day_ticks 不产生 world.changed
[ ] time_phase 可以产生 world.changed
[ ] weather 可以产生 world.changed
[ ] biome 可以产生 world.changed
[ ] block removal 可以被检测
[ ] 新增测试覆盖全部问题
[ ] 全量 CI 通过
[ ] 没有新增 Minecraft Action 能力
```

最终输出：

1. 修改文件清单
2. 三个问题的根因
3. 修改前后行为
4. 新增测试清单
5. 全量测试结果
6. 当前剩余风险
7. Phase 3 readiness assessment

不要自行进入 Phase 3。