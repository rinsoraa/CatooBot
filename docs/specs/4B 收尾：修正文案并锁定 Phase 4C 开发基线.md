基于当前仓库 `8439322`，执行一个非常小的收尾 cleanup。

## 目标

Phase 4B 已经通过：
- CI 全绿
- Node 五套测试全绿
- pytest 2093 passed
- 真实 Java Server `REAL SERVER: PASS`
- 真实 dig 已验证 `sand → air`
- world snapshot 已验证方块消失
- WorldPerception 输入连续 3 次稳定
- 再次 dig 正确得到 `block.not_found`
- STOP 子场景因为环境没有足够慢速方块而 SKIPPED，不能算失败，也不要伪造 PASS

本次**不要增加任何新功能**，也不要修改：
- ActionRuntime
- runtime.js
- MinecraftService 行为
- confirmation 逻辑
- dig 行为
- tool schema
- policy gate

只修正文案/示例配置中的过期描述。

## 修改内容

搜索整个仓库中与 Minecraft MEDIUM action 相关的配置说明，尤其是：

```yaml
allow_medium: false
```

把类似：

```yaml
# 暂无实现
# 本阶段还没有这类动作
```

这类已经过时的注释删除或改成准确描述。

建议表达为：

```yaml
allow_medium: false # MEDIUM 风险动作默认关闭；启用后仍需用户确认
```

实际 wording 以当前配置文件风格为准，不要机械照抄。

同时检查 README / 示例配置 / WebUI 配置说明中是否还有同样的过时表述。

## 约束

- 只允许修改文档、示例配置、帮助文本、UI 文案。
- 不修改生产逻辑。
- 不改变任何默认值。
- `allow_medium` 默认仍然必须是 `false`。
- 不修改测试断言。
- 不重新设计风险模型。
- 不加入 Phase 4C 功能。

## 验证

完成后运行仓库已有的最小必要检查：
- 配置/格式检查
- 与修改文件相关的测试
- 如项目 CI 对文案/配置没有独立测试，则至少运行已有的 lint / typecheck 相关检查

然后提交一个独立 cleanup commit。

最终报告必须明确：

```text
Phase 4B = PASS
dig STOP = SKIPPED（环境原因，不伪造结论）
本次 cleanup = 仅修正文案，无生产逻辑变化
默认 allow_medium = false 保持不变
```

不要把这次 cleanup 扩展成新的功能开发。