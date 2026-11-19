# RPC 协议代码生成 Implementation Plan

同一套 RPC 协议今天以四份手工副本存在：`server.py` 的 `kind == "…"` 分发
（898 行）、`rpc.rs` 的 `serde_json::Value` 代理（1009 行）、`types.ts` 及其
镜像模块的手写 TS 类型（844+ 行，文件头自述 "Shapes verified against the
live server" —— 靠人工对齐），加上 `bridge.ts` 的 e2e mock（1035 行）。每新增
一个 op 需要四处落地（见 git log 里连续的 `feat(rpc): git_*` 系列），且 ts↔py
之间没有任何类型系统兜底，漂移只能靠 e2e 在运行时发现。

目标：**py dataclass 成为唯一事实源，TS 侧生成、rust 侧按需**，把"加一个 op
的熵成本"从 ×4 压回 ×1。

## Global Constraints

- **不引入 pydantic**。协议模型全部是已有 dataclass；`dataclasses.fields` +
  `typing.get_type_hints` 足以驱动代码生成，运行时依赖零新增。
- **线上 JSON 语义不变**。生成只读类型，不改 server.py 的分发行为；未知字段
  在 TS 侧保持容忍（不加 excess 检查），避免生成类型比线上更严。
- **生成物是派生物**。`*.gen.ts` 文件头标注 generated + 来源模块，禁止手改；
  gate 用"重新生成 + git diff 必须为空"保证新鲜度。
- **增量化迁移**。手写镜像按域逐个换成 `import … from './types.gen.ts'`，
  每换完一个域，`allowlist.json` 里 `types.ts` 的记录随之缩减（payback
  2026-12-31 本来就要求它只减不增）。

## Review Focus

1. 类型映射表（py → TS）是否覆盖现有全部字段类型——先全仓盘点，出现映射
   表之外的类型即生成器报错拒绝，而不是静默 `any`。
2. `Optional[T]`、`Literal`、嵌套 dataclass、`list[T]`、`dict[str, T]` 的
   空值与可空语义（py 的 `Optional` 在线上是"字段缺席或 null"二义，TS 侧
   统一为 `?: T | null` 的哪种？需要拍板）。
3. 事件帧（`EventFrame`、`SwarmEventFrame`）是否进同一 registry——事件是
   推送方向，request/response 模型不适用，需要独立的 frame 声明形态。

## Phase 1 · 协议清单（py 侧收敛，不动行为）

### Task 1: `zenagent/cli/rpc/protocol.py` — op registry

新建唯一清单模块：`OPS: dict[str, OpSpec]`，`OpSpec = (request_dataclass,
response_dataclass)`。现有 request 解析散落在 server.py 各分支里手写
`payload["…"]`——第一步只登记**已有**的 dataclass（响应侧基本齐全），request
侧按 op 家族补 dataclass（git_*、fs_*、session、limits…每族一个），server.py
分发处改为 `spec.request.from_payload(payload)`，字段访问从 dict 下标变属性。
行为等价由现有 `tests/test_rpc_v2.py`（2319 行）背书。

### Task 2: 事件帧登记

`EVENTS: dict[str, type]` 收编 `EventFrame` / `SwarmEventFrame` 家族。帧是
union，登记时声明判别字段（`event` / `type`），生成器据此产出 TS discriminated
union。

## Phase 2 · TS 生成器 + gate

### Task 3: `scripts/gen_rpc_ts.py`

读 registry，产 `desktop/src/rpc/types.gen.ts`：每个 op 的 Req/Resp interface、
`RpcRequest`/`RpcResponse` 判别 union、事件帧 union。映射表只认白名单类型，
未知即失败。`allowImportingTsExtensions` 已开，生成物用 `import … from
'./types.gen.ts'` 风格对齐现有代码。

### Task 4: check.sh 接入新鲜度 gate

`python3 scripts/gen_rpc_ts.py --check`：重新生成到临时目录与现有文件 diff，
不一致即 fail（CI 语义：忘记重新生成 = 挂 gate，与 ruff/mypy 同级）。

### Task 5: types.ts 改为 re-export，手写镜像按域退役

`types.ts` 顶部 re-export 生成物，逐域删除手写 interface；`types-events.ts`、
`types-swarm.ts` 同法。bridge.ts 的 `BridgeState` 保持手写但字段改引生成类型，
mock 形状从此被编译期检查。每完成一域更新 allowlist（844 行目标只减不增）。

## Phase 3（可选）· rust 侧

`rpc.rs` 是 `Value` 薄代理，字段不解析、只转发——生成 serde struct 的收益低、
维护成本高。**默认不做**；仅当 rust 侧开始解析字段（目前 18 处）时再评估。
这一条是明确的"负范围"，防止计划膨胀。

## 验收

- 新加一个 op 的改动面：protocol.py 登记 + server.py handler + 前端使用；
  ts 类型零手写、gate 全绿。
- `types.ts` 从 844 行降到 re-export 薄壳；`allowlist.json` 对应条目删除。
- `tests/test_rpc_v2.py`、e2e 全量无回归。

---

## 落地记录（2026-11-19，Phase 1 + Phase 2 首批）

- **registry 落地为 `zenagent/cli/rpc/protocol.py`**：与原稿的偏差——响应侧没有现成
  dataclass（全是临时 dict + asdict），故 result 形状采用**声明式登记**（SHAPES +
  每 op 的 result keys），由生成器消费；request 侧为可执行模型（from_payload 校验
  形状，handler 保留语义与报错文案）。
- **已接线**：git_* 五 op（经 ops_git.py mixin——顺带完成 server.py 的拆分还债，
  server.py 915→747 行，allowlist 摘除）与 fs_list/fs_read（ops_files）。memory、
  hello/cancel/tools/usage/shutdown 已完整声明（codegen 就绪），handler 接线随
  ops_* 后续迁移。
- **frontier 冻结**：`tests/test_rpc_protocol.py` 断言 server.py 分发的 kind 集合
  == OPS ∪ PARTIAL（双向），PARTIAL 每项带原因；生成器只输出 OPS，stub 不会漏出
  半真类型。
- **生成器 + gate**：`scripts/gen_rpc_ts.py` → `desktop/src/rpc/wire.gen.ts`；
  `--check` 进 check.sh。types.ts 完成 git/fs/memory/tools/usage 域的 re-export
  迁移（854→700 行，allowlist 摘除）。`WorkspaceFile`（判别 union）属前端精化，
  保留手写——wire.gen 的扁平 FilePayload 是其必须兼容的线上真相。
- **EVENTS**：`EVENT_FIELDS`/`EVENT_TYPES` 声明了 `serialize_event` 的扁平输出
  （一个测试断言 serializer 的每个 `payload[...]` 键都已登记），生成
  `AgentEventWire`；types-events.ts 的判别 union 保留为前端精化，后续域迁移再收。
- **负范围维持**：rust 侧不动；RpcHost（swarm/zen/runs/supervisor/workspace）是
  独立协议面，留待第二批 registry。
