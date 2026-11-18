# Git 审阅面板设计（融合 worktree / review / graph）

日期：2026-11-18 · 状态：设计已对齐，builder check 完成（四项拍板已并入正文，标"评审拍板"） · 关联：[TUI × GUI 对齐](2026-11-17-tui-gui-parity-design.md)

## 意图与动机

作者日常用桌面端（ADE 形态）驱动 agent 写代码，但仍然要开一个 VSCode，只为五件事：
看当前 worktree、看 git diff、review 最近修改的文件、terminal 操作、查看文件。目标是把
审阅链搬进桌面 GUI，让 VSCode 可以不再开。

关键事实（作者自述）：**几乎不手改代码**——要改就直接跟 agent 说；提交等写操作走终端。
因此本设计是纯只读审阅，不含任何编辑器能力。

## 现状

右侧 work rail（`desktop/src/ui/rightbar/RightPanel.tsx`）已有五个 tab 的雏形：

| tab | 组件 | 现状 |
|---|---|---|
| review | `ReviewPanel` | 数据源是**会话 transcript**（agent 本会话的编辑 diff），不是 git |
| files | `FilesPanel` | 目录树 + 只读高亮查看器，browse→open→back 单栏流 |
| worktree | `WorktreePanel` | worktree 列表 + 切换（切换 = `openProject` 重启 runtime），脏文件只有计数 |
| memory | `MemoryPanel` | 不变 |
| terminal | `TerminalPanel` | xterm + PTY，跨 tab 存活，不变 |

后端 git 面很薄：`zenagent/app/git.py` 只有 `git_status`（porcelain 计数，TTL 缓存）和
`git_worktrees` RPC。没有文件级状态、diff、log。

## 方案选型

考虑过三种做法：

1. **最小闭环**——ReviewPanel 数据源换成 git，加 `git_diff` 一个 RPC。改动最小，但丢
   worktree 切换入口，也没有提交历史。
2. **完整 Source Control**——方案 1 + log/graph，对标 VSCode + Git Graph 插件的布局
   （左列：分支 / Changes / Graph，右侧：点文件看 diff）。
3. **时间线聚合**——会话轮次与提交混排成一条时间线。差异化最大但工程量与 UX 风险最高。

**作者选了方案 2**（布局蓝本即其日常使用的 VSCode + Git Graph 截图），并从方案 3 偷一手：
Changes 列表里给当前会话动过的文件加"本会话改动"徽章。理由：审阅链完整、符合既有肌肉
记忆、diff 渲染与文件查看器全部可复用。

## 目标 / 非目标 / 反目标

**目标**

- work rail 增加全屏切换：全屏时 chat 收起，rail 占满窗口；⤢ 按钮与 Escape 切回。
- worktree 与 review 两个 tab 合并为一个 **Git** tab。
- Git 面板左列自上而下：**分支区**（当前分支 + worktree 列表与切换）、**Changes**
  （staged / unstaged / untracked 分组，逐文件 +/− 计数）、**Graph**（提交 DAG）。
- 左列整体垂直滚动；三个 section 可折叠；Graph 滚动到底部增量加载更早提交。
- 点 Changes 里的文件 → 右侧详情区显示该文件的 unified diff（working tree vs HEAD；
  staged 组下则是 `--cached`）。
- 点 Graph 里的提交 → 右侧详情区显示该提交的完整 diff（文件列表 + 逐文件 diff）。
- Graph 保真度：多 lane 彩色分支线、merge 结构清晰可读、refs 徽章（分支/tag）。
- rail 窄时左列与详情退化为 FilesPanel 既有的 browse→open→back 单栏导航。
- agent 在会话中改动文件后，Changes 列表与徽章自动更新（去抖刷新）。

**非目标**

- 文件编辑（反目标：成为编辑器。真需要编辑是另一个独立项目）。
- stage / unstage / commit / discard 按钮（终端与 agent 已覆盖）。
- incoming / outgoing（0↓ 15↑）——作者明确不要；意味着**不做 auto-fetch、不做远端操作**。
- blame、两提交对比选择器、side-by-side diff（现有渲染器是 unified，蓝本截图也是 unified）。

## 设计细节

### 面板结构

- `TOOL_TABS` 变为 **git / files / memory / terminal**；`review`、`worktree` 两个 kind
  移除，launcher 同步。Git tab 图标用 `BranchIcon`。
- Git 面板内部为双栏：左侧子边栏（约 260px，垂直滚动）+ 右侧详情区。宽度低于阈值时
  退化为单栏导航（详情页带返回按钮，复用 FilesPanel 的交互模式）。
- 全屏：rail 头部加 ⤢ 按钮；全屏状态存 `workPanelStore`（会话级，不持久化）。全屏的
  覆盖范围（评审拍板）：**rail 铺满窗口减去左侧应用 sidebar**——sidebar 保留（会话导航
  仍在），中栏整体隐藏（chat / swarm 房间页 / zen 中心页 / ComposerDock 一并收起）；
  实现为 `computeColumns` 的一个新布局分支，不是浮层。**Escape 语义**：agent 运行中
  Escape 维持既有"取消运行"绑定（全屏不抢），空闲时 Escape 退出全屏；⤢ 按钮任何时候
  都可切换。

### 左列三个 section

1. **分支区**（sticky 顶部）：当前分支名；worktree 紧凑列表（分支 + 当前标记 + 切换
   按钮），切换逻辑原样复用 `WorktreePanel` 的 `openProject` 守卫（agent 运行中禁用）。
2. **Changes**：三组标题（Staged / Changes / Untracked）带计数；行为状态字母 + 路径 +
   `git diff --numstat` 的 +/−；当前会话 transcript 中出现过的路径加"本会话"徽章
   （复用 `review.ts::collectFileChanges` 的路径集合）。空仓库 / 非仓库显示对应占位。
   **Untracked 语义**（评审拍板）：untracked 行的 +/− 为空（numstat 不产出）；porcelain
   以 `-uall` 展开目录但**上限 200 个文件**，超出则显示折叠的目录行（不可点，去 Files 看）；
   点击 untracked 文件 → 后端生成的**全新增行 diff**（整文件内容加 `+` 前缀，同样受
   渲染截断上限约束，截断时脚注给"在 Files 中打开"链接）——"这个文件的改变"就是它
   整个是新的，这与作者对点文件看改变的预期一致。
3. **Graph**：提交行 = lane 线（SVG）+ 圆点 + subject + author + 相对时间 + refs 徽章；
   滚动触底加载下一页（每页 100 条）。merge 提交画多条父边。
   **刷新策略**（评审拍板）：刷新按 commit hash **键控合并**——新提交前插，已加载的
   尾部保留，滚动位置不重置；新 merge 边到达时允许整段 lane 重排（低频，可接受）。
   不采用"刷新即重置回第一页"。

### 详情区

- 文件 diff：头部（路径 + +/− + 在 Files 中打开的链接），正文复用 `parseDiffHunks` +
  `DiffBlock`（从 ReviewPanel 提出为共享模块）。二进制文件显示占位说明；超长 diff 沿用
  现有 1200 行截断。
- 提交 diff：`git_show` 语义（commit vs first parent），文件列表 + 逐文件 diff，同一渲染器。

### 后端 RPC（`zenagent/cli/rpc/server.py` dispatch + app 层）

沿用 `app/git.py::_run_git` 的 shell-out 模式，超时与 TTL 缓存同现有约定；diff 按需实时取。

**不得阻塞事件循环**（评审拍板）：RPC server 的 `_handle_line` 是顺序 asyncio 循环，而
本设计会在 agent 工具调用完成（正在流式输出时）触发刷新轮询——因此四个新 op 一律经
`asyncio.to_thread`（或等价物）执行，超时上限调到 5s；超时或失败回**错误帧**（沿用
frameError 本地化提示），面板保留上一次数据并显示重试提示，**不返回部分数据**。

| op | 入参 | 出参 |
|---|---|---|
| `git_changes` | — | 分支名 + 文件级状态列表（path、staged/unstaged/untracked、plus/minus） |
| `git_diff` | `path`、`staged?` | unified diff 文本（单文件） |
| `git_log` | `limit`、`skip` | 提交列表（hash、parents、subject、author、date、refs 装饰） |
| `git_show` | `commit` | 文件级 diff 集（同 review 的按文件分组结构） |

类型镜像进 `desktop/src/rpc/types.ts`。i18n 文案走 `messages.ts`（en + zh）。

### 刷新

面板激活时、agent 工具调用完成事件后（接现有 refresh 基建）去抖 500ms 重取
`git_changes` / `git_log` 首页；diff 详情按点击实时取。手动刷新按钮保留。

## 分期

- **Phase 1**：全屏切换；Git tab 融合（分支区 + Changes + 文件 diff 详情）。后端
  `git_changes` + `git_diff`。
- **Phase 2**：Graph + `git_log` + 提交 diff（`git_show`）。

每期独立可交付，一个阶段一个 PR 粒度。

## 验收

- Phase 1：桌面端打开 Git tab，能看到分支、worktree、按组分类的改动文件与 +/−；点文件
  出可读的 unified diff（untracked 为全新增行 diff）；全屏审阅可用；agent 会话中改文件后
  列表与"本会话"徽章自动更新；agent 流式输出期间刷新不卡顿聊天流。
- Phase 2：对含 merge 的仓库，graph 正确渲染多 lane 彩色分支线与 refs 徽章；滚动加载
  更早提交；点提交看完整 diff。

## 测试

- 后端：`tests/test_app_git.py` 扩展（临时真仓库 fixture：文件级状态、numstat、diff、
  log 分页、refs 解析）；RPC 契约测试沿现有 `test_rpc_*` 模式。
- 前端：`desktop/tests` 组件测试（分组渲染、lane 布局算法、窄栏导航退化、全屏切换），
  e2e 沿 `desktop/e2e` 既有模式。

## 留给 builder 的决定

- lane 颜色板与分配细节（贪心 first-fit 即可）；SVG vs canvas（预期 SVG）。
- 详情区超长 diff / graph 长列表的虚拟化策略（沿用截断上限可接受）。
- 去抖参数、刷新事件接线方式、组件文件拆分与命名。
