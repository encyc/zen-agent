# 飞书连接器设置

1. 打开[飞书开放平台](https://open.feishu.cn/app)，创建「企业自建应用」。
2. 在「凭证与基础信息」里复制 **App ID** 与 **App Secret**。
3. 在「权限管理」开通消息（IM）、文档与日历相关权限，然后发布版本并等管理员审批。
4. 回到 zen-agent「插件 → 飞书」，填入 App ID / App Secret 并保存。
5. 本机需安装 Node.js 18+（连接器用 `npx` 启动官方 `@larksuiteoapi/lark-mcp`）。

## 注意

- 凭据通过环境变量传给子进程，不出现在命令行参数里，`ps` 看不到。
- App Secret 只写进本机 `~/.zenagent/settings.json`（0600），日志和界面只显示掩码。
- 飞书官方把这些 MCP 工具标注为 Beta，行为可能随版本调整；升级后先跑一次「发消息」验证。
