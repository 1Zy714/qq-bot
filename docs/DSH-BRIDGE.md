# DSH agent 桥接：会话、工具与故障恢复（2026-10-27）

机器人（`official_bot.py`）在 `agent_backend=dsh` 时，模型调用不直连端点，而是走
`dsh_agent.py` → 官方 Python SDK → 一个**独立的 DSH runtime**：

```
QQ 消息 → botpy → DshAgent → deepseek-harness-sdk-runtime --profile sdk --patch dsh-qq.patch.yml
                                      └─ provider: local-qwen → Strata 8080
```

- Harness home：`~/qq-bot/.dsh-qq-home`（独立于 `~/.dsh`，不污染网页 profile）
- 每个 QQ 会话映射一个 `qq-<sha1>` 的 dsh session（持久化在 home 的 `sessions/`）

## 一、工具必须注册进 DSH

**曾经的坑**：MCP（stardew/nagi）只挂在机器人自己的 `chat_with_tools` 循环里；
切到 DSH 后请求绕过那层，模型**看不到任何工具**，表现就是「她说工具没接上」。

修法：在 `dsh-qq.patch.yml` 用 `insert` 把 MCP server 注册进 DSH：

```yaml
- insert:
    - id: mcp-nagi
      name: "@deepseek-ai/dsh-mcp-client"
      config: { serverName: nagi, transport: stdio, command: ..., args: [...] }
```

两个注意点：

1. **新条目必须在 `insert:` 列表里**，直接写 `- id: xxx` 会被当成覆盖已存在条目（`entry not found`）。
2. **走 Windows 进程的 MCP 必须设 `WSLENV`**（stardew 用 `/mnt/c/.../node.exe`）：
   WSL 不会把环境变量透传给 Windows 进程，不登记就报 `Bridge file not found`。

## 二、空正文：坏会话会让机器人「一直嗯嗯」

现象：日志反复 `RuntimeError: dsh agent 返回空正文`，模型本身很快（直连 1.5s）。

根因：长命 DSH 会话在工具消息堆积后，`final_response` 返回空。
处理（`dsh_agent.py`）：

- 空正文 → **自动换一个全新 session 重试一次**
- 触发过的会话不再复用
- 手工恢复：备份并删除 `~/.dsh-qq-home/sessions/**/qq-<hash>/`

## 三、门控：游戏没开就不挂工具

| 位置 | 判断 | 配置 |
|---|---|---|
| 机器人侧 `mcp_bridge` | `health` URL 探活，失败 120s 内不重试 | `config.json` 的 `mcp_servers[].health` |
| DSH 侧 | `gate-nagi.sh` curl 探活；`gate-stardew.sh` 看 bridge 文件 2 分钟新鲜度 | `dsh-qq.patch.yml` 的 `command` |

日志：`[qq][mcp] nagi: 跳过挂载：健康检查未通过（游戏没跑？）`。
游戏打开后要**重启机器人**才会挂上。

## 四、模型路由

- provider `local-qwen` → `http://127.0.0.1:8080/v1`，模型 `qwen3.8-flash-next-iq2_xs`
- `apiKeyEnv: LOCAL_LLM_KEY` 必须解析到非空值（本地服务不校验，但适配器要求）
- 默认模型钉在 `agent-default-model` 里，开新会话不用手选

## 相关提交

`5271181`（工具收敛）、`ea4dc4f`（空正文兜底）、`1c4a8fb`（两端门控）。
