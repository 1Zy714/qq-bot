# 聊天响应提速与工具收敛（2026-10-27）

## 问题

机器人在 `agent_backend=dsh` 下，每条消息要 10~26 秒才回。日志里能看到
模型在**翻工作区**（回复中出现 `/home/lzy959/qq-bot/agent-workspace`）。

## 根因

DSH 的 sdk profile 默认挂**整套 agent 工具**：`tool-bash`、`tool-fs`、`tool-jobs`、
`tool-subagent*`、`tool-workflow`、`tool-web`、`bash-sandbox`、`shell-env`。
QQ 聊天根本用不到，但模型每轮都可能调用它们探索文件，于是：

- 延迟从秒级涨到 10~26 秒
- 上下文被工具结果挤占
- **安全风险**：群里任何人诱导它执行命令，就能在这台机器上跑命令

另外游戏 MCP 在游戏没开时也会挂载，模型尝试调用失败又浪费一轮。

## 改法

1. **关闭无关工具**：在 `dsh-qq.patch.yml` 里逐个 `disabled: true`
   （`tool-bash`/`tool-pwsh`/`tool-fs`/`tool-fs-search`/`tool-jobs`/
   `tool-subagent*`/`tool-workflow`/`tool-todo`/`tool-goal`/`tool-web`/
   `bash-sandbox`/`shell-env`）。
2. **限 token 与超时**：`dsh_max_tokens=1024`、`dsh_timeout=180`
   （`dsh_agent.py` 传给 `DeepSeekHarness`）。
3. **游戏工具两端门控**：机器人侧 `mcp_bridge.health` 探活；DSH 侧
   `tools/gate-nagi.sh` / `gate-stardew.sh`（bridge 文件 2 分钟新鲜度）。

## 实测

| 消息 | 改前 | 改后 |
|---|---|---|
| 「在吗」 | 22.7s | **6.6s** |
| 「今天过得咋样」 | 11.2s | **4.8s** |
| 「你现在有哪些工具」 | — | 4.4s，只列安全工具 |

工具清单从「bash/fs/subagent/workflow」收敛到：

```
working_directory / skill / MCP 资源读取 / exit_plan_mode
```

## 代价与边界

- 机器人**不能**再执行命令、读写文件、联网搜索（要恢复就删对应 `disabled`）
- 游戏工具需要游戏在跑 + 重启机器人（MCP 在启动时挂载）
- 相关提交：`5271181`（提速）、`1c4a8fb`（门控）
