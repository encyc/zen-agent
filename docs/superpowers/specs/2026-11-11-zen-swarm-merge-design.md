# Zen × Swarm 合并设计（feat/zen-mode + feat/swarm-runtime → merge/zen-swarm）

日期：2026-11-11
状态：待批准
输入：`feat/zen-mode`（10 commits，ddc49c6…f4db7c9）、`feat/swarm-runtime`，两者均从 main `2ad6391` 分叉。注意 swarm 分支仍在推进：设计调研时为
`92a8200`（18 commits），执行前已到 `b17b307`（21 commits，含房间帧修复与
任务详情 UI）；**合并输入取执行时分支 tip**（见"方案选型"末条），执行前
重新核对冲突清单与行数预算。

## 意图与动因

zen（单 router session 异步统筹项目 worker）与 swarm（全局房间 + 每角色常驻
Runtime + @mention 派发）是同一枚硬币的两面：都解决"多 agent 协作、单点认知"。
两条分支各自完整交付，swarm 甚至预埋了三态 workspace 切换器并把 zen 态留成
占位页（`ZenPlaceholder` 注释明说"撑到 zen worktree 合并自己的 surface 为止"）。
本次合并让两个功能在同一个 build 里共存且互不破坏，为下一阶段的公共模块
抽象（两套 roster、两套 semaphore/调度、两套 worker 装配）留出干净边界。

用户已拍板的两个方向（见"方案选型"）：

1. **模式开关**：三态切换器收编 —— `workspaceMode: 'coding'|'zen'|'swarm'`
   是唯一模式状态，zen 的 ✨ ZenToggle 退役。
2. **跨模式语义**：fleet 后台继续跑 —— 切走 zen 只切 UI，不停 worker；
   fleet 的关闭必须由显式动作触发。

## 目标

- 两个分支全部功能在合并后原样可用：zen 的 enable/disable/六工具/名册/抽屉/
  唤醒+批处理；swarm 的房间/角色/任务链/审批/设置页/e2e。
- 两分支全部测试通过（Python 预估 2620 + swarm 新增 ≈ 45+；desktop 332 +
  swarm 新增；e2e swarm.spec.ts 6 例）。
- 全门禁绿：`scripts/check.sh`（ruff + 架构预算 + pytest）、desktop
  `pnpm test && pnpm build`、style tokens lint。
- 一套模式状态、一个持久化 key，旧 key 一次性迁移。

## 非目标（anti-goals）

- **不做公共抽象**：不合并 `app/zen/roster.py` 与 `app/swarm/roster.py`，
  不统一 semaphore/调度器/worker 装配 —— 那是用户明说的下一阶段工作。本合并
  刻意保持两包零交叉 import。
- 不改任一分支的后端行为（fleet、房间、审批、生命周期逻辑都不动）。
- 不给 zen 补 dev-bridge mock / e2e（zen 分支本就没有；另立项）。

## 约束

- 架构预算只许降基线（`docs/architecture/allowlist.json`）。合并后关键文件
  实测预估：`appStore.ts` ≈1183 > 1175（**超**，必须抽离）；
  `runtime.py` = 1061 = 上限（零余量，解冲突时一行都不能多）；
  `Sidebar.tsx` ≈854 → 删 ZenToggle 后 852 ✓；`types.ts` ≈796 ✓。
- `test_layering.py` 钉死 app/ 不得 import cli/（swarm 的 manager 在 app/
  内自建 broker，合规；合并不得引入越层 import）。
- RPC 契约测试三方刮源码：Python dispatch 的 kind 集合 == desktop
  REQUEST_KINDS，PROTOCOL_VERSION 两侧一致（取 zen 的 3；swarm 不依赖版本号）。
- 保留双方完整提交历史（merge commit，不 squash）。

## 方案选型（已定）

**合并策略** —— 集成分支：新 worktree `merge/zen-swarm` 从 main 出发，先
`git merge feat/zen-mode`（fast-forward，main 就是 merge-base），再
`git merge feat/swarm-runtime`（真冲突在此解决）。绿了之后由用户决定是否推进
main。备选：直接在 main 上合并（省一步但无验证缓冲，弃）；rebase 某一支
（重写已评审历史，弃）。

**模式状态** —— `workspaceMode` 收编（用户选）：唯一状态 + 唯一 persist key
`zen.workspaceMode`（沿用 swarm 的）。`zen.ui.mode` 迁移：`loadWorkspaceMode()`
在新 key 缺失时读旧 key（值 'zen' → 'zen'，其他 → 'coding'），读到即写新 key。
备选：双开关并存（两状态打架，弃）；zenMode 收编 swarm（三态退化二态，弃）。

**跨模式 fleet 语义** —— 用户可配置，默认后台继续跑（用户定）：Settings→Zen
提供开关 `zen.fleet.background`（默认 **true** = 离开 zen 只切 UI 不停
worker）；切到 false 即回到"离开即停"。显式停止入口（名册 router 行按钮）
在两种取值下都存在。备选：硬编码后台（用户改为可配置）；硬编码即停（否）。

**合并输入** —— 执行时分支 tip（用户确认：swarm 后续增量小、不影响架构
方向，不阻塞计划）。执行开始时快照当时 SHA 并锁定，中途分支再动不追；
执行前重核冲突清单与行数预算（swarm tip 已从调研时的 92a8200 走到
b17b307+，e2e 已 6→7 例，均属小增量）。备选：钉死调研 SHA（丢已完成
修复，弃）；等收尾（用户明确不等，弃）。

## 设计

### 1. 文本冲突解决（4 个文件）

- `desktop/src/rpc/types.ts`：REQUEST_KINDS 并集（`'swarm'` + `'zen_fleet'`）；
  `ServerFrame = ResultFrame | EventFrame | ZenFleetFrame | 四个 Swarm 帧`；
  PROTOCOL_VERSION = 3。zen 的 `ZenUiMode` 类型随 ZenToggle 退役而删除。
- `desktop/src/state/stores.ts`：删 zen 的 `zenMode/setZenMode/zen.ui.mode`；
  保留 swarm 的 `workspaceMode/setWorkspaceMode/zen.workspaceMode` +
  迁移读取；`loadWorkspaceMode` 加旧 key 回退。
- `desktop/src/state/appStore.ts`：import 并集；handleFrame 头部 swarm 分流
  （**必须保持在 project/session 过滤之前**，swarm 帧无标签）+ 尾部 zen_fleet
  分支（带标签，走 `frameBelongsTo`）；bootChild 尾部 `syncZenState` 改由
  `workspaceMode === 'zen'` 触发；swarm 的 `_useSwarmSender` 注册一并保留。
- `CHANGELOG.md`：两个 Unreleased 合成一节，Zen 模式与 Swarm runtime 两个
  小节并列 + 合并说明一句。

### 2. 模式统一接线（本合并的核心新代码）

- 删 `ui/ZenToggle.tsx`、`ui/pages/ZenPlaceholder.tsx(+css)`、i18n 死键
  （`zen.placeholder`、zen 的 toggle 相关键）。
- `App.tsx`：三态 ternary 中 `zen` 分支渲染与 coding **相同的对话区**
  （Topbar + ChatView + InputBar + workPanelToggle）——zen 的 surface 本来
  就是"router 会话 = 普通对话"，只是 sidebar 换名册。`<WorkerDrawer />`
  维持全局挂载（workerPath 为 null 时自渲染 null）。
- `Sidebar.tsx`：保留 swarm 的 `<ModeSwitcher />`；删 `<ZenToggle />`；
  名册换肤条件维持 `zenStore.enabled`（RPC 真实状态驱动，非 UI 模式）。
- `state/zenActions.ts`：
  - `syncZenState`：改读 `workspaceMode === 'zen'` 触发自动 enable（逻辑不变）。
  - `setZenMode` 重命名为 `switchZenPresence(get, entering: boolean)`：
    entering=true → 现有 enable 流程（成功跟随 session、刷 transcript）；
    entering=false → **仅本地状态，不发 disable**（用户拍板的后台语义）。
  - 新增 `stopFleet(get)`：发 disable，成功后 applyRoster(false) + 跟随
    返回的 session + 刷新（沿用原 disable 路径）。
- `ModeSwitcher` 经 appStore 新 action `switchWorkspaceMode(mode)`：
  `setWorkspaceMode(mode)` 持久化 + `mode === 'zen'` 时
  `switchZenPresence(true)`；切走 zen 时不调 fleet（隐式后台保活）。
- **显式停用入口**：`ZenRoster` router 行加一个小停止按钮（i18n
  `zen.stopFleet` zh"停用 Zen"/en"Stop Zen fleet"，`zen.stopFleetDone`），
  点击调 `stopFleet`。后台运行可见性（ModeSwitcher zen 段活动点）记为可选
  打磨，不阻塞。

### 2b. Settings 页新增 Zen / Swarm 区域

- Settings 导航新增两个 tab（`zen`、`swarm`，排在 agents 附近）。
- **ZenSettings**（`ui/pages/ZenSettings.tsx`）：一个开关绑定
  `SettingsState.zenBackgroundRun`（persist `zen.fleet.background`，默认
  true，非法存量值回落 true）——开=允许后台继续，关=离开 zen 即停；附一句
  "显式停止始终可用"。开关只影响**下次离开 zen 的迁移**（`onZenModeLeft`）：
  运行中改开关不立即启停 fleet。
- **SwarmSettings**（`ui/pages/SwarmSettings.tsx`）：v1 是说明卡（房间为
  全局、跨项目常驻；数据落在 `~/.zenagent/swarm/`）+ "管理 Agents" 按钮
  跳转 agents tab（`onNavigate` prop 由 SettingsPage 注入）。并发上限等
  swarm 专属设置需要后端配置面，留给公共抽象阶段。
- 离开迁移逻辑收敛在 `zenActions.onZenModeLeft(get)`：
  `zenBackgroundRun === false` 时执行 `stopFleet`（含跟随恢复 session），
  否则不发任何 RPC；`appStore.switchWorkspaceMode` 在 `prev === 'zen' &&
  mode !== 'zen'` 时调用它。

### 3. 后端

**零行为改动。** 合并即得：`server.py` 分发 `zen_fleet`（slot 层），
`host.py` 分发 `swarm`（host 层）+ `swarm:` 前缀审批路由（保持在 slot 解析
前）；`slot.py`（zen 的 pinned/fleet.close）与 swarm 无交集；
`runtime.py` 双方挂钩共存（zen 插 `fleet_tools` 于 tools 中段，swarm 的
`tools_allow` 过滤 + `extra_tools` 追加在末尾；角色 runtime 无 zen_fleet，
过滤不误伤）。`host.close()` 先关 swarm manager 再关 slots（fleet 在
slot.close 内关），顺序安全。

### 4. 行数预算落地

- `appStore.ts`（自动合并实测 1186 → 需 ≤1175，缺口 ~11 行）：主路径 =
  swarm 的 sender 注册（`_useSwarmSender({...})`，appStore.ts:961 附近
  ~6 行）移进 `state/swarmStore.ts` 或 `state/swarmWiring.ts` 装配缝
  （`_useSwarmSender` 本就是为此设计的测试缝），**并** 把 handleFrame 的
  zen_fleet 分支（~5 行）下沉到 `state/zenActions.ts` 的具名函数
  （如 `applyFleetFrame(frame)`）；两处合计腾出 ~11 行。若个别解冲突写法
  仍有出入，再评估把 swarm 分流同样下沉。
- `runtime.py`：合并恰好 1061 = 上限，解冲突以"零新增行"为纪律；若冲突
  标记清理净增行数，在同文件等量精简（注释收紧）而不升 allowlist。

### 5. 测试

更新：
- `desktop/tests/zenActions.test.ts`：`switchZenPresence` 语义（entering
  enable / 离开发 nothing）、`stopFleet` disable 流、`syncZenState` 改读
  workspaceMode。
- `desktop/tests/workspaceMode.test.ts`：加迁移用例
  （无新 key + 旧 key `'zen'` → workspaceMode 'zen' 且写回新 key）与
  `zenBackgroundRun` 默认值/persist 用例。
- `desktop/tests/zenActions.test.ts` 加 `onZenModeLeft`：关开关 → 发
  disable 并跟随恢复 session；开开关 → 零请求；运行中改开关不影响当次状态。
- `tests/test_rpc_contract.py`：预期无需改（swarm 未触碰该文件，合并直接取
  zen 的 v3 断言；kind 并集后 py==ts 自然成立——grep 同时覆盖 server.py 的
  `zen_fleet` 与 host.py 的 `swarm`）。若 fail 再按实情补。
新增：
- `desktop/tests/zenSidebar.test.ts` 或 zenActions：后台语义回归
  （切到 swarm 后 zenStore.enabled 仍 true、无 disable 帧发出）。
照跑（不改动，验证不破坏）：
- swarm 全套（test_swarm_*、test_rpc_swarm、test_runtime_role_assembly、
  test_project_scoping、desktop swarmStore/workspaceMode、e2e swarm.spec.ts）
  与 zen 全套（test_zen_*、test_app_chat deliver、desktop zen*；swarm e2e
  以执行时实际用例数为准，调研时 6→7 例）。

### 6. 文档

- CHANGELOG（见 §1）。
- `docs/architecture.md` 模块表补 swarm 包行（zen 分支已有表，swarm 未写，
  合并补齐）；`docs/architecture-full.html` 补 swarm 节点/边（沿用 zen 的
  画法，规模从简：swarm 包 → host/roles/room 三四节点）。
- `docs/README.md`（swarm 改过）核对 zen 指南链接在列。
- 两份 guide（zen-mode.md / swarm.md）互相各加一句"另一种多 agent 形态"
  的指引（各一行，不展开）；zen-mode.md 说明离开行为开关的位置与默认值。
- CHANGELOG 合并说明补一句：zen 离开行为从"toggle 即停"改为"默认后台继续
  + Settings 可配回即停"（对老用户是行为变化，需显式告知）。

## 风险与对策

| 风险 | 对策 |
|---|---|
| runtime.py 恰在上限，冲突清理易超 | 零新增纪律；超出则同文件等量精简 |
| appStore 合并后仍超预算 | swarmWiring 抽离已在计划内；再不够就把 zen_fleet 分支也挪 zenActions |
| ModeSwitcher 驱动的 enable 在 busy slot 失败 | 沿用 zen 现有回滚（本地切回 coding + 错误通知） |
| 旧 key 迁移把陌生值带进三态 | loadWorkspaceMode 白名单回落 coding（swarm 已有此逻辑，只需前置旧 key 读取） |
| swarm e2e/单测对 App 结构敏感 | App.tsx zen 分支改动不触碰 swarm 分支渲染路径；e2e 照跑兜底 |

## 交付物

分支 `merge/zen-swarm`（两个 merge commit + 冲突解决/接线提交），全门禁绿，
本设计 + 实施计划（`docs/superpowers/plans/2026-11-11-zen-swarm-merge.md`）
一并入库。main 的推进由用户在验收后决定。
