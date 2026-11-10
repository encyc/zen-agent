# 理解力验收记录 · 2026-11-09（manifest v2）

跑法：干净 Explore 子代理，只提供 brief.md / nodes.md / deps.json（deps 按 grep 采样），禁止读其他源码。

## 答题与判定

| # | 类别 | 回答要点 | 判定 |
|---|---|---|---|
| Q1 | 数据流 | cli → app.chat(ChatController.run) → agent.rt(Agent.prompt→_drive) → agent.loop → providers → events 回流 → cli 渲染；装配前置 app.assembly(prepare) | ✅ 顺序与方向全对 |
| Q2 | 影响面 | 主改 agent.loop（tool_exec 唯一调度点）；载体复用 trace/telemetry；注入点 app.assembly；波及 tools/tools.web/mcp/skills/app.taiji/extensions | ✅ |
| Q3 | 责任归属 | message 级重试在 providers（transport + message 级 retry，safe_stream 永不抛）；agent.loop 经 stream_with_retry 调用 | ✅（小瑕：把根 `retry` helper 当作 message 级载体，主结论正确） |
| Q4 | 扩展定位 | 能力层仿 tools.web 新建同级节点；走 http_client/settings 接线、app.assembly 注入、不改内核；支持 MCP 则复用 mcp | ✅ 引用了不变量 |
| Q5 | 变迁追溯 | **信息不足**：三件套只有版本戳，无 v2 变更内容 | ❌ 缺口 |

**判定：PASS（4/5，方向零错误）**

## 信息缺口 → 修正项

1. 三件套不含 deltas.md，变迁追溯题必然失败 → 已修：
   - `scaffold.py render` 在 brief.md 自动生成「§6 最近治理」（读 deltas.md 最后一条）
   - digest-format.md：阅读材料从三件套扩为**四件套**（+deltas.md）
2. Q3 的 `retry` 根 helper 与 providers message 级重试易混淆 → 已在 `retry` 节点 resp 标注「与 providers/message 级重试无关」
