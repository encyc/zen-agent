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

## [v4] 2026-11-10 — 移除 taiji

- 删：节点 `app.taiji`（11 文件）与 `cli.rpc` 中的 `ops_taiji.py` / `TaijiOps`
- 删：9 条边（`app.assembly → app.taiji`、`app.taiji →` agent.loop/agent.rt/app.assembly/app.chat/project/prompts/types、`cli.rpc → app.taiji`）
- 删：`cycles_seen` 中的 `app.taiji ↔ app.taiji.supervisor`；基线中 7 条 taiji 违规随节点消失而解除
- 熵增：refresh deps 暴露 v0.12.1 合并后的存量漂移（`runtime.py`、`server.py`、`test_session.py` 等行数增长，非本次删除引入），按当前值 `--update-baseline` 重登记；基线 112→109（taiji 解除 7 项，漂移新增/恶化按现值登记）

## [v5] 2026-11-11 — 多角色 swarm 运行时

- 增：节点 `app.swarm`（8 文件：`__init__` / `manager` / `protocol` / `roles` / `room` / `roster` / `router` / `runner`），符号 `RoleManifest` / `SwarmStore` / `Router` / `TaskRunner` / `SwarmManager`
- 增：11 条出边 `app.swarm →` `agent.loop`（工具协议）、`agent.rt`（Agent/Session）、`app.assembly`（Runtime.bootstrap）、`app.transcript`（压缩）、`config`、`mcp`、`plugins`、`project`（工作区围栏）、`settings`、`skills`、`types`；`config` / `settings` / `types` 为 hidden
- 增：边 `cli.rpc → app.swarm`（`ops_swarm.py` 的 `swarm` 请求族与 `swarm:` 审批前缀路由）
- 改：`cli.rpc` 并入 `host.py`、`ops_swarm.py`、`slot.py`、`terminal.py`（此前未登记，`gov_diff` 报未映射）
- 改：不变量 +1（swarm 只在 app 层组合既有内核：角色 runtime 经 `Runtime.bootstrap` 装配、插件仍单向外呼、凭据只存 `~/.zenagent/settings.json`，manifest 只放 `${KEY}` 占位）
- 改：14 个既有未映射文件（13 个 tests + `scripts/demo_age.py`）登记为 `unmapped`，`gov_diff` 归零；`app.swarm/__init__.py` 作为 facade 孤儿登记豁免
- 熵增：基线 109→113（`app.swarm` 1 错 3 警 + `cli.rpc` 新增文件 3 项 + 未映射整理），无新增/恶化项超出登记

## [v6] 2026-11-11 — zen 节点登记 + 评审修复合入

- 增：节点 `app.zen`（5 文件：`__init__` / `fleet` / `prompts` / `roster` / `tools`），符号 `ZenFleet` / `WorkerHandle` / `RosterRow` / `worker_statuses` / `read_roster`
- 增：6 条出边 `app.zen →` `agent.loop`（派发工具协议）、`agent.rt`（worker 会话）、`app.assembly`（`bootstrap.prepare`/`RuntimeOptions`）、`app.transcript`（`open_session` resume）、`app.chat`（`chat.steer` 注入 router，TYPE_CHECKING，hidden）、`tools`（`text_result`）；按实际 import 核定，zen 不直接 import config/settings/types
- 增：边 `cli.rpc → app.zen`（`ops_zen.py` 的 `ZenFleet`/`latest_router`/`set_role`）
- 改：`app.swarm` 补 `events_tap.py` / `transcript.py`（此前未登记）；`cli.rpc` 补 `ops_zen.py`；manifest `version` 4→5
- 改：不变量 +1（zen 只在 app 层组合既有内核：worker 经 `bootstrap.prepare` 装配并 resume 到项目会话，结果经 `chat.steer` 注入 router、不等待；fleet 属于单个项目 slot）
- 评审修复（fix/zen-swarm-review 分支）：`swarm_notice` 补 `swarm:true` 并钉住服务端帧契约（union + 每帧 flag + handler 覆盖）；角色 prompt/skills 路径围栏（`_within`）；房间 append `fsync` + load 修复未终止尾行；桌面 worker drawer 失败文案/稳定 key + `zenBackgroundRun` 布尔白名单
- 熵增：`scripts/check_architecture.py` 仅校验 LOC 预算（对 `docs/architecture/allowlist.json`），本次无文件越限，门禁绿；manifest 为治理文档，不参与 LOC 门禁
