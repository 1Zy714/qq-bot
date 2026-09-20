# 自动聊天（官方开放平台路线）· 方案

> **范围**：本文只做设计，**不改任何代码**。路线固定为 `official_bot.py`（QQ 开放平台 + botpy，WebSocket 模式）。
> **编写时状态**：`bash svc.sh status` 显示 napcat / official / ollama 三者均未运行；文中"代码扩展点"基于当前工作区实际文件。
> 平台规则核对日期：2026-09-20。

---

## 0. 一句话结论

官方平台上，"自动聊天"能做，但**不是"机器人自己定时找群友聊天"**，而是分三种：

| 形态 | 是什么 | 官方路线可行性 |
|---|---|---|
| **① 群内自动接话**（推荐） | 群里不 @ 也接话，用被动回复回帖 | ✅ 可行，需群主在手机 QQ 里开「获取群内全部消息」 |
| **② 定时/主动发言** | 到点主动往群里发一句 | ⚠️ 可行但受开关 + 频控（20 条/分钟·群、1000 条/天·群），且**群必须开启「机器人主动在群聊内发言」** |
| **③ 互动召回** | 用户聊过之后，30 天内分 4 个周期各发 1 条 | ⚠️ 可行，但本机 botpy 未暴露参数，要裸 HTTP |
| ~~④ 主动私聊回访~~ | 主动给好友发消息 | ❌ 不推荐，最像营销号，且同样受"允许主动发送"开关限制 |

关键点：**①②③ 全都依赖"用户在客户端显式开启"**。没开就是发失败，不是发得慢。

---

## 1. 现在代码长什么样（扩展点）

| 位置 | 现状 | 对自动聊天的意义 |
|---|---|---|
| `official_bot.py:186` `QQBotClient` | 只有 `on_ready` / `on_group_at_message_create` / `on_c2c_message_create` / `on_group_add_robot` | **新增 `on_group_message_create` 即可拿到群全量消息** |
| `official_bot.py:276` `Intents(public_messages=True)` | = `1 << 25`，即 `GROUP_AND_C2C_EVENT` | **intent 已经覆盖全量群消息事件，不用改** |
| `official_bot.py:154` `Responder.answer(session, prompt, sender)` | 与 botpy 解耦，`sender(内容, seq)` 由调用方注入 | 自动聊天可直接复用，不用碰模型这层 |
| `official_bot.py:92` `LocalModel` | 单个 `asyncio.Lock` 串行 + `queue_limit` 拒载 | **自动聊天必须走同一个 `LocalModel`**，否则和真人消息抢模型 |
| `official_bot.py:183` `split_message` | 按 450 字切片，最多 `MAX_PASSIVE_REPLIES=5` 片 | 被动回复上限的保护已就位 |
| `official_bot.py:204` `message.reply(...)` | 内部就是 `post_group_message(msg_id=self.id)` | 接话走这条 = 被动消息，**不占主动配额** |
| `official_bot.py:279` `client.run(...)` | 阻塞式启动 | 定时任务要在 `on_ready` 里 `asyncio.create_task`，或在 `run` 前用 `client.loop` |

### ⚠️ 一个必须先解决的坑：本机 botpy 不认识全量群消息事件

`~/.venvs/qqbot/.../botpy/connection.py:84-88` 用 `inspect.getmembers` 收集 `parse_*` 方法做事件分发表。当前版本**只有 `parse_group_at_message_create`，没有 `parse_group_message_create`**（`grep -rn group_message_create botpy/` 只有 flags 注释提到）。所以就算平台推了 `GROUP_MESSAGE_CREATE`，SDK 也会当成未知事件丢掉。

两条路：
1. **升级 botpy**（先确认新版是否已补该 parser）；
2. **不升级，加 6 行补丁**（放在 `official_bot.py` 的 import 之后、构造 Client 之前，因为 `parsers` 是在 `ConnectionState.__init__` 里一次性生成的）：

```python
import botpy.connection as _bc
from botpy.message import GroupMessage as _GroupMessage

def _parse_group_message_create(self, payload):
    # 字段与 GROUP_AT_MESSAGE_CREATE 完全一致，可直接复用 GroupMessage
    self._dispatch("group_message_create", _GroupMessage(self.api, payload.get("id"), payload.get("d", {})))

_bc.ConnectionState.parse_group_message_create = _parse_group_message_create
```

---

## 2. 平台硬约束（决定方案边界）

| 项 | 规则 | 来源 |
|---|---|---|
| 被动回复有效期 | 群 **5 分钟**、单聊 **60 分钟** | [消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) |
| 被动回复次数 | 群 5 次 / 单聊 4 次（旧版 `send.md` 写单聊 5 次，**两处文档不一致，代码按 5 次封顶更安全**） | 同上 / [send.md](https://raw.githubusercontent.com/tencent-connect/bot-docs/master/docs/develop/api-v2/server-inter/message/send-receive/send.md) |
| 主动消息开关 | 用户/群可在客户端关闭「允许主动发送」，关闭后**一律失败** | 同上 |
| 主动消息频控（群） | 单关系 **20/qpm**；Bot 维度未认证 30/qpm、已认证 60/qpm；**每日上限 1000 条/群** | 同上 |
| 主动消息频控（单聊） | 单关系 20/qpm；每日上限 **1000 条/用户** | 同上 |
| 互动召回 `is_wakeup` | 用户聊过之后 30 天内 4 个周期（当天 / 1–3 天 / 3–7 天 / 7–30 天）各 1 条 | 同上 |
| 全量群消息 | 需开启「接收所有消息」；AstrBot 文档给的实操路径：**手机 QQ → 群设置 → 机器人设置 → 消息范围设为「获取群内全部消息」+ 开启「机器人主动在群聊内发言」** | [群消息（全量模式）](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html) / [AstrBot 文档](https://docs.astrbot.app/platform/qqofficial/websockets.html) |
| 相关事件 | `on_group_msg_receive`（群接受主动消息）/ `on_group_msg_reject`（拒绝），botpy 已支持 | `botpy/flags.py:322-339` |
| 常见错误码 | `22009 msg limit exceed`（超频）；主动消息无权限时直接失败 | send.md |

> **关于 2025-04-21 那个"主动推送下线"公告**：官方 `send.md` 顶部仍留着"主动推送能力于 2025 年 4 月 21 日起不再提供"的告示（对应[腾讯 2025-04-16 公告](https://www.ithome.com/0/846/651.htm)），但**当前版本的消息收发概述仍在给主动消息的频控指标**，AstrBot 文档（v4.19.6）也写着"主动消息推送：支持"。
> 合理理解是：**旧的无条件群发被收掉，改为"用户/群显式开启后才可发"**，这正好对应 `on_group_msg_receive` / `on_group_msg_reject` 这两个事件。
> 结论：**别拿文档当结论，落地前先用一次真实调用探活**（见第 7 节）。

---

## 3. 三种形态的设计

### 形态 ①：群内自动接话（推荐先做）

```
GROUP_MESSAGE_CREATE ──▶ 过滤（关键词/冷却/额度/静默时段）
                          └─▶ Responder.answer(session, prompt, sender)
                                └─▶ sender 用 message.reply() ← 被动消息，不占主动配额
```

- **触发判定**建议三层叠加，缺一层都会翻车：
  1. **必答层**：`@自己`、命中关键词表（如"机器人""小助手"）、以问号结尾；
  2. **概率层**：其余消息按 `probability`（建议 0.05–0.15）随机接；
  3. **闸门层**：同群冷却 `cooldown_seconds`（建议 ≥ 60）、每群每日额度、静默时段。
- **回帖方式**：`message.reply(msg_type=0, content=..., msg_seq=seq)` → 走 `msg_id` 的被动通道，5 分钟窗口内有效，**不消耗主动消息额度**，这是它比形态②划算的根本原因。
- **不能做的**：全量消息 = 群里每句话都会进你的 27B。必须叠加闸门，否则 8 tok/s 的模型会被排队拖死（第 5 节有算例）。
- **不适用**：官方 SDK **收不到未开启全量模式群的消息**，所以"自动接话"在没开开关的群里彻底不可用。

### 形态 ②：定时 / 主动发言

```
on_ready ──▶ asyncio.create_task(scheduler)
              loop: 到点 && 群在 allowed_groups && 群已开启主动发言
                    └─▶ api.post_group_message(group_openid=..., msg_type=0, content=...)  ← 不带 msg_id/event_id
```

- 不带 `msg_id` / `event_id` 就是主动消息，受第 2 节频控 + 开关约束。
- **必须先知道哪些群能发**：监听 `on_group_msg_receive` 记进白名单，`on_group_msg_reject` 移出。群主随时能关，关了就是失败，别写死假设。
- 内容要有"独立的主动 prompt"，不要拿历史对话往下接（历史里有"用户: xxx"，模型会当成有人在跟它说话）。
- 建议起步配置：每天 1–2 条、只在 whitelist 群、静默时段 23:00–08:00、先 `dry_run` 只打日志不发。

### 形态 ③：互动召回（`is_wakeup`）

- 用户和机器人聊过之后，30 天内 4 个周期各 1 条 —— 适合"很久没聊了，回访一句"，**不适合当日常自动聊天**。
- **本机 botpy 的 `post_group_message` / `post_c2c_message` 都没有 `is_wakeup` 参数**（已核对 `botpy/api.py:1380,1426` 的签名），要用就得自己发裸 HTTP（`POST https://api.bot.qq.com/v2/groups/{group_openid}/messages`，body 加 `"is_wakeup": true`），token 复用 botpy 的 `token.BotToken`。

### 形态 ④：冷场搭话

= 形态①的全量消息（拿来记 `last_active`）+ 一个定时器。工程上不新增机制，但**体验风险最高**：群里刚安静 3 分钟它就冒头，很容易被踢。建议不做，或至少把阈值放到 30 分钟以上并只在白名单群开。

---

## 4. 如果之后要落地：改动清单

| 文件 | 改动 | 量级 |
|---|---|---|
| `official_bot.py` | 加 `parse_group_message_create` 补丁（第 1 节，6 行） | 6 行 |
| `official_bot.py` | `QQBotClient` 加 `on_group_message_create`、`on_group_msg_receive`、`on_group_msg_reject` | ~40 行 |
| `official_bot.py` | 新增 `AutoChatPolicy`（纯函数：判定该不该接、该不该发）+ `AutoChatScheduler`（`on_ready` 里起 task） | ~120 行 |
| `official_bot.py` | `main()` 里把 policy/scheduler 注入 client；`aclose` 时取消 task | ~15 行 |
| `config.json` | 新增 `auto_chat` 块 | 见下 |
| 文档 | 更新 `OPS.md`（开关注释）、`TOOLS.md` 无关、新增本节 | 小 |

> `AutoChatPolicy` 建议写成**不依赖 botpy 的纯函数**（输入：会话、文本、是否@、距上次回复秒数、当日已发条数、当前时刻；输出：`(是否处理, 原因)`）。这样能像 `bot.py` 的触发逻辑那样做单测（`PLAN.md` 第 11 节已有先例），不用连腾讯网关。

### `config.json` 建议结构（默认全关）

```jsonc
"auto_chat": {
  "enabled": false,                 // 总开关，默认关
  "dry_run": true,                  // 只打日志不真发，先跑一天看命中率
  "reply_mode": "passive",          // passive=接话(推荐) / proactive=主动发言
  "allowed_groups": [],             // 群 openid 白名单；空 = 不接任何群
  "keywords": ["机器人", "小助手"],
  "reply_when_mentioned": true,
  "probability": 0.1,               // 非必答消息的接话概率
  "cooldown_seconds": 90,           // 同群最小间隔
  "daily_limit_per_group": 30,      // 每群每日上限（远低于平台 1000 条硬顶）
  "quiet_hours": ["23:00", "08:00"],
  "schedule": [],                   // proactive 模式：["09:00","21:30"]
  "proactive_prompt": "现在群里很安静，你想主动说一句轻松的话，只回这一句，不要提问太多。",
  "max_tokens_override": 60         // 主动消息比被动回复更短
}
```

### 建议的护栏默认值

| 护栏 | 建议 | 理由 |
|---|---|---|
| `enabled` | `false` | 先手动开 |
| `dry_run` | `true` | 先看一天"会发什么"再真发 |
| 群白名单 | 显式列举 | 默认对一个群都不生效，避免拉进新群就开始刷 |
| 静默时段 | 23:00–08:00 | 凌晨发消息=必被踢 |
| 冷启动额度 | 每群 5 条/天起 | 先观察一周再抬 |
| 失败处理 | **完全静默** | 未开启主动发言时会失败，绝不能把错误刷进群里 |

---

## 5. 性能与成本（为什么闸门是必需的）

本机实测：`qwen3.8-27b-local` 生成 **约 8 tok/s**（`OPS.md` 第 4 节），`LocalModel` 单锁串行、`queue_limit: 3`，`official.log` 里真实回复 3.5–11.7 s。

- 一条自动接话 ≈ 一次完整推理 ≈ **3–12 秒**（还受联网工具影响：`TOOLS.md` 实测联网 11.1 s）。
- 群消息密集时（比如 1 条/20 秒的活跃群），全量接话会把单锁队列**持续占满**，真人 @ 机器人反而被 `overloaded` 拒载并回"我这边排队有点长"——这就是"自动聊天把机器人玩死"的典型路径。
- 因此：**冷却 ≥ 60 s 是底线**，且自动任务应该**优先级低于真人消息**（`LocalModel` 里 `_waiting` 计数已是天然优先级杠杆：自动任务可以在 `_waiting > 0` 时直接放弃本次触发，不排进队）。
- 另外，主动/接话每次都会换掉前缀缓存（`PERSONAS.md` 第 6 节：切人设互换 system 前缀会失效）。主动消息建议用**固定的一段 system 后缀**，别再拼多变内容。

---

## 6. 验证步骤（不改代码也能先做的探活）

落地前建议按这个顺序验，**每一步都能单独证伪**：

1. **群开关是否生效**：手机 QQ → 群设置 → 机器人 → 消息范围设为「获取群内全部消息」+ 打开「机器人主动在群聊内发言」。
2. **命令是否真的在跑**：`bash ~/qq-bot/svc.sh status`，再 `bash ~/qq-bot/svc.sh log official`。
3. **被动接话链路**：群里不 @ 发一句话，看日志有没有 `group_message_create`（当前 SDK 没有 parser 的话这里会是未知事件 → 说明第 1 节的补丁必须先打）。
4. **主动发言权限**：打补丁后临时发一条主动消息，看返回是成功还是 `22009` / 无权限错误。**这一步是唯一能确认"主动消息到底还能不能用"的方法**，别信文档（见第 2 节的矛盾说明）。
5. **频控与配额**：连续发两条同一 `msg_seq` 应失败（`msg_id + msg_seq` 去重）；错误码记进 `OPS.md`。
6. **回滚**：`auto_chat.enabled=false` + `bash ~/qq-bot/svc.sh restart official`，这是唯一的回滚动作，不涉及数据。

---

## 7. 风险与合规

| 风险 | 说明 | 缓解 |
|---|---|---|
| **被封/被限制** | 接话和主动发言都属于"机器人主动行为"，官方平台的运营规范对打扰用户有约束 | 低频、白名单、静默时段、默认关 |
| **被群友嫌** | 全量接话最容易变成刷屏 | 概率 + 冷却是硬需求，不是可选项 |
| **误触排查** | 自动行为出错很难归因 | `dry_run` + 结构化日志（每次触发记 `reason`） |
| **模型幻觉被当真** | 主动说的话没有上下文兜底 | 主动 prompt 里加"不确定就别下断言" |
| **内容审核** | 官方对生成内容有审核（`40034006` 已在 `PERSONAS.md` 记录过） | 自动消息用最保守的人设；`sanitize()` 必过 |
| **本地 GPU 争抢** | 与 DSH 共用 RTX 5080，27B 常驻已接近满载（`PLAN.md` 第 7 节） | 自动聊天只在人少时段开，或换小模型 |

---

## 8. 我的建议

1. **先只做形态 ① 的"必答层"**（关键词 + 概率 0.1 + 冷却 90 s，`dry_run` 跑一天），别一上来就上定时主动发言。
2. **主动发言（形态 ②）留作第二步**，且必须先跑第 6 节第 4 步探活——文档矛盾没解决之前，不要为它写自动化。
3. **不要做形态 ④**。收益最低、被嫌概率最高。
4. 真要"机器人自己找人聊天"，**NapCat 路线（`bot.py`）反而更自由**（没有平台配额与开关），代价是封号风险 —— 这条取舍记在 `PLAN.md` 第 2 节，随时可以从官方路线切回。

---

## 9. 参考来源

- [QQ 机器人 · 消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)（主动/被动消息、频控表、互动召回）
- [QQ 机器人 · 发送消息（send.md）](https://raw.githubusercontent.com/tencent-connect/bot-docs/master/docs/develop/api-v2/server-inter/message/send-receive/send.md)（2025-04-21 主动推送下线告示）
- [QQ 机器人 · 群消息（全量模式）](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
- [QQ 机器人 · 变更记录](https://bot.q.qq.com/wiki/develop/api-v2/changelog.html)
- [腾讯调整 QQ 机器人消息推送策略（IT之家，2025-04-20）](https://www.ithome.com/0/846/651.htm)
- [AstrBot · 通过 QQ 官方机器人接入（Websockets）](https://docs.astrbot.app/platform/qqofficial/websockets.html)（群设置开关路径、主动消息可用性）
- 本机代码：`official_bot.py`、`bot.py`、`config.json`；本机实测：`OPS.md`、`PERSONAS.md`、`TOOLS.md`
- 本机 SDK：`~/.venvs/qqbot/lib/python3.12/site-packages/botpy/`（`connection.py:84-88` 事件注册、`api.py:1380/1426` 发送签名、`flags.py:322-339` intents）
