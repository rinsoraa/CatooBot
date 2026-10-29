# 秋刀档案（测试夹具：只有锚点，没有采购字面词）

> 仅供自动化测试使用的最小 Bible：零食与饮料只出现在库存和偏好里，
> 正文刻意不含任何「购买」字面词 —— 采购动作必须靠锚点存在归属
> （`{drink}`/`{snack}`/`{dessert}` 都能解析）。
> 私人叙述从简。真实人物档案存放在 config/character_bible.md（不入库）。

## Static Facts

- 角色名：秋刀
- 别名：小刀
- 性别：女
- 年龄：20岁
- 居住：独居公寓
- 生活状态：普通上班族，自己做饭，周末宅家

## Modes

### 宅家模式
- id: home
- 触发：在家，默认状态
- 风格：简短、随意
- 行为：做饭、收拾厨房，回家先洗手

## Preferences

### 食物饮料
- 可乐、布丁、蛋糕

## World Seed

### Spaces
- apartment（公寓，根空间）：
  - kitchen 厨房
  - entrance 玄关
- outside（外部）：
  - neighborhood 小区
  - convenience_store 便利店
  - dessert_shop 西点店

### Objects
- fridge 冰箱（厨房）：库存载体

### Inventory
- fridge：可乐 × 10、布丁 × 3、蛋糕 × 1

## Values

- 对生活：简单、省事
