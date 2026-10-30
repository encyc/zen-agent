# 三家怎么做配置持久化（codex / omp / dsh）

> 调研目的：确定 ZenAgent 的"设置 + API key 落盘"该长什么样。
> 方法：直接读本机安装的产物与开源源码——`~/.codex/`、`~/.omp/`、`~/.dsh/`，以及 codex 的 Rust 源码（`codex-rs/core/src/config/edit.rs`、`codex-rs/login/src/auth/`）、omp 的 `config/config-file.ts` 与 `utils/atomic-file.ts`、dsh 的 `@deepseek-harness-tui/dsh-auth/lib/credentials.js`。
> 结论先行：**三家都把"配置"和"密钥"分成两个文件/两种写入者**，而且**没有人让用户手写 key 到配置文件里**。

## 1. 落盘地图

| | codex | omp | dsh |
|---|---|---|---|
| 用户配置 | `~/.codex/config.toml`（0600，`config.toml.bak` 备份） | `~/.omp/agent/config.yml`（YAML 正典；`config.yaml` 兼容）；项目级 `<cwd>/.omp/config.yml` | `~/.dsh/settings.yaml`（插件命名空间，见 `settings.yaml.imported`） |
| 密钥 | `~/.codex/auth.json`（0600） | 本地 auth store：**SQLite** `~/.omp/agent/agent.db`（可选远端 auth-broker） | `~/.dsh/dsh-auth/credentials.json`（OAuth）+ `~/.dsh/.credentials.yaml`（refs/records 保险库） |
| 其他状态 | `.codex-global-state.json`（临时文件 + rename） | `agent.db` | `~/.dsh/storages/*.json`、`~/.dsh/sessions/` |
| 目录权限 | `~/.codex` 0755、文件 0600 | `~/.omp/agent` 默认 | `~/.dsh` **0700**，凭证文件 0600 |

共同点：**配置可手改，密钥不可手改**。codex 的 `auth.json` 由 CLI 登录写入；omp 的 key 进 SQLite；dsh 的进 JSON/YAML 保险库。ZenAgent 之前把 `DEEPSEEK_API_KEY=...` 写进 `config.toml`，三家都不会读这种写法。

## 2. 写入策略：都是"临时文件 + rename"

**omp** `utils/atomic-file.ts` 的注释把理由写全了：

- 写私有临时兄弟文件 → 单次 `rename` 发布，**读者永远看不到半写状态**，失败时目标文件原样保留；
- 临时文件用 `mode`（默认 **0600**）创建，并以该权限发布，所以"迁移后的配置里带内联 API key 也仍然只有属主可读"；
- Windows `EPERM/EEXIST` 有专门的降级：目标→backup、temp→目标、失败回滚、最后删 backup。

**codex** 的配置写入走 `ConfigEdit` 枚举 + `toml_edit::DocumentMut`，再调 `write_atomically`：用 `toml_edit` 是为了**保留用户手写的注释和排版**，只改目标键。它还有 `resolve_symlink_write_paths`——dotfiles 常是软链，写之前先解析真实路径，否则 rename 会把软链替换成普通文件。

**dsh** `dsh-auth/credentials.js` 头注释：临时文件 + rename、**0700 目录 / 0600 文件、全平台 best-effort**；所有修改都走 `modify`，**按 provider 串行化读-改-写**——因为 OAuth refresh 发生在 `modify` 内部，不串行会出现并发双重刷新、把轮换后的 token 覆盖坏。

ZenAgent 现状：`save()` 直接 `write_text()`，没有 rename。进程在写一半时被杀，密钥文件就废了。这条必须补。

## 3. 密钥与配置分离的三种形态

**codex：分级存储 + OS 钥匙串**
`AuthCredentialsStoreMode` 有 `Auto / File / Keyring`，`AuthKeyringBackendKind` 有 `Direct / Secrets`（macOS Keychain）。`Auto` 先试钥匙串，失败才回落到 `auth.json`，并且**为"安全失败降级"单独打点**（测试名：`storage_metrics_distinguish_explicit_file_from_secure_failure_fallback`）。`auth.json` 里 `auth_mode` 区分 `chatgpt`（OAuth：id/access/refresh token + account_id + last_refresh）和 `apikey`。

**omp：优先级链 7 层**
`providers.md` 写得很清楚（先匹配先赢）：
1. 进程内覆盖（`--api-key`，不持久化）
2. `models.yml` 里钉死的 `apiKey`（自定义 baseUrl/网关用）
3. 存储的 OAuth（多账号轮换）
4. `/login` 存下的 API key
5. 扩展配置里的 key 引用
6. **provider 环境变量（含 `.env`）**
7. 其他存储 key（兜底）

注意第 3/4 在环境变量**之前**：用户在应用里显式登录的凭据，优先于 shell 里碰巧存在的 env。这条和我们"设置 > 环境变量"的方向一致。

**dsh：refs + records 保险库**
`.credentials.yaml` 是 `version: 1` 的文档：
- `refs:` 把**环境变量名**映射到一个引用（MCP/连接配置只写 ref，不写明文）；
- `records:` 按 `<scope>/<name>` 存实际条目（`kind`/`payload`/`version` + `secret`/`token`），例如 `client-connection/browser-session`、`deepseek-account-platform/default`。

两条设计值得抄：**带 version 字段**（可迁移），**非密钥元数据可单独读取**（dsh 的 `describe()` 只返回非敏感元数据给状态界面；omp 的 `/settings` 面板也"不回填环境变量里的 key"）。

## 4. 配置层优先级（omp 最完整）

`~/.omp/agent/config.yml`（全局）→ `<cwd>/.omp/config.yml`（项目）→ `--config <file>`（一次性覆盖）→ 运行时覆盖（内存，不落盘）。

配套规则：
- 写只写全局层（`modelRoleStorage: project` 时例外，只写 `modelRoles` 到项目层）；
- 环境变量覆盖**永远不落盘**，面板里也不预填；
- 写坏的持久化配置 → 移到唯一的 `.broken-*` 备份 + 带着原错误退出（不静默重置）；
- 非法值：普通键告警后按默认值读；带自定义校验的键直接拒绝。

## 5. 迁移与容错

| 产品 | 做法 |
|---|---|
| omp | `settings.json` → `config.yml` 一次性迁移，成功后把老文件名改成 `settings.json.bak`；`.json` → `.yml` 懒迁移（进程内幂等去重）；迁移写入用 0600 原子写 |
| dsh | 老 `settings.yaml` 迁成 `settings.yaml.imported`（后缀标记"已导入"） |
| codex | 保留 `config.toml.bak`；迁移提示用 `[notice]` 表记录"已提示过"（`SetNoticeHideModelMigrationPrompt` 等 ConfigEdit 变体） |

三家都用"**改名/加后缀**"而不是删除来标记迁移完成。

## 6. 对 ZenAgent 的结论

**已经对齐的**
- 密钥与配置分离：`~/.zenagent/config.toml`（配置）+ 新的 `~/.zenagent/settings.json`（key + 偏好）
- 设置优先于环境变量（`settings.get_api_key`）
- 遗留迁移：`config.toml` 里误放的 `*_API_KEY="..."` 自动导入（`_migrate_legacy`）
- UI 里输入 key、不回填明文

**要补的（本次实施）**
1. `settings.json` 写入改成**临时文件 + rename**，并保持 0600；目录尝试 0700；
2. 文档加 `version` 字段，后续可迁移；
3. 解析失败**大声报错**（不静默当空），但仍允许继续用 env；
4. 提供 `describe()`：只返回"哪些 key 已配置"的非敏感元数据（`settings.describe()`），禁止任何界面/日志打印值。

**暂不做的**
- OS 钥匙串（pync 依赖 + 跨平台差异，收益是"文件泄露时更安全"，等有真实需求）
- refs/records 式保险库（MCP server env 目前用不到；将来接 MCP 的 secret_ref 时再引入）

## 源码对照

- `~/.codex/config.toml`、`~/.codex/auth.json`、`~/.codex/.codex-global-state.json.tmp-*`（本机实例）
- codex：`codex-rs/core/src/config/edit.rs`（`ConfigEdit` + `toml_edit` + `write_atomically`）、`codex-rs/login/src/auth/`（store mode / keyring）
- omp：`packages/coding-agent/src/config/config-file.ts`、`src/utils/atomic-file.ts`、`docs/settings.md`、`docs/providers.md`
- dsh：`~/.dsh/.credentials.yaml`（refs/records）、`@deepseek-harness-tui/dsh-auth/lib/credentials.js`（原子写 + per-provider 串行）、`~/.dsh/settings.yaml.imported`
- ZenAgent：`zenagent/settings.py`、`zenagent/app/support.py::api_key_for`、`zenagent/tools/web/providers.py::_key`
