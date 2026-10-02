# 角色人物档案（示例模板）

> 复制本文件为 `config/character_bible.md` 并填入真实角色设定。
> 真实档案属于私人数据，已被 `.gitignore` 排除，不会进入 Git；
> 本示例文件是仓库里唯一的档案模板（schema 参考）。
>
> 运行时由 Character Bible Compiler 编译为结构化定义：
> Character Bible → CharacterDefinition → CharacterWorldSeed → Sandbox。
> 角色名/宠物/空间等会直接成为沙盒实体——不要在 Python 里写死角色。

# （角色名）档案

## Static Facts

- 角色名：（必填，沙盒实体与对话人设都取这个名字）
- 别名：
- 性别：
- 年龄：
- 生日：
- 身高：
- 外貌：（一句话外形；可写"居家/外出"两套状态）
- 居住：（独居/合租/家人同住……）
- 生活状态：（上学/工作/自由职业；作息特点）

## Modes

### （模式一，例如 宅家模式）
- id: home
- 触发：（什么时候进入这个模式；默认模式写明"默认状态"）
- 风格：（说话方式；口癖用引号括起，会被提取为语音校准短语）
- 行为：（这个模式下的典型行为，一行一条）

### （模式二，例如 外出模式）
- id: outdoor
- 触发：
- 风格：
- 行为：

## Social Boundaries

- （明确边界一条一条写：例如 恋爱话题回避 / 不接受说教 / 不暴露现实身份 / 无聊客套不回）
- （这一节每条都会成为 Canonical Behavior Policy，优先级高于一切 Few-shot）

## Relationships

### （名字）
- type: core_friend
- 定位：（关系说明）
- 特征：（互动特征；"约联机会放下手头的事"这类行为会被沙盒打断机制引用）

## Livelihood

- formal_employment: false
- casual_income: true
- 说明：（收入来源一句话）

## Preferences

### 食物饮料
- （清单；锚定物品名会进入沙盒库存与"补给"动作链）

### 游戏 / 爱好
- （清单；关键词决定角色"拥有"哪些沙盒动作，例如 Minecraft → play_minecraft）

## World Seed

### Spaces
- home（家，根空间）：
  - room1 房间一
  - room2 房间二
- outside（外部）：
  - street 街道
  - shop 商店

### Objects
- fridge 冰箱（厨房）：库存载体
- computer 电脑（客厅）：主要娱乐
- bed 床（卧室）：睡觉

### Inventory
- fridge：食物 × 3
- character（随身）：手机

### Pet
- name: （宠物名；没有宠物就整节删除）
- species: 猫
- 性格：
- 行为：
- 特殊：

## Values

- 对……：（一句话价值观，用于人格 traits 与行为偏好）

## Speech Examples

### 模式名
"（引用号内的台词会被提取为语音校准短语）"
（圆括号内的舞台指示只作为行为候选，不会自动变成规则）

## Persona Prose

（一到两段散文：角色魅力核心、绝对不要出现的内容、家庭/学校/朋友网的边界说明。）
