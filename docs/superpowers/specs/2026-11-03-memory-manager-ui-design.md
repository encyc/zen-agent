# Memory 管理 UI（/memory 命令）设计（2026-11-03）

关联：`2026-11-02-project-memory-design.md`（项目记忆库）。本文落实其三期规划的
`/memory` 命令，并推翻该文"不做记忆的 UI 管理面板，文本视图即可"的一条决策：
本期的目标就是这个面板，但保持"简单"。

## 意图

把 `/memory` 升级为项目记忆库（`.zenagent/memory/`）的轻量管理入口：TUI 弹窗
面板做增删改查，REPL 提供等价的文本形式。人工整理记忆不必再绕道模型
（memory_write）或手改文件。

## 目标

- TUI：`/memory` 弹出管理面板——条目列表（id / 标题 / 更新时间），`a` 新增、
  `e` 编辑、`x` 删除（Confirm 弹窗）、esc 关闭；正文编辑用标题 Input +
  正文 TextArea 的小编辑屏。
- REPL：`/memory` 打印索引；`/memory <id>` 打印全文；`/memory rm <id>` 直接
  删除（id 是显式给出的，回执注明 journal 已记录，事后可经 git/journal 追溯）；
  `/memory add [title]`、`/memory edit <id>` 交给 `$VISUAL`/`$EDITOR` 编辑。
- 所有写操作走 `MemoryStore`，journal 记 `source="ui"`（模型侧为 `"agent"`），
  审计链不断。
- 命令进 `/help` 与补全表；文案进 en/zh 两份 i18n。

## 非目标（本期不做）

- 面板内触发模型整理（旧设计里"依赖二期"的部分）。
- 面板内搜索/过滤（`MemoryStore.search` 已够，面板需要时二期加）。
- 全局 `~/memory/`（clawhub Memory skill 那套）——只管项目记忆库。
- 记忆自动总结机制——维持"是否记忆由模型经工具判断"的现状。

## 反目标

- 绕过 `MemoryStore` / journal 的任何写路径（哪怕为了省事直接 unlink 文件）。
- 面板长成多标签大而全的管理器——保持单屏 + 键位，向 `TodoEditor` 看齐。

## 方案取舍

- **A（选定）：TUI 面板 + REPL 文本视图。** 双前端各有其位，符合"每个 screen
  必有文本形式"的现有惯例（`/todo` 先例）；改动集中：1 个命令 + 1~2 个 TUI
  screen + REPL 渲染 + i18n。
- B（弃）：只做 TUI，REPL 仅提示。改动最小，但破坏双前端惯例，REPL 用户
  失去全部管理能力。
- C（弃/延期）：A + 面板触发模型整理。UI 与 agent 运行循环耦合，"模型正在改"
  期间的并发与刷新复杂，整理策略本身是另一个未讨论的产品问题。

## 已定的做法

- 命令名 `/memory`（沿用旧设计的命名）。命令层在 `app/commands.py` 新增
  `_memory`：空参数 → `CommandResult(screen="memory-manager")`；`<id>` / 状态
  查询类操作在命令层直接操作 store 并返回 message，两个前端行为一致。
- TUI：新 `MemoryManager` 屏（列表 + 键位，仿 `SessionPicker`/`TodoEditor`）；
  正文编辑屏被面板按键与 `/memory add|edit` 共用；写操作即时落盘。
- REPL：列表/全文/删除是纯文本渲染；add/edit 用临时文件 + `$VISUAL`/`$EDITOR`
  子进程交接（行式 REPL 无需 suspend；TUI 已有 suspend 版先例，`app.py:1746`）。
- source 区分：UI 写入 `source="ui"`，模型 `source="agent"`（`tools/memory.py:126`）。

## 留给 builder

- 编辑屏是独立 screen 还是面板内切换视图，按 `modals.py` 既有风格定。
- add/edit 的目标参数如何随 `CommandResult` 传递（显式附带字段 vs screen 名
  变体），倾向显式字段；REPL `$EDITOR` 辅助函数的落点（`cli/` 共享 or repl.py 私有）。
- 测试：命令层进 `test_app_commands.py` / `test_repl_commands.py`；TUI 屏仿
  `test_tui_todo.py` 新建 `test_tui_memory.py`。
