# 代码架构审查(2026-11-08,聚焦 taiji 增量与旧债清偿)

范围:`zenagent/`(~42k 行 Python,2463 测试)、`desktop/`(Tauri 2 + React,~9.8k 行 TS)。基线 HEAD `f3f9e1e`(worktree `refactor/arch-cleanup` 内实测:`uv run pytest -q` → 2463 passed(163s);`uv run ruff check` → 全过;desktop `pnpm test` 全过)。上一份 [2026-11-07 审查](2026-11-07-architecture-review.md) 的 16 项核验:**13 项已修复,2 项半途,2 项未动**(§一)。本份聚焦 11-07 之后两天的 taiji 功能流带来的新债。

**方法**:四路并行只读探查(taiji 模块 / rpc.py 增长 / 旧债核验 / desktop 状态层),承重结论逐条 grep 复核。

---

## 总体判断

**上一轮清理是实打实兑现的(13/16),六层结构与进程模型依旧健康。但 taiji 功能流绕过了自己刚建的预算门**:`cli/rpc.py` 从 1181 行涨到 2144 行(+81%,18 个提交),LOC 预算门**当前红灯**(实测 `python3 scripts/check_architecture.py` exit=1,已接入 CI `scripts/check.sh:27`)。门建好了,新功能没有从门里过。

## 一、旧债清偿核验(对照 11-07)

已修复(13):StreamOptions→`providers/stream_options.py`、工具协议→`agent/protocol.py`、bootstrap 四入口统一、crash 接线(`cli/main.py:501`)、usage 四合一(`views.py:437` 单源)、命令词表归注册表(`completion.py:32`)、全局钩子 DI 化(`set_ask_handler` 全仓零命中)、task.py 拆 `agent/subagents_runtime.py`、AsyncClient 池化(`providers/transport.py:133`)、协议握手(`rpc.py:85` ↔ `types.ts:34`)、契约测试(`tests/test_rpc_contract.py`)、chatStore 反向定位、ToolCallView kind、CSP 严格化、login_shell_env 超时(`rpc.rs:231`)、`__init__` 门面。

- ⚠️ 半:同步 IO 出循环——仅 `tools/fs.py:173` 完成;`agent/session.py:399` 每消息 open/close、`trace.py:88`、`telemetry.py:104` 仍旧。
- ❌ 未修:事件解码器×4(`main.py:49`/`repl.py:278`/`views.py:458`/`rpc.py:189`);`.catch(() => {})` 吞错(`appStore.ts` 7 处)。

## 二、P0-1:LOC 门红灯——机制被自己的功能流绕过

```
zenagent/cli/rpc.py:            2144 行(登记 1869)
zenagent/app/runtime.py:          919 行(登记 908)
desktop/src/i18n/locale.ts:       962 行(登记 858)
desktop/src/state/appStore.ts:    910 行(登记 812)
```

清理计划的硬约束"神文件只许变小"被 18 个提交反向执行;门已进 CI 却红着——红三天的门等于没有门。要么缩文件,要么显式改 allowlist 认账(带债务注记与还债任务),不能挂红。

## 三、P0-2:rpc.py 从翻译层变业务层(taiji 板块)

taiji 给 rpc.py 加了 ~220 行(`rpc.py:688-879` + `:2044-2065`),超出翻译职责:

- **穿透封装**:直接摸 `supervisor.registry.records`(`rpc.py:791,860`)与私有方法 `supervisor._record(name)`(`rpc.py:861`)。
- **投影逻辑住在协议层**:`_taiji_worker`(`rpc.py:809-840`)逐字段拼 board DTO、截断选项 `[:4]`、attention JSON 与 `supervisor.py:70-75` 两份解析。
- **策略在协议层**:`merge(auto=False)`(`:749`)由线层选策略分支;`_taiji_action`(`:856-862`)在编解码器里做 `was_running` 快照决定 inbox 语义。
- **第四套装配又冒头**:taiji boot 锁仪式(`:2044-2065`,含 `runtime._note` 又一次私有访问)在 bootstrap 统一之后作为前端特例后挂。

修法:`app/taiji` 出 board 投影 API 与公共访问器,rpc 只剩翻译;测试钉住"rpc 不得出现 `supervisor._`/`registry.records`"。

## 四、P0-3:supervisor 同步 git 阻塞事件循环

`_git` 是同步 `subprocess.run`(10s 超时,`supervisor.py:44-58`),在 spawn(`:307-344`)、kill(`:394`)、merge(最多 5 次串行 git,`:649-668`)、auto-merge(`:554`)、`_retire_worktree`(`:678-683`)路径上直接跑在 loop 上——而 board 读路径明知该 to_thread(`rpc.py:719-726` 注释自证)。一个慢 git 卡死 RPC pump 与 Router chat。

## 五、P1

1. **`app/taiji/supervisor.py` 是新的神文件**:一个文件 9 种职责(worktree 生命周期、worker RPC 协议 `RpcRunner :85-204` 整段第二关注点、turn 调度、slot 策略、inbox 语义、合并门+diff 记账、chat 路由、注册表、单例)。症状:`message()` 同一段"取消排队 turn"逐字复制两份(`:352-358` 与 `:371-377`)。
2. **desktop boot 序列克隆**:`openProject`(`appStore.ts:447-496`)复制 `connect`(`:189-248`)的 boot 体手工维护差异;`ChatView.tsx:193-194`、`WorktreePanel.tsx:45-46` 仍直连 `disconnect()+connect()` 绕过新路径;`switching` 标志无任何组件消费(`App.tsx:183-191` 只认 idle/starting/exited)。
3. **持久化缺口**:`WorkerRecord` 无 prompt 字段(`registry.py:20-31`)——首 turn 完成前崩溃任务本身丢失;`_inbox` 纯内存(`supervisor.py:243`),排队插话崩溃即蒸发。
4. **taiji payload 第三份手写投影无契约**:supervisor records → `_taiji_worker` dict → `types.ts` `TaijiWorker`(`:519-562`);契约测试只钉 REQUEST_KINDS。`TaijiPanel.tsx` 自带数据层(2s 轮询进组件局部 `useState`,`:105` 裸 `getState().client().send`)。
5. **locale 双通道失管**:TS 字典 458/458 键一致但零测试(`translate()` 静默回退 `locale.ts:951-956`);服务端硬编码中文错误(`rpc.py:743` `'合并未执行'`、`:763` `'worker 不存在: {name}'`)在英文 UI 裸渲染;TaijiPanel 无论 locale 拼中文协议串(`:100,:102,:197,:322`)。Python 侧有 parity 测试,TS 侧没有。

## 六、P2

1. rpc argparse 双写:`rpc.py main() :2073+` 约 25 个旗标重抄 `cli/main.py build_parser()`(`:265-458`),仅 `THINKING_CHOICES` 复用。
2. 同步 IO(同 §一 半途项)。
3. `.catch(() => {})` 纪律(`appStore.ts:284,303,315,323,341,379,416`)。
4. `prefs.ts` 无测试(模块自述以可测性为目标)。
5. 事件解码器×4 与 repl 重复 reload 块(`repl.py:564-579`/`:706-713`)。
6. rpc.py 包拆分(旧计划 Task 16 从未执行;现在多了 ~1000 行要搬)。

## 七、好的方面(保持不动)

- 分层与进程模型:taiji 只向下 import(`app.taiji → app.runtime → agent`),内核对 taiji 零感知;单 Python 内核 + stdio JSONL + JSONL 会话文件不动。
- taiji 崩溃恢复是认真设计的:原子注册表写入(`registry.py:66-82`)、boot 对账 `git worktree list`(`:84-94`)、每 turn 新子进程复用 JSONL 会话。
- 确定性合并门:LLM 不在关键路径,用户工作树神圣(`--abort` 兜底);kill 竞态守卫(`:419-427`、`:456-458`)是推理过的修复。
- 双端契约测试与 chatStore 测试投影是稀缺纪律,保持并扩展。

**一句话**:先让门变绿、把 taiji 边界(rpc↔supervisor)补干净、git 出 loop;然后 supervisor/desktop 状态层/locale 按序还债;最后 rpc 包拆分收尾。修复计划见 [`docs/superpowers/plans/2026-11-08-arch-cleanup-p0p1p2.md`](../superpowers/plans/2026-11-08-arch-cleanup-p0p1p2.md)。
