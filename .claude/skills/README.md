# skills/ — 项目技能目录

存放项目专属的可复用技能。每个技能一个子目录，内含 `SKILL.md`。技能只在任务命中其 `description` 时被加载，因此必须写清触发场景。

## SKILL.md 格式

```markdown
---
name: <技能名，kebab-case，如 generate-test-case>
description: <一句话说明何时使用——触发描述；只有当任务匹配时该技能才会被加载>
---

<技能正文：步骤、约束、模板、检查清单>
```

## 当前技能

（暂无——后续按需添加。候选示例：`generate-test-case`（按 RULE §11 生成测试用例）、`review-code`（按 RULE §14 自查清单评审代码）等。写技能时请引用 `../rules/RULES.md` 对应章节，避免规则重复维护。）
