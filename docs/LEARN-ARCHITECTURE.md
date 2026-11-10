# 架构导读：一个 QQ 消息是怎么变成回复的

> 读代码前先有地图。这篇只讲「谁负责什么、数据怎么流」，不抠语法。
> 行号对应 2026-11 的代码，文件改动后请以函数名搜索为准。

---

## 一、进程与部件

```
QQ 服务器
   │  WebSocket（botpy）
   ▼
official_bot.py  ── QQBotClient（收事件）
   │
   ├─ Responder.answer()  ← 一条消息的完整处理
   │     ├─ 人格 / 记忆 / 成长 指令（不走模型）
   │     ├─ 出图 / 改图（ComfyUI）
   │     ├─ [回忆:…] 记忆检索（本地索引）
   │     ├─ 模型调用
   │     │     ├─ agent_backend=dsh → DshAgent → dsh SDK → 本地模型 8080
   │     │     └─ agent_backend=local → 直连 OpenAI 兼容端点
   │     └─ 分片发送 / 表情包
   │
   ├─ GameLoop（每 60 秒）→ GameBackend → NagiBridge 7842 或 MCP
   │
   └─ 后台：记忆整理（每天）、自动接话调度
```

**三个外部依赖**：

| 地址 | 是什么 | 挂了会怎样 |
|---|---|---|
| `127.0.0.1:8080` | 本地模型 Strata | 聊天报错；游戏循环的策略层失效（本地候选仍可跑） |
| `localhost:7842` | NagiBridge（星露谷 Mod） | 游戏工具不可用；门控会跳过挂载 |
| 腾讯网关 | QQ 消息通道 | 机器人掉线，`svc.sh status` 显示未运行 |

---

## 二、一次私聊消息的完整链路

| # | 位置 | 做什么 |
|---|---|---|
| 1 | `official_bot.py:2053` `on_c2c_message_create` | 收到私聊事件，先去重（防平台重推） |
| 2 | 同函数 | 收表情包进库（`_harvest_stickers`），解析文本 |
| 3 | 同函数 | 记入跨会话索引（`record_incoming`，判定「要不要回」之前） |
| 4 | `official_bot.py:1214` `Responder.answer` | 进入统一处理 |
| 5 | `answer` 前段 | 人格指令 / 记忆指令 / 成长指令（严格前缀，命中直接回复，不经模型） |
| 6 | `answer` 前段 | 出图/改图检测（命中就走出图链路，不写聊天历史） |
| 7 | `answer` 中段 | 解析 `[回忆:…]` 或自动识别「上次/之前」→ 检索注入（`memory_search.py:223`） |
| 8 | `answer` 中段 | 给提问打发言人标签（`[私聊 昵称] …`，放在 user 消息里） |
| 9 | `answer` 中段 | 拼 system 后缀：表情包提示 + 人物档案 + 成长附录（**刻意保持稳定**） |
| 10 | `answer` 中段 | 调模型：`model.ask(session, prompt, system_suffix)` |
| 11 | `dsh_agent.py:147` `ask` | DSH 模式：走 SDK；空正文自动换新会话重试一次 |
| 12 | `answer` 后段 | 摘 `[记住:…]` / `[成长:…]` 标记；清洗、截断 |
| 13 | `answer` 后段 | 正文太短（只有表情/括号）→ 重试一次要求说人话 |
| 14 | `official_bot.py:1399` `_reply_with_sticker` | 按 450 字分片发送；需要时补一张表情包 |

> 关键设计：**system 逐字稳定**（保 llama.cpp 前缀缓存），**动态内容全部塞 user 消息**。

---

## 三、模块职责表

| 文件 | 行数 | 一句话 | 关键函数（行号） |
|---|---|---|---|
| `official_bot.py` | 2206 | 官方机器人主体：事件、策略、回复 | `answer` 1214、`on_c2c_message_create` 2053、`_build_game_loop` 1642 |
| `dsh_agent.py` | 200 | 把 DSH 包成模型接口 | `ask` 147、`_run` 127、`_harness_for` 86 |
| `game_loop.py` | 442 | 自主游戏循环（策略层） | `_tick` 193、`_do_tile_action` 272、`_verify_tile` 308 |
| `game_backend.py` | 336 | 两种游戏后端（同伴 / 玩家） | `StardewBackend` 66、`NagiBackend` 150、`make_backend` 325 |
| `user_memory.py` | 467 | 人物档案（合并、淘汰、摘要） | `consolidate` 342、`prune` 365、`digest` 437 |
| `memory_search.py` | 246 | 按需检索（关键词 + 时间衰减） | `search` 197、`digest` 223 |
| `cross_session.py` | 249 | 跨会话最近消息（内存，读时现算） | `digest` 145 |
| `growth_log.py` | 260 | 成长附录（只追加 + 哈希链） | `append` 144、`digest` 194 |
| `mcp_bridge.py` | 297 | MCP 客户端（手写 JSON-RPC）+ 健康门控 | `init_servers` 199、`health_ok` 34 |
| `tools/nagi_mcp.py` | 265 | NagiBridge → MCP 薄服务器 | `call_tool` 135、`handle` 209 |
| `web_tools.py` | 583 | 联网工具（仅 local 后端用） | `chat_with_tools` 464 |
| `image_gen.py` | 841 | 出图/改图（ComfyUI） | — |
| `sticker_lib.py` | 515 | 表情包库（收集、标签、挑选） | `infer_tags_from_text` 432 |
| `bot.py` | 803 | 旧 NapCat 路线 + 共用配置/人设 | `Config` 146、`PersonaStore` |

---

## 四、三条数据流

### 1. 聊天

```
QQ 文本 → 去重 → 记跨会话 → 指令判断 → 检索注入 → 打标签 → 拼 system 后缀
      → model.ask → 摘标记 → 清洗/分片 → sender 发送
```

### 2. 游戏循环（Nagi 后端）

```
每 60s：预算检查 → 观察（/state + /surroundings）→ 现场候选（收/浇/清）
        → 移动或动作 → 等动画 → 复核那一格 → 记账
        没活时：事件触发 → 模型选模式（farm/fish/idle，mine/follow 降级 idle）
```

### 3. 记忆

```
写入：[记住:xx] → add_notes 合并（事实键/子串/前缀）
读取：system 里给稳定摘要；[回忆:] 或「上次」触发检索（≤600 字）
维护：每天合并 + 180 天淘汰；成长附录只追加
```

---

## 五、为什么这么设计（面试常问的三点）

1. **为什么 system 不逐条变？**
   llama.cpp 会缓存 system 前缀；一变就全量重算。所以动态内容（时间、检索结果）放 user 消息。

2. **为什么工具分两处挂（机器人 MCP + DSH MCP）？**
   `agent_backend=dsh` 时模型请求由 DSH 发出，机器人自带的工具循环被绕过。
   所以 DSH 侧要单独注册 MCP（`dsh-qq.patch.yml` 的 `insert`），机器人侧的 MCP 只服务 local 后端和游戏循环。

3. **为什么游戏工具要门控？**
   游戏没开时挂 28 个死工具，模型会尝试调用、失败、重试——既慢又费 token。
   两端各有一道门：机器人侧 `health` 探活，DSH 侧 gate 脚本。

---

## 六、下一步

- 想逐段读代码 → [LEARN-CODE-WALK.md](LEARN-CODE-WALK.md)
- 想动手 → [LEARN-EXERCISES.md](LEARN-EXERCISES.md)
- 想看具体子系统 → [DSH-BRIDGE.md](DSH-BRIDGE.md)、[GAME-BACKEND.md](GAME-BACKEND.md)、[MEMORY-SEARCH.md](MEMORY-SEARCH.md)
