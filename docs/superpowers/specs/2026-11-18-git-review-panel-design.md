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
| worktree | 并入 Git 的**分支区** | worktree 列表 + 只读预览（点行看该 worktree 的 changes/diff，不重启 runtime） |
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
- Git 面板左列自上而下：**分支区**（当前分支 + worktree 列表与预览）、**Changes**
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
  都可切换。（第二轮改为真全屏：sidebar 也收起，见评审修订 7。）

### 左列三个 section

1. **分支区**（sticky 顶部）：当前分支名；worktree 紧凑列表（分支 + 当前标记 + 切换
   按钮），切换逻辑原样复用 `WorktreePanel` 的 `openProject` 守卫（agent 运行中禁用）。
   （第二轮去掉 sticky：侧栏滚动时整段随 Changes/Graph 滚走，限高列表不会在吸顶头下
   露出半截；行主体改为只读预览，按钮改名"打开"，见评审修订 9。）
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

## 评审修订（2026-11-18 返工）

首轮实现交付后按实际使用反馈返工，设计与验收相应补充：

1. **分支区可折叠、限高、按时间倒序**。分支区头部即折叠开关（chevron + 当前分支 +
   worktree 数），折叠状态记在 `localStorage`；worktree 列表 `max-height: 168px`
   内滚，不再把 Changes / Graph 顶下去；头部不再 sticky（整段随侧栏滚走）。排序键是分支 tip 的提交时间——worktree 没有
   创建时间，而"从当前 HEAD 新开的分支"本来就该排最前；`worktree_overview` 增加
   `timestamp`（一次 `git log --no-walk` 取全部 head），未知/未出生 head 为 0 排最后，
   同时间用分支名稳定排序。
2. **Changes 点文件右侧空白**是 `false ?? element` 的优先级 bug：两个详情分支在未选中
   时返回的是 `false` 而非 `null`，`??` 不会落空，整个详情区被渲染成空。改为三元表达式
   显式返回 `null`，并补了宽/窄两种布局的回归断言（原有 e2e 在修复前失败、修复后通过）。
3. **Graph 重做**。原实现给每一行画满所有 lane，且引用了不存在的
   `--dsw-alias-state-info-primary` / `state-warning-primary`，颜色回退成灰黑，观感是
   "彩色竖条码"。现在 lane 布局输出 `incoming / outgoing / through / converge` 四个
   集合，渲染器只画真正穿过本行的线：直通线、汇入点的 S 弯、merge 的第二父边；同一
   提交被多条 lane 等待时收敛到点，不再在末尾吊一条悬空线。行内是 VSCode 风格的两行：
   refs 徽章（当前分支实心蓝、tag 灰）+ subject，下一行短 hash、作者、相对时间；
   选中行蓝底 + 左侧 2px 蓝条；调色板全部换成真实存在的 static token。Graph 头带
   已加载提交数。
4. **提交详情逐文件折叠**。多文件提交默认展开第一个文件、其余折叠成文件清单；每个文件
   头部是 `aria-expanded` 的折叠按钮，详情头提供"全部展开 / 全部折叠"。单文件提交仍
   直接展开，保留"点提交就能看到 diff"的手感。
5. **增量翻页单飞**。发送端与 sentinel 都会在底部触发 `loadMoreLog`，并发两页会把
   `logSkip` 前进两次而静默丢一页；store 增加单飞守卫，并在刷新重排 head 时丢弃在途
   的旧页（`logSkip` 游标比对）。
6. 宽栏左列从 260px 调到 300px，给 refs、作者与时间留出可读宽度。

验收补充：分支折叠与限高、worktree 倒序、提交文件折叠/全展开全折叠均进入 e2e；
lane 的 through/converge 与并发翻页进入 node --test。

### 第二轮（同日，按实际使用反馈）

7. **全屏改为真全屏**：`AppFrame` 的 fullscreen 分支不再渲染左侧 sidebar（也不渲染它的拖拽把手），rail 独占网格唯一一轨；`computeFullscreenColumns(viewport)` 只回
   `{sidebar: 0, center: 0, rightbar: viewport}`。全屏是审阅面，作者明确不在全屏里切
   会话；Escape / ⤢ 退出时恢复原三栏与 sidebar。`RightPanel` 的宽窄判断改用
   `window.innerWidth`，不再减 260。
8. **Graph 走全部本地 ref**：`git_log` 改为 `git log --branches --tags HEAD`，未合并的
   本地分支（`git-review-panel`）也出现在图里，tip 上有对应的 ref 徽章；远端 ref 不参与
   （面板不 fetch）。测试仓库里补了"未合并分支必须可见"的断言；分页/顺序测试按 git 的
   日期序更新。
9. **分支区从"切换"改为只读预览**：行主体（分支名 + 时间）现在是可点按钮，点击以
   只读 scope 预览该 worktree 的 changes/diff；`git_changes` / `git_diff` 增加可选
   `worktree` 参数，服务端用新的 `workspace.worktree_paths` 校验只接受本仓库已知
   worktree（拒绝任意目录），陈旧/伪造路径回错误帧。Changes 上方出现预览条（分支名 +
   "返回当前工作树"），窄栏文件详情头带 scope 徽章；预览时隐藏"本会话"徽章与"在文件面板
   打开"（它们属于当前项目，不属于被预览的 worktree）。原来的"切换"按钮语义不清，先改名
   "打开"；第三轮按反馈直接删除了该按钮与 `openProject` 切换路径——列表里所有行都只做
   只读预览，当前 worktree 行点回即回到默认 scope。
10. **Graph lane 宽度自适应**：lane 数 >6 用 11px、>10 用 9px，避免多分支时把 subject
    挤没；同一 path 在"当前"与"预览"两个 scope 的 diff 缓存键分离。

### 第三轮（同日，树形视图与折叠）

11. **Changes / 提交详情 / Graph 支持 tree 与 list**：工具栏用紧凑的图标分段开关切换，
    选择分别记在 `localStorage`（`zen.git.changesView`、`zen.git.commitView`、
    `zen.git.graphView`）。tree 视图复用 `fileTree.ts` + `FileTreeList`：目录行是
    chevron + 文件夹图标 + 递归文件数，文件行保留完整路径作为可达名称、显示 basename，
    并按深度缩进；list 视图回到 VSCode 的扁平文件列表。Graph 的 list 模式隐藏 lane 列，
    只保留 refs / subject / 元信息，长历史更好扫。
12. **同一目录在不同列表里独立折叠**：Changes 的三个分组（已暂存 / 更改 / 未跟踪）各自
    持有一套折叠目录集合；提交详情另有一套。折叠"更改"里的 `desktop` 不会连带折叠
    "未跟踪"里的 `desktop`。
13. **分组与 Graph 可折叠**：三个 changes 分组头、Graph 头都变成 `aria-expanded` 按钮，
    带 chevron 与计数；Graph 的折叠状态持久化（`zen.git.graph.collapsed`）。分组折叠只
    隐藏该组行，Graph 折叠只保留表头，便于在窄栏里把空间让给列表或 diff。
14. **红绿灯让位**：真全屏时工作面板头在 darwin 下留 78px 左内边距，避免窗口控制按钮
    压住标签与操作；`data-work-panel-header` 作为 e2e 断言锚点，其他平台仍是 10px。

验收补充（第三轮）：Changes 的 tree/list、两个同名目录独立折叠、分组/Graph 折叠、
提交详情的 tree/list 与 macOS 全屏内边距均进入 e2e。
