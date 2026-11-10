# 代码精读：跟着一条消息读完整条链路

> 和架构导读不同，这篇**按代码顺序**走，每段配真实片段与行号。
> 读法：打开对应文件，跳到行号，对照着看；遇到不懂的函数名先跳过，先追主线。

---

## 1. 起点：`main()`（official_bot.py:2123）

```python
def main() -> int:
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()

    cfg = build_config(Path(args.config))       # 合并默认值 + config.json + 环境变量
    if args.selftest:
        return asyncio.run(selftest(cfg))       # 只测模型，不连网关

    appid = str(cfg.values.get("qq_appid") or "")   # 凭据
    if not appid or not secret:
        return 2                                 # 缺凭据直接退出
```

要点：
- `--selftest` 是给排查用的旁路，不碰 QQ 网关。
- 凭据缺失时 **返回非零**，`svc.sh` 会看到失败。

---

## 2. 组装对象

```python
personas = PersonaStore(cfg, HERE / "persona.state", HERE / "roles")   # 人设
responder = Responder(make_model(cfg, personas), cfg, personas)        # 回复器
client = QQBotClient(responder, policy, state, intents=intents)        # 网关
client.run(appid=appid, secret=secret)
```

`make_model`（2098）按配置选后端：

```python
def make_model(cfg, personas):
    backend = str(cfg.values.get("agent_backend") or "local").lower()
    if backend == "dsh":
        return DshAgent(cfg, personas)
    return LocalModel(cfg, personas)
```

> `Responder` 只依赖 `model.ask(session, prompt, system_suffix)`
> 这个统一接口——所以换后端不用改回复逻辑。

---

## 3. 连接后：`on_ready`（official_bot.py:1611）

连上网关后依次做：

1. 记日志（机器人名、intents）
2. 起「主次模型回收」任务
3. 起**记忆维护**任务（`_memory_maintenance` 1582，每天合并 + 淘汰）
4. 起自动发言调度
5. 后台连 MCP（`_init_mcp` 1681，如星露谷/nagi 工具）

> 顺序有讲究：主动发言要等网关鉴权完成，否则发不出去。

---

## 4. 消息入口：`on_c2c_message_create`（2053）

```python
if self._is_duplicate(message, content):     # 平台会重推同一条消息
    return
tags = await self._harvest_stickers(message) # 顺手把图收进表情包库
text, _ = message_text(message, tags)        # 解析文本（含表情/图片占位符）
self.responder.record_incoming(...)          # 先记入跨会话索引（判定前！）
await self.responder.answer(session, text, sender, ...)
```

两个容易忽略的点：

- **去重在最前面**，否则同一条消息会被回两遍；
- **记录在判定之前**，这样「即使没回」，这句也进了「刚才聊了什么」的原料。

---

## 5. 核心：`Responder.answer`（1214）

它是一条很长的流水线，按顺序：

### 5.1 指令（不经模型）

```python
handled, reply = self.personas.handle_command(prompt)   # 「人格 xxx」
handled, reply = self.memory_command(prompt)            # 「记忆」「忘掉 x」
handled, reply = self.growth_command(prompt)            # 「成长记录」
handled = detect_image_action(...)                      # 「/画 一只猫」
```

### 5.2 记忆检索（显式 + 自动）

```python
prompt, recall_query = parse_recall(prompt)     # [回忆:关键词]
explicit = bool(recall_query)
if not recall_query and self.recall_auto:
    recall_query = auto_query(prompt)           # 「上次/之前/还记得」
if recall_query:
    recall_block = self.recall.digest(recall_query, min_score=0.0 if explicit else 0.45)
```

### 5.3 打标签 + 拼 system 后缀

```python
prompt = f"[私聊 {profile.display}] {prompt}"    # 标签放 user
suffix = FACE_HINT + self.sticker_hint() + self.memory.digest() + self.growth.digest()
```

> **为什么标签不放 system**：system 要逐字稳定，前缀缓存才命中。

### 5.4 调模型

```python
reply = await self.model.ask(session, prompt[:max_prompt_chars], system_suffix=suffix)
```

### 5.5 摘标记 → 清洗 → 兜底

```python
reply, notes = parse_memory_marker(reply)        # [记住:…]
reply = self._absorb_growth(reply, session, speaker)  # [成长:…]
text = sanitize(reply, max_reply_chars)          # 去掉 URL、截断
if not message_has_content(text):                # 只回了个「？」
    retry = await self.model.ask(f"{session}#text-only", ...)   # 再要一次正经回答
```

### 5.6 发送：`_reply_with_sticker`（1399）

```python
for part in split_message(text):     # 按 450 字分片
    await sender(part, seq); seq += 1
if wants_sticker and seq <= MAX_PASSIVE_REPLIES:
    await image_sender(data, seq)    # 表情包占最后一个序号
```

---

## 6. DSH 后端：`DshAgent.ask`（dsh_agent.py:147）

```python
reply = await asyncio.to_thread(self._run, session, text)   # 同步 SDK 丢线程
if not reply.strip():                                       # 空正文兜底
    fresh_key = f"{session}#fresh{int(time.time())}"
    self._sessions[session] = self._session_id(fresh_key)   # 换新会话
    reply = await asyncio.to_thread(self._run, fresh_key, text)
```

`_run`（127）里做两件事：**首次见面注入人设**，然后 `resume_session`/`start_session`。

> 这个兜底是实战救回来的：长命会话堆满工具消息后会吐空正文。

---

## 7. 游戏循环：`GameLoop._tick`（game_loop.py:193）

```
预算检查（242）→ 观察 backend.observe() → 场景同步 → 规则（体力/时间）
→ 现场候选 → 移动或动作（272）→ 复核（308）→ 记账
```

`_do_tile_action` 的关键逻辑：

```python
if dist > 1 or (diagonal and kind != "harvest"):
    await self.backend.move_to(x, y)      # 斜对角先走一步
    return
for _ in range(swings):                   # 多击资源连挥
    ok, result = await self.backend.act(kind, x, y)
    if ok and await self._verify_tile(kind, x, y):
        break                             # 只有复核通过才算成功
```

`_verify_tile`（308）等动画落地后重新观察：`cropReady`/`waterState`/`breakable` 变了才算。

---

## 8. 记忆三件套

| 文件 | 读入口 | 写入入口 |
|---|---|---|
| `user_memory.py` | `digest`（437，注入 system） | `add_notes`（合并规则） |
| `cross_session.py` | `digest`（145，拼 user） | `record`（判定前记录） |
| `growth_log.py` | `digest`（194，注入 system） | `append`（哈希链，只追加） |
| `memory_search.py` | `digest`（223，按需检索） | —（只读索引） |

---

## 9. 建议的阅读顺序（3 天）

| 天 | 读什么 | 目标 |
|---|---|---|
| 1 | `main` → `on_ready` → `on_c2c_message_create` | 知道一条消息从哪进来 |
| 2 | `Responder.answer` 前 100 行 + `_reply_with_sticker` | 知道回复怎么产生和发出 |
| 3 | `DshAgent.ask` + `GameLoop._tick` + 记忆 `digest` | 知道模型/游戏/记忆怎么接的 |

读的时候**随手改一个常量**（比如 `MAX_PASSIVE_REPLIES`），重启看日志，比干读有效得多。
