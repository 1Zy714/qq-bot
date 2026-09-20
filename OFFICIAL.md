# QQ 开放平台官方机器人 → 本地模型

> 配套代码：`~/qq-bot/official_bot.py`（已离线验证）· 平台文档：[事件订阅与通知](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[发送群聊消息](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_messages.post.html)

## 0. 先纠正一个前提：平台不会"调取"你的本地模型

腾讯的服务器**永远连不到**你 WSL 里的 `127.0.0.1:11434`，也不该连。真实链路是：

```
腾讯平台 ──(1) 事件推送──▶ 你的桥接进程 ──(2) 本地 HTTP──▶ Ollama
```

- **(1) 方向是"你的进程主动连出去"**：官方支持两种事件通道，WebSocket 模式下由你的程序去连 `wss://api.bot.qq.com/websocket/`，**不需要公网 IP、不需要备案域名、不需要端口映射**。
- **(2) 才是"调取本地模型"**：桥接进程收到群 @ 事件后，用事件里的 `msg_id` 做被动回复，内容是本地模型生成的。

所以"官方平台调取本地模型"的正确理解是：**你在本机跑一个桥接进程，它替平台去调本地模型。**

### 两条事件通道，本地部署必须选 WebSocket

| | WebSocket（选它） | Webhook |
|---|---|---|
| 谁主动 | 你的程序连出去 | 平台 POST 到你的公网地址 |
| 公网要求 | **无** | 必须 HTTPS 公网，端口只能是 80/443/8080/8443 |
| 额外工作 | 无 | Ed25519 签名校验 + 回调地址验证 |
| 本地机器适配 | ✅ 天然适配 | ❌ 要打隧道 |

## 1. 架构

```
┌── 腾讯 QQ 开放平台 ──┐
│  GROUP_AT_MESSAGE_CREATE / C2C_MESSAGE_CREATE
└──────────┬───────────┘
           │ 出站 WSS（本机主动连）
┌──────────▼─────────── WSL2 ──────────────────┐
│  official_bot.py（botpy）                     │
│   ├ intents = public_messages (1<<25)         │
│   ├ LocalModel：asyncio.Lock 串行 + 历史      │
│   └ sanitize/split → message.reply(msg_seq=n) │
│                    │                          │
│                    ▼ 127.0.0.1:11434          │
│                 Ollama（只绑回环）            │
└───────────────────────────────────────────────┘
```

## 2. 落地步骤

### Step 1 — 拿凭据

到 [QQ 开放平台](https://q.qq.com/) 注册开发者 → 创建机器人应用 → 在「开发设置」里拿到 **AppID** 和 **AppSecret**。先别急着提审，用**沙箱环境**联调（沙箱里可以把机器人拉进测试群）。

### Step 2 — 申请事件权限

需要订阅 `GROUP_AND_C2C_EVENT`（`1 << 25`，botpy 里叫 `Intents(public_messages=True)`）。群聊相关事件属于**需要申请**的特殊事件，后台没开权限时鉴权会直接报错并断开连接。

### Step 3 — 起桥接进程

```bash
export QQ_APPID=你的AppID QQ_SECRET=你的AppSecret
~/.venvs/qqbot/bin/python ~/qq-bot/official_bot.py
# 常驻（脱离终端）：
# setsid nohup env QQ_APPID=... QQ_SECRET=... ~/.venvs/qqbot/bin/python ~/qq-bot/official_bot.py > ~/qq-bot/official.log 2>&1 &
```

依赖已装好（`qq-botpy` 在 `~/.venvs/qqbot`）。只测本地模型那一半：`python official_bot.py --selftest`。

**验收**：日志出现 `机器人已上线: xxx`。

### Step 4 — 拉进群并 @ 它

群管理员把机器人加进群（否则发送报 `40034101 机器人非群成员`），然后 `@机器人 你好`。

## 3. 平台规则 ↔ 本地模型的对应关系（关键）

| 平台规则 | 对本地模型的影响 | 代码里的处理 |
|---|---|---|
| 被动回复有效期 **5 分钟**，同一消息最多 **5 条** | 本地 20 s 生成完全来得及 | `message.reply()` 直接带 `msg_id` |
| 同一 `msg_id` 的多次回复**必须换 `msg_seq`**，否则报 `40054005 消息被去重` | 长回复分片时要递增 | `split_message()` + `enumerate(..., start=1)` |
| **群消息不支持流式** | 必须整段生成完再发 → 7.6 tok/s 直接决定延迟 | 非流式请求 + `max_tokens` 控制长度 |
| 消息里**不允许 URL**（`40054010`） | 本地模型很爱附链接 | `sanitize()` 把 URL 换成"(链接已省略)" |
| 消息长度超限（`40054007`） | 27B 容易写长 | `sanitize()` 截断 + `split_message()` 450 字分片 |
| `msg_id` 过期（`304103` / `40034005`） | 排队太久会导致回复失效 | `queue_limit` 上限 + 回"忙"话术，不硬攒 |
| 主动消息有频控（未认证 30 qpm；单群 20 qpm、1000 条/天） | 别把被动回复退化成主动推送 | 只用被动回复 |
| 用户只有 **openid**，不是 QQ 号 | 无法按真实 QQ 号做黑名单 | 会话历史按 `group_openid` / `user_openid` 分键 |
| 内容有审核（`MESSAGE_AUDIT` 事件） | 违规内容会被拦 | 见第 5 节 |

> `msg_seq` 用满 5 次后不能再回同一 `msg_id`；`split_message()` 已做 `[:5]` 截断。

## 4. 与 NapCat 路线怎么选

| | 官方开放平台 | NapCat（第三方协议端） |
|---|---|---|
| 需要 QQ 客户端常驻 | ❌ 不需要 | ✅ 需要（Windows 侧） |
| 封号风险 | 无（合规） | 有，建议小号 |
| 群聊能力 | 需申请/审核，能力上限低 | 几乎全部接口 |
| 能拿真实 QQ 号 | ❌ 只有 openid | ✅ |
| 能读群里全部消息 | 需管理员开启/申请 | ✅ |
| 上线门槛 | 创建应用 + 权限申请 + 沙箱 | 装完就能用 |
| 适合 | 长期、对外、要合规 | 自用、小群、要控制力 |

两条路线**共用同一个 `config.json`**（模型、system prompt、历史轮数、忙/错话术），可以同时跑：NapCat 那套占 `6199` 端口，官方这套是纯出站连接，两者不冲突。区别只在"谁来触发"。

## 5. 其他要注意的

- **openid 会随机器人不同而变化**，同一个用户在单聊和群聊里也是不同 openid，别拿它当用户主键跨场景关联。
- **内容安全**：官方平台对生成内容有审核与拦截，本地小模型的输出不可控，建议在 `system_prompt` 里加硬约束，必要时前置敏感词过滤。
- **断线重连**：botpy 自带 session resume（OpCode 6），网关抖动会补发事件；WSL 本身别休眠，否则机器人直接离线。
- **沙箱 vs 正式**：沙箱里能拉进测试群，正式环境需要提审通过、且群聊场景有额外门槛。
- **`raw: true` 那类坑与本路线无关**：这里统一走 `/v1/chat/completions` + `reasoning_effort: "none"` 关思考（原因见 `~/qwen/PROMPTING.md`）。

## 6. 验证状态

| 项目 | 状态 |
|---|---|
| `qq-botpy` SDK API 名核对（`on_group_at_message_create` / `post_group_message` / `msg_seq` / `intents=1<<25`） | ✅ 读 SDK 源码逐个确认 |
| 清洗与分片逻辑（去 URL、去 `<think>`、450 字分片） | ✅ 单测通过 |
| 官方 handler → 本地模型 → 回帖参数全链路（伪造 `GroupMessage`） | ✅ 3.3 s，回帖 `{'msg_type': 0, 'content': ..., 'msg_seq': 1}` |
| `on_ready` / 凭据缺失分支 | ✅ 通过（`on_ready` 里曾误用 `self.intents.value`，已修） |
| **真实连接腾讯网关** | ❌ 未验证——需要你的 AppID/AppSecret 和事件权限 |

联调时若报错，把 `official.log` 和错误码给我：常见的是 `40034101`（机器人不在群里）、`40034128`（被动回复超时/超次）、`40054010`（消息含 URL）、`40054005`（`msg_seq` 重复）。
