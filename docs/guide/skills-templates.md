# Skills 与提示词模板

两者都是「文件即扩展」，但用途不同：

- **Skills**：给**模型**用。只把 `name + description` 放进 system prompt，正文由模型按需用
  `read_skill` 加载——所以放 100 个 skill 也几乎不占上下文。
- **Templates**：给**你**用。在 REPL 里敲 `/name args`，展开成一段 prompt 发出去。

## Skills

目录结构：

```
.zenagent/skills/pdf-report/SKILL.md      # 项目级（优先）
~/.zenagent/skills/pdf-report/SKILL.md    # 用户级
```

文件格式（frontmatter 可选，手写解析，不依赖 YAML）：

```markdown
---
name: pdf-report
description: Build a PDF status report from a CSV
---
1. Read the CSV with read_file.
2. Generate the report with bash.
```

行为：

- `name` 缺省用目录名；`description` 缺省用正文第一行。
- 项目根优先于用户根，同名时项目赢并 warning。
- frontmatter 未闭合 / 行缺冒号 → 跳过该 skill 并 warning，不影响其他。
- prompt 里的索引上限 4KB（超出显示 "more skills omitted"）。
- `read_skill` 返回正文，上限 32KB，超出截断并标注。
- `read_skill` 是只读工具，不需要 `--yes`。
- `/skills` 列出当前可用 skills。

## Templates

```
.zenagent/commands/review.md
~/.zenagent/commands/review.md
```

```markdown
---
description: Review a file carefully
---
Review {{args}} and list concrete problems.
```

- `{{args}}` 替换成参数；`{{args:staging}}` 无参数时用默认值。
- 没有占位符时，参数作为新段落追加。
- 模板名来自文件名（可被 frontmatter 的 `name` 覆盖）。
- 项目级覆盖用户级。
- `/help` 列出模板；补全会把模板名并进斜杠命令。

## 热加载：`/reload`

会话中途新增/修改 skill、模板、扩展后，执行 `/reload`：

- 重新发现 skills/templates/extensions；
- 更新 `agent.tools` 与 system prompt；
- 刷新模板与扩展命令表；
- 运行中（agent 正在跑）拒绝 reload。

注意：补全列表（prompt_toolkit completer）在 REPL 启动时构建，`/reload` 不会重建它——
输入 `/` 补全可能看不到新模板，但直接敲命令是可用的。

## 源码对照

- Skills：`zenagent/skills.py`（`discover_skills`、`build_skill_tool`、`skills_prompt_index`、`default_skill_roots`）
- frontmatter：`zenagent/frontmatter.py::parse_frontmatter`
- Templates：`zenagent/templates.py`（`discover_templates`、`expand`、`default_template_roots`）
- REPL 展开与 `/reload`：`zenagent/cli/repl.py`（`discover_runtime`、`reload_runtime`）
- 对应测试：`tests/test_skills.py`、`tests/test_templates.py`、`tests/test_repl_logic.py`
