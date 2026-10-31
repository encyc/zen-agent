"""简体中文 catalog.

本文件与 ``en.py`` 的 key 一一对应（``tests/test_i18n.py`` 会校验 key 集与占位符）。
措辞力求短、动宾清楚，和 docs 里的中文术语一致；命令名 / flag / tool 名 / 代码块不翻译。
"""

from __future__ import annotations

__all__ = ["CATALOG"]

CATALOG: dict[str, str] = {
    # 机制冒烟测试
    "test.greeting": "你好 {name}",
    "test.apples.one": "{n} 个苹果",
    "test.apples.other": "{n} 个苹果",
    # TUI：状态带
    "tui.status.cache": "缓存 {pct}%",
    "tui.status.context": "上下文 {pct}%",
    "tui.status.tokens": "入 {input} / 出 {output}",
    "tui.status.token_rate": "{rate} tok/s",
    "tui.status.queued.one": "排队 {n}",
    "tui.status.queued.other": "排队 {n}",
    "tui.status.steering.one": "插话 {n}",
    "tui.status.steering.other": "插话 {n}",
    # TUI：欢迎 / 工作条
    "tui.welcome.subtitle": "v{version} · Python agent 循环",
    "tui.welcome.hints": "/ 命令  ·  ctrl+b 面板  ·  ctrl+d 退出",
    "tui.work.jobs.one": "{n} 个后台任务",
    "tui.work.jobs.other": "{n} 个后台任务",
    "tui.work.task": "1 个子任务",
    "tui.work.working": "运行中",
    "tui.new_output.label": "有新输出",
    # TUI：转录行
    "tui.rows.thinking": "思考",
    "tui.rows.thinking_active": "思考中…",
    "tui.rows.done": "完成",
    "tui.rows.in": "入 {n}",
    "tui.rows.out": "出 {n}",
    "tui.rows.cache": "缓存 {n}",
    # TUI：todo HUD
    "tui.todo.title": "待办",
    "tui.todo.closed": "完成 {closed}/{total}",
    "tui.todo.done": "全部完成",
    "tui.todo.blocked.one": "{n} 个阻塞",
    "tui.todo.blocked.other": "{n} 个阻塞",
    "tui.todo.tasks": "任务",
    # TUI：子代理横幅
    "tui.subagent.label": "子代理：",
    "tui.subagent.readonly": "只读 · esc 返回",
    "tui.subagent.done": "完成",
    # TUI：工作面板分区
    "tui.sidebar.session": "会话",
    "tui.sidebar.model": "模型",
    "tui.sidebar.runtime": "运行时",
    "tui.sidebar.context": "上下文",
    "tui.sidebar.todo": "待办",
    "tui.sidebar.subagent": "子代理",
    "tui.sidebar.jobs": "后台任务",
    "tui.sidebar.messages.one": "{n} 条消息",
    "tui.sidebar.messages.other": "{n} 条消息",
    "tui.sidebar.ctx": "上下文 {used}/{window}",
    "tui.sidebar.ctx_total": "上下文 {pct}% · {mark}{used}/{window}",
    "tui.sidebar.spend": "花费 ${cost}",
    "tui.sidebar.state_running": "运行中",
    "tui.sidebar.state_idle": "空闲",
    "tui.sidebar.queue": "排队 {queued} · 插话 {steer}",
    "tui.sidebar.models.one": "{n} 个模型",
    "tui.sidebar.models.other": "{n} 个模型",
    "tui.sidebar.short.ctx": "上下文",
    "tui.sidebar.short.system": "系统",
    "tui.sidebar.short.agents": "AGENTS",
    "tui.sidebar.short.skills": "技能",
    "tui.sidebar.short.extensions": "扩展",
    "tui.sidebar.short.plan": "计划",
    "tui.sidebar.short.mode": "模式",
    "tui.sidebar.short.tools": "工具",
    "tui.sidebar.short.session": "会话",
    "tui.sidebar.short.other": "其他",
    # 上下文切片标签（侧栏摘要 + 明细浮层）
    "tui.context.system": "系统提示词",
    "tui.context.agents": "AGENTS.md",
    "tui.context.skills": "技能",
    "tui.context.extensions": "扩展",
    "tui.context.plan": "计划提示",
    "tui.context.mode": "模式提示",
    "tui.context.tools": "工具 schema",
    "tui.context.session": "会话",
    "tui.context.other": "其他（生成/开销）",
    "tui.ctx.free": "空闲空间",
    "tui.ctx.note_window_measured": "百分比按模型窗口计算；分段为估算（≈），已用总量来自 provider 实测",
    "tui.ctx.note_window_estimated": "百分比按模型窗口计算；分段与总量均为估算（≈）—— 尚无 provider usage",
}
