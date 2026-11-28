# `read_image`：让 agent 自己读图片（新工具 + 目录开关修正）

日期：2026-11-28
状态：已批准（2026-11-28，用户选定方案 A 后要求出实现计划）
规模：小改动（新工具 1 个 + 权限名单 3 处 + 提示块 4 处 + 目录开关 3 个 + 测试与 golden）

## 意图与为什么

agent 自己截了图（Playwright 落盘、`desktop` 的 `screenshot` 带 `output_path`），或者项目里
本来就有图片时，要能**直接看图**。

为什么现在做：今天的两次真实会话里，模型都是「截图 → `read_file` 拒读 → 只能改去量像素或读
无障碍树」（zen-agent 的「我没找到折叠按钮」、zhizu-auto 的视觉验证）。本机 107 个会话文件里，
送达模型的图片块数量是 **0**。

成功的样子：同一场景下，模型截图后调用 `read_image`，说出画面里有什么；用户在输入框贴图，
主力模型也能看到。

## 对话里已经定下的事

| 决定 | 谁定的 |
| --- | --- |
| 走方案 A：独立的图片工具，`read_file` 保持纯文本 | 用户选 |
| DeepSeek-4.1-flash 有识图能力，DeepSeek 直连与 opencode go 两个入口都是 | 用户确认（与实测一致） |
| 目录开关一并修正 | 本设计提议，用户选 A 时一并同意 |
| 工具名用 `read_image`（与 `read_file` / `write_file` 一致），不用 `read_pic` | 本设计（用户原话是「read_pic 之类的」，名字可改） |

## 证据（实测，2026-11-28）

用 64×32 的合成图（左橙右青）和一张「圆 / 方 / 三角」图，走 zen 自己的 provider 适配器，
每个模型发一到两次最小请求，目录的 vision 开关临时强制为 true（测的就是这个开关）：

| 模型 | 目录现值 | 结果 |
| --- | --- | --- |
| `opencode-go/deepseek-v4.1-flash` | false | 两张图都答对（`orange, teal`；`circle, square, triangle`） |
| `opencode-go/claude-haiku-5-5` | false | 答对 `orange, teal` |
| `zai-coding-cn/glm-5.3-flash` | false | 两张图都答对 |
| `zai-coding-cn/glm-5.3` | false | **HTTP 400**：`messages.content.type 参数非法，取值范围 ['text']` |
| `opencode-go/glm-5.3` | false | **HTTP 400**：`Upstream request failed: [invalid_request]` |
| `opencode-go/deepseek-v4-pro` | false | 4 次均 75 秒超时，**未测出** |
| `deepseek/deepseek-flash` | true | HTTP 402 余额不足，**未测出**（用户确认该模型能识图） |

结论：开关不能按模型家族一刀切。`glm-5.3-flash` 能看图而 `glm-5.3` 不能。

本机会话的模型用量（assistant 轮次）：`opencode-go/deepseek-v4.1-flash` 4086、
`deepseek/deepseek-flash` 2614、`zai-coding-cn/glm-5.3-flash` 911，其余可忽略。

## 现状（读代码 + 本地复现得到的）

图片链路本身已经存在，被两道闸门挡住：

1. **`read_file` 拒读一切二进制文件**（`zenagent/tools/fs.py:181`）。png / jpeg / gif / webp
   本地复现全部命中「it looks binary」或「not valid UTF-8」。会话里有 5 次真实的截图被拒读，
   全在 `deepseek-v4.1-flash` 会话里（11-18 两次、11-26 一次、11-28 两次）。
2. **所有入口都按目录里的静态开关 `supports_vision` 放行**（`catalog.yaml`，缺省 false）：
   `/image`、ctrl+v、`--image`、RPC `prompt.images` 都查它；发送层的
   `downgrade_unsupported_images` 把图片换成「(image omitted: model does not support images)」。

链路下游已经齐全，本设计**不改**：

- `read_image_argument`（`zenagent/attachments.py`）：魔数嗅探四种格式、单图 5 MB 上限，返回
  `ImageContent`。
- `apply_result_budget`（`agent/result_budget.py:165`）：图片块原样透传。
- 会话落盘：图片随消息持久化，超过 4096 字符进内容寻址的 blob 存储，加载时自动水合。
- 四个适配器都能把工具结果里的图片送达模型（chat-completions 里提升为紧跟工具结果的一条
  user 消息；Anthropic 的错误结果由 `split_error_tool_result_images` 把图片提出来）。
- RPC 线上视图（`app/views.py`）把图片块投影成 `[image image/png]` 文字，base64 不上线。
- 压缩、标题生成、导出、TUI 文本行都按 `type` 取块，图片块被跳过，不会崩。

## 目标 / 非目标 / 反目标

**目标**

1. 模型能用工具把磁盘上的图片（png / jpeg / gif / webp）作为图片块取回并看到。
2. 输入框贴图时，目录里实测能识图的模型不再被拒。
3. 模型找得到这个工具：`read_file` 的拒读信息指路；plan 模式和工具策略里点名。

**非目标（本次不做）**

- 大图缩放或压缩。
- `desktop` 的 `screenshot` 不带 `output_path` 时直接回图（服务端行为没验证）。
- 对话记录里渲染缩略图（现在只显示 `[image image/png]`）。
- 只发图不发字：RPC 现在回 `prompt needs text`（`cli/rpc/server.py:425`）。
- 给没验证过的模型翻开关；读 PDF。

**反目标（做成了也算失败）**

- 绕过权限闸门：`read_image` 必须和 `read_file` 一样受「受保护路径」确认的约束（读工具本来
  就不设工作区围栏，这一点不变）。
- 让纯文本模型的请求变慢变贵，或引入「按模型动态增减工具」的逻辑。
- 把图片 base64 发到 RPC 线上或写进日志。

## 方案

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| **A. 新工具 `read_image`** | `read_file` 保持纯文本，另加一个只读图片的工具 | 每次请求多一个小工具定义；模型需要知道它存在 |
| B. 扩展 `read_file` | 遇到图片直接返回图片块 | 不增工具，模型现有的调用习惯零改动生效；但 `read_file` 的分页、读后编辑、纯文本契约都要加上图片这一形态 |
| C. `desktop screenshot` 直接回图 | 传输代码已在 | 只覆盖一个来源，且服务端行为未验证；不能替代读文件的工具 |

**用户选了 A。** C 的传输代码已经存在，将来自成一项，本次不动。

### 子决定：纯文本模型调用 `read_image` 时怎么办

对话里我原本说「工具内按模型拒收」。核对后**改为另一种做法**，请确认：

- **选定：工具始终返回图片，由发送层已有的 `downgrade_unsupported_images` 换成提示文字。**
  理由：工具拿不到「正在执行的那个 agent」的模型。子代理复用父代理的工具对象（
  `tools/task.py:188`），agent 层也没有「当前 agent」的上下文；按父会话的模型去判断，会在
  「子代理换模型」时判错。MCP 工具回图（`desktop`）现在就是这么走的，行为一致。
- 放弃的做法：工具内按模型拒收。好处是不白存图、提示更直接，代价是上面那个判错的问题，
  外加一处按 Runtime 注入模型的接线。
- 选定做法的代价：纯文本模型会白读一次，并把那张图存进会话（单图不超过 5 MB）。

## 设计

### 工具

- 新模块 `zenagent/tools/read_image.py`，导出 `READ_IMAGE`；不放进 `fs.py`，和「`read_file`
  保持纯文本」的边界一致。
- 名字 `read_image`，唯一参数 `path`（字符串，必填）。
- 成功：结果含一行文字（路径、MIME、字节数）和一个 `ImageContent` 块；`details` 带
  `path` / `mime_type` / `bytes`。
- 失败（`is_error=True`，沿用 `read_image_argument` 的文案）：文件不存在、不是文件、超过
  5 MB、不是受支持的图片格式。
- `approval="read"`，相对路径和 `read_file` 一样绑定到会话的工作目录（同一个 `_bound` 包装）。
- 注册：`ALL_TOOLS` 和 `base_tools()` 里紧跟 `read_file` 之后，**始终注册**。`/model` 切换不
  重建工具名单，子代理通过 `task` 的 `base_tools` 也带上它。
- **权限闸门**（必须，否则是新的绕过口）：`app/permissions.py` 的三个名单都加上
  `read_image`：`READ_ONLY_TOOLS`、`_PATH_READ_TOOLS`、`_PATH_TARGET_TOOLS`。

### 提示

- 新块 `prompts/blocks/tools/read_image.md`，上限 900 字符，大意：
  「View an image file (png/jpeg/gif/webp, up to {{max_mb}} MB) with vision: screenshots you
  captured, images in the project. Returns the image itself, not text. A model without image input
  gets a placeholder instead.」上限数值通过 `{{max_mb}}` 从 `MAX_IMAGE_BYTES` 来。
- `read_file.md` 末尾加一句：图片也拒读，请用 `read_image` 查看。
- `read_file` 的拒读信息（`fs.py:181-190`）：读入字节后先嗅探图片格式，是图片就回
  「refusing to read …: it is an image (image/png); call read_image to view it」。两条拒读路径
  （二进制、非 UTF-8）都要先过这一步。`attachments._sniff_mime` 公开成
  `sniff_image_mime` 供这里复用。
- `plan_mode.md` 的只读工具清单加入 `read_image`；`system/tool_policy.md` 的「专用工具」一句
  加入 `read_image`，免得模型用 `cat` 或 base64 硬啃。
- `prompts/spec.py`：`TOOL_BLOCK_NAMES` 在 `read_file` 后加 `read_image`，
  `_TOOL_VARS["read_image"] = ("max_mb",)`。
- `PROMPT_VERSION` 升到 `2026-11-28.2`，`prompts/CHANGELOG.md` 加一条（含工具描述总字符数的前后
  对比，现在 7295 / 20000）。
- `tests/prompt_fixtures.py` 的 `TOOL_GOLDEN_NAMES` 加入 `read_image`，重新生成
  `tests/golden/prompts/`。

### 目录

- `supports_vision: true`：`opencode-go/deepseek-v4.1-flash`、`opencode-go/claude-haiku-5-5`、
  `zai-coding-cn/glm-5.3-flash`。
- 不动：`zai-coding-cn/glm-5.3`、`opencode-go/glm-5.3`（实测拒收）；`opencode-go/deepseek-v4-pro`
  （未测出）；`deepseek/deepseek-v4-pro`（key 余额不足，未测）。
- 测试：现有的 `test_supports_vision_is_parsed_and_defaults_to_false` 钉的是 `glm-5.3` 和
  `deepseek-v4-pro`，仍然成立，不用改；另加断言钉住三个翻开的值和两个实测拒收的值。

### 用户看到什么

- TUI 和桌面端：一张 `read_image` 工具卡片，显示参数和那行文字；记录里图片仍是
  `[image image/png]`。
- 输入框贴图：上面三个模型不再报「… does not accept images」。

## 风险

1. **大图不缩放**。Retina 全屏截图可能被服务商按尺寸或大小拒收（HTTP 400，这一轮失败）。
   第一版接受；Playwright 截图可以由模型自己控制视口和缩放。
2. **会话目录变大**。每张图的 base64 进 blob 存储（内容寻址，同一张图只存一份）。
3. **纯文本模型白存图**。见上面的子决定。
4. **主力网关连接不稳**。探测时 `opencode.ai` 出现过两次 TLS 提前断开，重试即好；与本次改动
   无关，记一笔。

## 留给实现者

- 是否在 `details` 里带图片宽高。默认不带（内核只依赖 httpx，没有图像库；PNG 头好解析，
  JPEG 要扫标记）。
- `read_image` 在 TUI 里是否并入「read_file ×N」分组。默认不并入。
- 测试文件名和测试组织方式。

## 涉及文件

新增：`zenagent/tools/read_image.py`、`zenagent/prompts/blocks/tools/read_image.md`、
`tests/test_read_image.py`。

修改：`zenagent/tools/__init__.py`、`zenagent/tools/fs.py`、`zenagent/attachments.py`、
`zenagent/app/permissions.py`、`zenagent/catalog.yaml`、`zenagent/prompts/spec.py`、
`zenagent/prompts/CHANGELOG.md`、`zenagent/prompts/blocks/tools/read_file.md`、
`zenagent/prompts/blocks/plan_mode.md`、`zenagent/prompts/blocks/system/tool_policy.md`、
`tests/prompt_fixtures.py`、`tests/test_catalog.py`、`tests/golden/prompts/*`，以及钉住工具
名单的既有测试。

## 验收

自动：

- 四种格式各返回一个 `ImageContent`；不存在、超限、非图片各返回 `is_error=True`。
- 相对路径按工作目录解析。
- 三个权限名单含 `read_image`；读受保护路径下的图片触发确认，判定与 `read_file` 逐路径一致。
- `read_file` 对 png 和 jpeg 回出指向 `read_image` 的信息，对其它二进制文件文案不变。
- 目录三个值为 true、两个拒收的值仍为 false。
- 提示 golden 重新生成后 `scripts/prompt_goldens.py --check` 通过，预算测试通过。

手工（桌面端，`opencode-go/deepseek-v4.1-flash`）：

- 让它用 Playwright 截一张图并描述画面：应调用 `read_image`，描述与画面相符。
- 输入框贴一张图发送：不再被拒，模型能说出图里有什么。
- 切到 `zai-coding-cn/glm-5.3` 再贴图：应被拒，说明该模型不支持图片。
