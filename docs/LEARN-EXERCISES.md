# 练习册：从「找到」到「改坏再修好」

> 每题都有**验收标准**和**提示（文件:行）**。不要一次做太多，做完一题提交一次：
> `git add -A && git commit -m "练习: xxx"`。

---

## L1 · 找东西（只读，不改代码）

| # | 题目 | 验收 | 提示 |
|---|---|---|---|
| 1 | 机器人启动时，先检查哪些凭据？缺了会返回什么？ | 说出两个字段名和返回码 | `official_bot.py:2123` |
| 2 | 一条私聊消息进来，**先去重还是先回复**？为什么？ | 说出顺序和原因 | `official_bot.py:2053` |
| 3 | 回复正文太短（只回「？」）时会做什么？ | 说出「重试」策略 | `answer` 里的 `message_has_content` |
| 4 | 表情包最多占几个回复序号？超了会怎样？ | 说出上限与行为 | `MAX_PASSIVE_REPLIES` |
| 5 | 游戏工具在「游戏没开」时靠什么被跳过？ | 说出两处门控 | `mcp_bridge.py:34`、`tools/gate-nagi.sh` |

---

## L2 · 改行为（改常量/配置，重启验证）

| # | 题目 | 怎么改 | 验收 |
|---|---|---|---|
| 1 | 让一条回复最多 200 字（原 400） | `config.json` 的 `max_reply_chars` | 发一条长回复，观察是否截断 |
| 2 | 聊天历史只保留 2 轮 | `history_turns` | 问「我刚才说了什么」，看它多快忘 |
| 3 | 记忆档案摘要从 8 人改成 3 人 | `user_memory.py` 的 `digest(limit=8)` 默认值 | 重启后 `/记忆` 输出变短 |
| 4 | 成长附录注入从 6 条改成 3 条 | `growth_log.py` 的 `INJECT_COUNT` | `python3 growth_log.py --tail 3` 对照 |
| 5 | 游戏循环改为每 30 秒醒一次 | `config.json` 的 `game_loop.interval_seconds` | 日志里 `[game]` 间隔变化 |

> 每题改完都要：`bash svc.sh restart official`，再 `tail -f logs/official.log` 确认生效。

---

## L3 · 加功能（写代码）

### 3.1 加一条「提醒」命令（入门）

**目标**：私聊发「提醒」→ 回固定话，不走模型。

**骨架**（照抄 `growth_command` 的写法，`official_bot.py:1143`）：

```python
def remind_command(self, text: str) -> tuple[bool, str]:
    if text.strip() in ("提醒", "提醒一下"):
        return True, "该喝水啦，主人。"
    return False, ""
```

**插入点**：`answer()` 里 `growth_command` 判断之后（`official_bot.py:1255` 之后）。

**验收**：发「提醒」秒回固定句；发「提醒我明天开会」**不**命中（严格相等）。

---

### 3.2 给记忆检索加一个来源（进阶）

**目标**：把 `notes/` 目录下的 Markdown 也纳入检索。

**步骤**：

1. `memory_search.py` 加 `_load_notes()`，读 `notes/*.md`，每行截断成条目（`Source="笔记"`）。
2. 在 `all_items()` 里把新来源拼进去。
3. `test_memory_search.py` 加一个用例：临时 md 文件能被检索到。

**验收**：`[回忆:Python]` 能命中笔记里的内容；单测全绿。

---

### 3.3 给游戏后端加能力（进阶）

**目标**：Nagi 后端支持「回床睡觉」。

**步骤**：

1. `game_backend.py` 的 `NagiBackend` 已有一个 `sleep()` 方法（POST `/sleep`）。
2. 在 `set_mode` 里加一个 `sleep` 分支：调 `self.sleep()` 并返回 True。
3. `game_loop.py` 的 `STRATEGY_MODES` 加 `"sleep"`，规则层（体力低/天黑）改成返回 sleep。

**验收**：游戏内 18 点后日志出现 `模式 → sleep`，角色回床（注意存档！先备份）。

---

## L4 · 排障（跑起来，找原因）

| # | 场景 | 你要做的 | 判断标准 |
|---|---|---|---|
| 1 | 机器人不回消息 | 按 [LEARN-LINUX 第 9 节](LEARN-LINUX.md) 五步排查 | 能定位是「进程没起 / 模型没起 / 会话坏」之一 |
| 2 | 回复要 20 秒 | 看日志里有没有工具调用；确认 `dsh-qq.patch.yml` 的 disabled 是否生效 | 能说出慢在「模型思考」还是「工具探索」 |
| 3 | 游戏工具失效 | 检查 7842、bridge 文件 mtime、是否重启过机器人 | 能说出「门控跳过了」还是「游戏没开」 |

---

## L5 · 挑战

1. **把「记忆检索」做成可观测**：在 `记忆` 指令里额外输出「最近命中过哪些查询」。
   提示：`MemorySearch` 加一个 `hits` 计数器，`describe()` 里拼进去。
2. **给 `_verify_tile` 加退避**：同一格连续 3 次「上报成功但没变化」后，把等待时间从 0.8s 加到 1.5s 再试。
   提示：`game_loop.py:308`，用字典记每格失败次数（已有 `self.failures`）。

---

## 提交规范

```bash
git add -A
git commit -m "练习: L2-1 回复上限改 200 字"
```

做错了也没关系：

```bash
git reset --hard HEAD~1     # 丢弃最近一次提交
git checkout -- 文件名       # 丢弃单个文件未提交的改动
```
