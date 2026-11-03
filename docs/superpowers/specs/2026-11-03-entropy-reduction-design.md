# 熵减:拆解 ZenAgentApp 及两个次大类(设计)

2026-11-03 · 目标文件:`zenagent/cli/tui/app.py`(1903 行,十摊职责离场)、
`zenagent/app/chat.py`(761 行,三块状态离场)、`zenagent/app/runtime.py`(局部)、
工作区卫生两件。

## 意图与为什么

2026-11-03 的熵增体检结论:这个仓库的熵防御集中在文档层(守卫测试钉住所有
公开数字),而代码层的熵集中在三个大类上:

- **`ZenAgentApp`**(cli/tui/app.py):114 个方法、1640 行方法体、10 类职责混于
  一类,是第二名的 2.5 倍;全史 70 次提交(占 411 次的 17%),为改动最频繁文件。
  实测归堆:十摊可拆职责合计约 1360 行,core(生命周期/组装/状态刷新)281 行。
- **`ChatController`**(app/chat.py):44 个方法,附件管理、follow-up 队列、
  steering、子代理视图状态四摊内聚状态机挤在一个类里。
- **`Runtime`**(app/runtime.py):组装根,已塞入与组装无关的 metrics 关注点,
  且仍在活跃改动(近期 60 提交中 4 次)。

工作区有两件顺手杂物:根目录两份知乎草稿(`zhihu-zenagent.md`、
`zhihu-zenagent-v2.md`,未追踪),`.gitignore` 一处未提交修改且缺行尾换行。

`tui/app.py` 近期已降温(最近 60 次提交仅 3 次),是低冲突拆分窗口;仓库已有
`screens/`、`widgets/` 的组件抽取惯例可循。

## 目标

- **app.py 十摊职责全部离场**。留在 app.py 的只有一张冻结清单:core(生命周期/
  组装/状态刷新)、审批/提问管线、弹窗原语(`_modal_result`/`_confirm`/
  `_prompt_rename`/`open_menu`)、滚动 query 委托、分发注册表、组件的同名委托壳。
- 十摊职责各归其位,照既有惯例:`screens/`(真组件)、`widgets/`(真组件)、
  新增 `cli/tui/app_parts/`(分域 mixin,存放必须挂在 App 实例上的动作与状态逻辑)。
- `_dispatch_command` 的 17 分支 `elif result.screen ==` 链替换为一张注册表字典,
  并配一条守卫测试钉住"每个 result.screen 键都有归属"。
- `ChatController` 不再持有附件、待处理队列(含 steering)、子代理视图状态——
  三块状态各自成为独立对象;`chat.py` 只留提交/事件/压缩主线。
- `Runtime` 的 metrics(`_start_metrics`/`_flush_metrics`)抽出为独立模块。
- 全程每个提交 `bash scripts/check.sh` 全绿(ruff + 全部测试),行为零变化。
- **验收是结构不是行数**(用户裁决:行数不重要,模块拆出来、组织有条即可):
  行数只作前后报告写入 CHANGELOG,不作门槛;门槛是上面的离场/冻结清单。

## 非目标

- 不改任何用户可见行为:按键、命令、提示文案(i18n)、启动顺序都不动。
- 不动内核五层(`types/events/providers/agent/tools`)。
- 文档快照索引(feature-inventory / eval-landscape / superpowers 归档)——用户已
  明确"文档先不管",不在本计划内。
- `providers/catalog.py` 的 9 处 type-ignore 清理——独立的小活,另行安排。
- 不新增依赖。

## 反目标(做出来算失败)

- mixin 拆完文件、类语义没变——十摊里凡是存在真实组件边界的(选择器、横幅、
  视图)必须成为独立对象,而不是只把代码搬家;`app_parts/` 只收"必须挂在 App
  实例上的状态+动作"。
- 任何一步 gate 红(失去可二分性)。
- 注册表字典做成需要二次跳转才能找到 open 函数的间接层——查找路径不得比
  现在的 elif 链更绕。
- 公开 API 面变化:`zenagent/__init__` 导出被守卫测试钉住,不得增删。
- **把承逻辑的组件做成纯代码搬家**:两者的分界线是"有没有自己的状态或可独立
  测试的逻辑"。picker/横幅/视图/提示匹配是组件(独立对象或模块级纯函数);
  而 open_* 编排胶水只是"取 app、push screen、回调改状态"的几行——以模块级
  函数(参数带 `app`)落进 `screens/` 域模块即可,不强行包对象。

## 约束

- **公开方法面冻结**:37 个测试文件引用 `ZenAgentApp`,其中 38 处直接调用
  内部方法(`open_session_picker`、`set_appearance`、`_confirm`、
  `request_tool_approval` 等)。所有方法名必须继续可从 `ZenAgentApp` 实例直达
  ——mixin 继承即可满足,组件化对象需要 App 上留一行同名委托。
- **测试触碰面一并冻结**(builder check 裁决):测试除方法外还直接读写实例
  属性(`app._viewing`、`app._menus`、`app._theme_spec`、`app._language_spec`、
  `app._appearance_warnings`、`app._work_timer`)并调用 `app._band_width()`、
  `app._command_matches()`(tests/test_i18n_app.py:93)、`app._refresh_status()`。
  凡测试触碰的名字,必须继续可从 App 实例直达(mixin 方法或同名 property);
  组件内部其余状态自由归属。改测试只许改 import 路径,不许改断言与访问路径。
- **BINDINGS 留在 App 类**:Textual 的 `action_*` 解析走 App,按键表不动;
  mixin 上的 `action_*` 方法自然满足。
- 方法内懒 import screen 是刻意的(启动时间),搬动时保持懒加载写法。
- 注释里对 omp 研究文档的引用(`docs/research/omp-cli-study/…`)随代码一起走,
  不删不改。
- 守卫测试钉住 README/docs 全部数字(47 命令、17 provider 等),本计划不触碰
  这些文档。
- `scripts/check.sh` 是唯一闸门;TUI 行为契约就是现有 TUI 测试(headless
  `run_test`),不要求人工跑 TUI 验收。

## 方案(候选与取舍)

- **方案 A(选定):分域 mixin + 真组件 + 分发注册表。** 十摊中,选择器/横幅/
  视图类(约 5 摊)抽成 `screens/`、`widgets/` 真组件;必须读写 App 实例状态的
  动作群(队列/草稿/显示开关、模型切换、外观切换、hint 导航)按域放
  `cli/tui/app_parts/*.py` 的 mixin,`ZenAgentApp` 继承之;elif 链换成注册表。
  代价:mixin 仍是"一个大类分文件"——类熵只软化未根除;收益:零行为风险、
  方法名天然保留、每步 diff 小且可独立回滚。
- **方案 B(弃):全部委托对象。** 真解耦,但 114 个方法要留 shim,
  `run_worker`/`call_later`/`query` 的 self 语义处处要过桥,在一周 411 commit
  节奏的仓库上是数周级 diff。若 mixin 落地后某域仍持续膨胀,可单域升级为
  委托对象——那是后续决定,不在本计划。
- **方案 C(弃):只拆前三摊(瘦身)。** 用户已选彻底分层(方案 A 的终点)。

## 决定(拆分终点与去处)

实测归堆(方法数/方法体行数)与去处:

| 摊 | 方法 | 行数 | 去处 |
|---|---|---|---|
| 弹窗编排(12 个 open_* + 会话动作) | 16 | 324 | open_*/picker 进 `screens/` 域模块;App 上留注册表项 |
| 输入动作/队列/转向 | 22 | 302 | `app_parts/queue.py`、`app_parts/drafts.py`、`app_parts/display.py`(能一行委托 `self.chat` 的直接删) |
| 模型/Provider/Key | 13 | 145 | picker 进 `screens/providers.py`;`_apply_model`/`_cycle_model` 进 `app_parts/models.py` |
| 输入补全提示 | 8 | 145 | 匹配逻辑并入 `cli/completion.py` 复用;hint 条动作进 `app_parts/hints.py` |
| 命令分发 | 5 | 110 | 注册表字典 + `app_parts/commands.py`(get_system_commands/_dispatch 留名) |
| 外观/主题/语言 | 8 | 95 | 切换/重绘进 `app_parts/appearance.py`;picker 进 `screens/` |
| 视图(滚动/子代理/任务日志) | 11 | 80 | 滚动留 App(纯 query 委托);子代理/任务日志视图现状是 TranscriptView 换显 + `SubagentBanner`,不是独立 screen——抽为持有 `_viewing`/`_job_log_screen` 状态的视图状态对象 |
| 审批/提问管线 | 5 | 78 | **留在 app.py**(App-API 内建机制,push_screen+Future 即核心) |
| 侧边栏布局 | 8 | 41 | 并入既有 `widgets/sidebar.py` + `sidebar_resize.py` |
| 更新检查 | 4 | 39 | `widgets/update_notice.py`(数据层 `zenagent/update_check.py` 不动) |
| core(生命周期/组装/状态) | 14 | 281 | 留在 app.py |

**`_modal_result`/`_confirm`/`_prompt_rename`/`open_menu`(单实例菜单注册)
留在 app.py**:它们是 App 自身的弹窗原语(push_screen/Future 的薄壳),抽出去
只是为 45 行代码加一层间接。各域 `open_*` 搬走后经注册表指向新家。

**提交顺序原则**(细化步骤交计划文档):

1. 先立注册表(消灭 elif 链枢纽),此后各域搬家不再触碰分发代码;
2. 白送四摊(更新/侧边栏/视图/外观)与枢纽替换可穿插,先小后大建立节奏;
3. 大摊(弹窗域、动作群、模型域)按域一摊一提交;
4. 收尾:`wc -l` 前后对照写进 CHANGELOG(仅报告,非门槛),留场清单核对一遍。

**Phase 划分**(相互独立,可分开落地):

- Phase 0 工作区卫生:知乎草稿移入 `docs/za/`(已忽略);`.gitignore` 补行尾
  换行并提交。分钟级,先行。
- Phase 1 `ZenAgentApp` 十摊拆分(主体,约 10 步)。
- Phase 2 `ChatController`:附件与 follow-up 队列抽独立对象(队列对象可复用
  TUI 侧同构逻辑,先核对其状态归属再动手)。
- Phase 3 `Runtime`:metrics 抽出为独立模块,组装根只留调用点。

## 交给执行者的

- `app_parts/` 内模块命名与各 mixin 的基类排序(MRO 无冲突)。
- 每步提交的切分粒度:一摊一提交、gate 全绿、提交信息沿用仓库 conventional
  风格(refactor(tui): …)。
- 测试不需新增行为用例;若某 mixin 搬动导致测试 import 路径变化,改 import
  不改断言。注册表替换 elif 链后,补一条单测钉住"每个 result.screen 键都有
  归属",防止未来加 screen 时漏注册(对齐仓库"数字用守卫测试钉住"的惯例)。
- Phase 2 动手前先读 `app/chat.py` 与 TUI 队列动作的共享状态,确认队列对象
  的边界(设计已给出方向,边界细节由实现核实)。

## 裁决记录(builder check 带回的问题)

1. **app.py / chat.py 行数验收**:builder 实测证明原口头目标(≤450 / ≤400)
   与"承重路径留在原地"的裁决冲突。用户裁决:**行数不重要,模块拆出来、
   组织有条即可**——验收改为上面的结构清单,行数只报告不设门槛。
2. **队列对象边界**(执行者核实项收窄):steering(`steer`/`promote`/
   `_demote_steering`)与 follow-up 队列共享 `_pending` 列表,二者合成**一个**
   待处理队列对象;附件、子代理/任务日志视图状态各自独立对象。
3. **moved open_* 编排胶水的形态**:模块级函数(参数带 `app`)落 `screens/`
   域模块,不包对象;反目标的"必须成为独立对象"只约束承逻辑组件。
4. **测试触碰面**:凡测试读写的属性与方法名,继续从 App 实例直达(见约束),
   组件内部其余状态自由归属。
