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
- `/skills` **先重扫 skills 根目录再列出**（纯文件读取，15 个 skill 约 0.7ms），
  所以列表永远是磁盘真相，不会显示启动时的旧快照：
  - **TUI/app**：发现变化就同时换掉 `read_skill` 的工具清单和 prompt 索引，立刻可用；
  - **REPL**：只重扫展示（REPL 的发现是一次性打包 skills/templates/extensions 的，
    在展示命令里就地重建等于重跑扩展），列表尾部会提示
    `changed on disk (...)`: `/reload makes them available to the model`；
  - agent 正在运行时两处都只报告变化、不动工具（见下文「热加载」）。

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

## 热加载：`/skills` 与 `/reload`

会话中途新增/修改 skill、模板、扩展后，不用重启，两个命令分两档：

| 命令 | 重扫什么 | 代价 | 运行中（agent 正在跑） |
|---|---|---|---|
| `/skills` | 只重扫 skills 根目录 | 纯读取，0.7ms/15 个 | 只提示「跑完生效」，**不改** `agent.tools` |
| `/reload` | skills + templates + extensions（TUI 还会重连 MCP） | 会**重新执行**扩展代码 | 直接拒绝 |
| `/mcp` | 重连 MCP server 并重建工具表 | 网络往返，可能数秒 | 跳过刷新、只列状态 |

TUI/app 的 `/skills` 会把变化直接采纳（重建 `read_skill` + 索引）；
REPL 的 `/skills` 只重扫展示并在有变化时提示 `/reload`——REPL 的发现是
skills/templates/extensions 一次打包的，就地采纳等于在展示命令里重跑扩展。

`/reload` 之后：

- 重新发现 skills/templates/extensions；
- 更新 `agent.tools` 与 system prompt；
- 刷新模板表（REPL 还会刷新扩展命令表；TUI 的扩展命令尚未接线，见 known gaps）；
- 旧的扩展 hooks 先退订再重订，不会重复投递事件。

`/skills` **故意**不做完整 reload：`load_extensions()` 内部是 `exec_module()` + `register(api)`，
把一个会执行用户代码的动作挂到纯展示命令上，等于每敲一次 `/skills` 就重跑一遍扩展。
所以它只重扫 skills（纯读），并把 skills 那部分工具/索引换掉——避免「列表显示 16 个、
`read_skill` 只认识 15 个」这种不一致。

`--no-skills` 启动时 `/skills` 只提示已禁用，不扫盘。

注意：补全列表（prompt_toolkit completer）在 REPL 启动时构建，`/reload` 不会重建它——
输入 `/` 补全可能看不到新模板，但直接敲命令是可用的。

## 源码对照

- Skills：`zenagent/skills.py`（`discover_skills`、`build_skill_tool`、`skills_prompt_index`、`default_skill_roots`）
- frontmatter：`zenagent/frontmatter.py::parse_frontmatter`
- Templates：`zenagent/templates.py`（`discover_templates`、`expand`、`default_template_roots`）
- REPL 展开与 `/reload`：`zenagent/cli/repl.py`（`discover_runtime`、`reload_runtime`）
- TUI/app 层：`zenagent/app/runtime.py`（`_recompose`、`refresh_skills`、`rediscover`）、
  `zenagent/app/commands.py`（`_skills`、`_reload`）
- 对应测试：`tests/test_skills.py`、`tests/test_templates.py`、`tests/test_repl_logic.py`、
  `tests/test_app_discovery.py`
