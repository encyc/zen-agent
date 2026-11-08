# 遗留债务清单（2026-11-08 清理分支终审 deferred minors）

> 原账本（SDD 工作区 progress.md）按执行流程在终审后删除；本文件是持久化账本。
> 每项：现象 / 影响 / 修复状态。修完一项就更新一项的状态行。

## 1. `merge_gate.diff_lines` 失败返回 -1 会放行 auto-merge

- 现象：`app/taiji/merge_gate.py` 的 `diff_lines` 在 git 失败时返回 -1（注释写"unknowable: refuse rather than guess"），而 `supervisor.merge` 的门是 `diff > AUTO_MERGE_MAX_DIFF_LINES` —— -1 直接放行，未知大小的改动会被 zen 自动合并。
- 影响：git diff 不可用时 zen 模式静默合并无界改动。
- 状态：**已修复**（2026-11-08）——auto 门显式拒绝负值；`block_reason` 同步补"diff 不可知"。

## 2. `append_meta` 仍在事件循环上同步写

- 现象：`agent/session.py` 的消息 `append` 已走锁 + `to_thread`，但 `append_meta` 在 loop 线程直接写文件；与线程内的消息写并发时理论上可撕裂行。
- 影响：低频（side-channel 记录），理论上的行交错窗口。
- 状态：**已修复**（2026-11-08）——`append_meta` 与 `append` 共用同一把锁 + `to_thread`，全部调用方 await。

## 3. T13(b)：REPL 未接 TranscriptBuilder

- 现象：`cli/repl.py` 的 `_EventPrinter` 仍手绘定稿渲染，享受不到视图层的孤儿配对/稳定行 id/notice 修复。
- 影响：REPL 与 TUI/RPC 的定稿渲染可能漂移。
- 状态：**已修复**（2026-11-08）——`_EventPrinter` 内建 `TranscriptBuilder`：每个事件先过 builder，工具行的成败标记取自卡片的 `settle_tool_status` 结果（超时/中断显示 `✗ (timed_out)` 等，而旧版会把超时 bash 标 ✓）；`render.tool_result_line` 增加可选 `status` 参数，旧格式逐字节不变。golden 兼容由既有 render/repl 测试钉住。

## 4. main 侧新增长是 taiji 功能流的新债

- 现象：allowlist 已登记（runtime 938、harness 801、messages 994、appStore 947），来源提交逐条注明在 `docs/architecture/allowlist.json`。
- 影响：神文件余量被 taiji on-ramp/太极进出功能吃掉；appStore 再增约 37 行功能动作。
- 状态：**挂账**——不属本修复轮（改动来自并行功能流，收缩需与其作者协调）；留给下轮架构评审，按账处理。
