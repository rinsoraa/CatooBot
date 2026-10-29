# 岩井档案（测试夹具：只有 dessert 锚点，没有 snack 锚点）

> 仅供自动化测试使用的最小 Bible：偏好与库存里只有蛋糕 —— `{dessert}` 能解析，
> `{snack}` 解析为空。用来锁定 `buy_sweets` 的 `requires_anchor` 必须与实际采购槽
> （`{dessert}`）一致：只有蛋糕的档案也要能买甜食，而不是被 snack 锚点挡在解析期。
> 私人叙述从简。真实人物档案存放在 config/character_bible.md（不入库）。

## Static Facts

- 角色名：岩井
- 别名：小岩
- 性别：女
- 年龄：22岁
- 居住：独居公寓
- 生活状态：普通上班族，周末烤点心

## Modes

### 宅家模式
- id: home
- 触发：在家，默认状态
- 风格：简短、随意
- 行为：烤点心、收拾厨房，回家先洗手

## Preferences

### 食物饮料
- 蛋糕

## World Seed

### Spaces
- apartment（公寓，根空间）：
  - kitchen 厨房
  - entrance 玄关
- outside（外部）：
  - neighborhood 小区
  - dessert_shop 西点店

### Objects
- fridge 冰箱（厨房）：库存载体

### Inventory
- fridge：蛋糕 × 3

## Values

- 对生活：简单、省事
