# 架构变迁台账

## [v1] 2026-11-09 — init 骨架
- 增：骨架 37 节点 / 149 边（目录级自动聚类，待 enrich）

## [v2] 2026-11-09 — enrich：骨架 → 治理版
- 增：节点 `app.assembly`/`app.chat`/`app.transcript`（拆自 app 35 文件）、`agent.loop`/`agent.rt`（拆自 agent 15 文件）
- 改：39 节点全部补 resp/symbols；overview 与 6 条 invariants 落库
- 改：边表按 deps 重聚合，引入 hidden 标志（显示层修剪：base 工具扇入/i18n/cli 内部边不画线，悬停仍可见）
- 熵增：无新增（基线 110 项维持）

## [v3] 2026-11-10 — 插件系统接入
- 增：节点 `plugins`（6 文件，base 簇）与符号 `PluginManifest` / `ResolvedContributions`
- 增：边 `app.assembly → plugins`（plugin_wiring 把贡献翻译成 MCP 配置）、`cli.rpc → plugins`（ops_plugins）、`plugins → settings`（凭据，hidden）
- 改：`app.assembly` 并入 `plugin_wiring.py`；`cli.rpc` 并入 `ops_plugins.py`、`errors.py`；`mcp` 并入 `redact.py`
- 改：app 包环因 `plugin_wiring ↔ runtime`（TYPE_CHECKING 导入）扩为 11 模块；新增 facade 环 `plugins ↔ plugins.install`（同 `tools ↔ tools.fs` 模式），二者登记 `cycles_seen`
- 改：不变量 +1（插件单向外呼、`plugins/` 禁 import 上层、只有 `app/plugin_wiring.py` 桥接）
- 熵增：refresh deps 暴露 2026-11-09 基线之后的存量漂移（`ops_config.py` 854→941、`server.py` 744→792 等 10 项，非本分支引入），随本次 `--update-baseline` 按当前值重登记；基线 110→111（+plugins 环、+test_plugin_layering 孤儿，-scripts.import_graph 孤儿）
- 合并 `main@202d6f9`（无缝切换/懒加载会话）：`agent/session.py` 767 行使 `Session` 越过上帝类阈值、runtime/server/test 行数增长；均为 main 侧改动，按当前值一并重登记（基线 111→112），后续如需降熵另开任务拆 `Session`
