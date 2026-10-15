# 群聊免 @ 自动回复

让机器人在群里**不用 @ 也能自动接话**。两条路线都改了，但**官方平台这条路卡在腾讯那边**，见下。

---

## 1. 结论先看

| | 能做到吗 | 卡在哪 |
|---|---|---|
| **NapCat 路线**（`bot.py`） | ✅ 纯代码就能做到 | 只需 NapCat 里打开群消息推送 + 配置开关 |
| **官方平台**（`official_bot.py`，你在用的） | ⚠️ 代码已就绪，但**必须先在开放平台后台给机器人开启「接收所有消息」** | 这是腾讯的功能开关，只能你在网页上开 |

配置里 `group_reply_all` 已置为 `true`，机器人也重启加载了。
**但如果后台没开「接收所有消息」，这个功能不会生效**——腾讯根本不会把非 @ 的群消息推给你的机器人。

---

## 2. 为什么官方路线做起来这么绕

### 2.1 平台侧：两个不同的事件

腾讯的文档写得很明确（[群消息（全量模式）](https://bot.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)）：

> 当机器人开启了"接收所有消息"功能后，群里的每一条消息（不限于@机器人）都会推送此事件。

| 事件名 | 何时推送 |
|---|---|
| `GROUP_AT_MESSAGE_CREATE` | **只在被 @ 时**（现在用的是这个） |
| `GROUP_MESSAGE_CREATE` | 群里**每一条**消息（需开启「接收所有消息」） |

好消息：两者 **intent 完全相同**（都是 `1<<25`），事件体字段也**完全一致**，
`content` 同样已去掉 @ 前缀。所以不需要改 intent，也不用重新申请权限位。

### 2.2 SDK 侧：botpy 1.2.1 不认这个事件

botpy 只实现了 @ 那个。它的分发机制是这样的：

```python
# botpy/connection.py  ConnectionState.__init__
self.parsers = {}
for attr, func in inspect.getmembers(self):
    if attr.startswith("parse_"):
        self.parsers[attr[6:].lower()] = func

# botpy/gateway.py  收到事件时
event = msg["t"].lower()          # "group_message_create"
func = self._parser[event]        # KeyError -> "[botpy] _parser unknown event ..."
```

也就是说：**腾讯推了，botpy 查不到解析函数，直接丢事件并打一行 error 日志。**

因为分发表是**运行时用 `inspect.getmembers` 扫出来的**，所以不用改 SDK 源码，
在启动前往 `ConnectionState` 上挂一个同名方法就够了：

```python
# official_bot.py
def install_group_message_create_support() -> bool:
    from botpy.connection import ConnectionState
    if hasattr(ConnectionState, "parse_group_message_create"):
        return False
    def parse_group_message_create(self, payload):
        message = GroupMessage(self.api, payload.get("id"), payload.get("d", {}))
        self._dispatch("group_message_create", message)
    ConnectionState.parse_group_message_create = parse_group_message_create
    return True
```

已实测：补丁后解析表里同时有 `group_message_create` 和 `group_at_message_create`，
`_dispatch` 能正确派出 `group_message_create`。

---

## 3. 你需要做的一步：开启「接收所有消息」

登录 [QQ 开放平台](https://bot.q.qq.com/open) → 进入你的机器人（AppID `1903562070`）→
在**功能配置 / 消息设置**一类的页面里找 **「接收所有消息」** 并开启。

> ⚠️ 我没能确认这个开关的具体位置、是否需要申请审核、或者是否只对特定机器人开放。
> 这个功能是较新的（官方文档 2026-09-16 才更新），如果后台里找不到，
> 说明你的机器人暂时没这个权限，那就只能用回 @ 模式。

**怎么知道开没开成**：机器人启动后，在群里随便发条不带 @ 的消息，然后看日志：

```bash
grep "全量群消息" ~/qq-bot/official.log
```

- 有输出 → 开关生效了 ✅
- 一直没有 → 后台没开，或者腾讯没推 ❌

---

## 4. 配置

```jsonc
"group_reply_all": true,          // 群里不 @ 也回（已置 true）
"group_cooldown_seconds": 5.0     // 每群冷却，防刷屏
```

想关掉就把 `group_reply_all` 改成 `false` 并重启。

### 两条路线的差异

| | 官方平台 | NapCat |
|---|---|---|
| 需要平台开关 | ✅ 必须 | ❌ 不需要（但要在 NapCat 里开群消息推送） |
| 冷却实现位置 | `QQBotClient.on_group_message_create` | `bot.py` 的 `_trigger` + `on_event` |
| 其他机器人消息 | 已过滤（`author.bot` 为真则跳过） | 由 NapCat 侧决定 |
| 串行队列 | ❌ 没有 | ✅ 有单并发 `worker` |

---

## 5. ⚠️ 开之前请认真考虑的两件事

### 5.1 很吃 GPU，而且会和出图互相挤

这条机器人的回复不是模板，是 **27B 模型真推理**——每条 3~12 秒。
群里只要有几个人聊天，GPU 就会被持续占满。后果：

- 出图（40~100 秒）会和聊天抢显卡；官方路线**没有排队**，
  并发时可能把出图挤爆，或让聊天自己 `httpx.ReadTimeout`。
  （这个现象在开全量之前就已经实测出现过一次）
- **冷却 5 秒其实很激进**。按每条回复 8 秒算，等于 GPU 几乎没有空闲。
  活跃群建议调到 `15`~`30`，或者干脆只在自己的小群里用。

### 5.2 会刷屏

机器人会对你群里**每一句话**发表意见，包括别人之间正常聊天。
建议先在小群试，确认语气和频率能接受再考虑大群。

真要长期用，更稳的做法是给 `Responder` 加一把 `asyncio.Lock` 串行化
（官方路线目前完全没有并发控制），以及给聊天和出图分别限流。这些还没做。

---

## 6. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `official_bot.py` | 新增 `install_group_message_create_support()` 补丁、`on_group_message_create` 处理、每群冷却、`_senders()` 抽取（三个入口共用） |
| `bot.py` | `DEFAULTS` 新增 `group_reply_all`；`_trigger` 在原有规则之后追加全量模式兜底 |
| `config.json` / `config.example.json` | 新增 `group_reply_all`（`config.json` 里已置 `true`） |

原有触发方式**全部保持不变**：`/ai`、`#ai`、@机器人、出图前缀，优先级都在全量兜底之前。
实测（NapCat 路线）：

```
group_reply_all=False          group_reply_all=True
  你好呀      -> 不回             你好呀      -> 回 "你好呀"
  /ai 你好    -> 回 "你好"        /ai 你好    -> 回 "你好"
  /画 一只猫  -> 出图             /画 一只猫  -> 出图
```
