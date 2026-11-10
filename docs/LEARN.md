# 学习路线：从零到能改这个机器人

> 这份是**总入口**。不想学全，只想「会改、会排障、不弄坏」，按下面顺序走一遍即可。

---

## 一、先搞清楚你在学什么

```
Linux 命令   → 会看进程/端口/日志/文件（排障的入口）
Python 语法  → 看得懂代码、能加一个小功能
项目结构     → 知道一个请求从 QQ 消息到回复经过了哪几层
```

这个项目是个很好的教材：**不复杂、能跑、有日志、有测试**，而且出问题会立刻看见。

---

## 二、推荐路线（7 天版）

| 天 | 学什么 | 用什么练 | 文档 |
|---|---|---|---|
| 1 | 项目跑起来：venv、启动、看日志 | `bash svc.sh start official`、`tail -f logs/official.log` | [README](../README.md) |
| 2 | Linux 基础：进程 / 端口 / 日志 / 文件 | 用 `ps` `ss` `grep` 查一次机器人状态 | [LEARN-LINUX.md](LEARN-LINUX.md) |
| 3 | Python 基础：变量 / 字符串 / 列表字典 | 读 `user_memory.py` 前 150 行 | [LEARN-PYTHON.md](LEARN-PYTHON.md) |
| 4 | Python 进阶：dataclass / 异常 / 文件 / JSON | 读 `growth_log.py`（只追加 + 哈希链） | 同上 |
| 5 | async/await 与项目分层 | 读 `official_bot.py` 的 `Responder.answer` | 同上 |
| 6 | 改一个小功能 | 加一条「提醒」命令（练习在 LEARN-PYTHON 第 13 节） | 同上 |
| 7 | 排障实战 | 按 LEARN-LINUX 第 9 节走一遍 | [OPS.md](OPS.md) |

---

## 二·五、三篇「结合代码」的深入文档

| 文档 | 内容 |
|---|---|
| [LEARN-ARCHITECTURE.md](LEARN-ARCHITECTURE.md) | 架构地图：一个 QQ 消息如何变成回复、模块职责表、三条数据流 |
| [LEARN-CODE-WALK.md](LEARN-CODE-WALK.md) | 代码精读：按真实行号走一遍 `main → answer → 发消息 → 游戏/记忆` |
| [LEARN-EXERCISES.md](LEARN-EXERCISES.md) | 练习册：L1 找东西 → L5 挑战，每题有验收标准和提示行号 |

## 三、想更深入（14 天版）

在 7 天版基础上加：

| 主题 | 读什么 |
|---|---|
| 逐行精读 Python（知识点 → 行号） | [LEARNING.md](LEARNING.md) |
| 记忆系统（合并 / 检索 / 衰减） | [MEMORY-SEARCH.md](MEMORY-SEARCH.md) |
| DSH 桥接（会话、工具、兜底） | [DSH-BRIDGE.md](DSH-BRIDGE.md) |
| 游戏后端（同伴 / 玩家两种） | [GAME-BACKEND.md](GAME-BACKEND.md) |
| 聊天性能（为什么快/慢） | [CHAT-PERF.md](CHAT-PERF.md) |

---

## 四、学习方法：三轮循环

1. **看**：读一段代码/文档，只求「知道它在干嘛」
2. **改**：改一个常量或一句话，重启，看日志变化（改错也没关系，git 能回退）
3. **讲**：合上文档，用自己的话复述这一步做了什么

> 检验标准不是「看完」，而是「能改坏、能修好」。

---

## 五、每次改完的固定动作

```bash
# 1. 语法检查（快）
~/.venvs/qqbot/bin/python -m py_compile 改过的文件.py

# 2. 跑相关测试
~/.venvs/qqbot/bin/python test_user_memory.py

# 3. 重启并看日志
bash svc.sh restart official
tail -f logs/official.log
```

---

## 六、出问题先看这里

| 症状 | 先查 |
|---|---|
| 机器人不回 | 进程在不在（`pgrep`）、模型端口（8080）、日志最后 50 行 |
| 回很慢 | 模型是否在跑、GPU 显存、是否有工具在翻文件 |
| 报「空正文」 | DSH 会话坏了（[DSH-BRIDGE.md](DSH-BRIDGE.md) 第二节） |
| 游戏工具用不了 | 游戏开没开、有没有重启机器人（MCP 启动时挂载） |

---

## 七、别踩的坑

1. **别在配置文件里手写时间戳的路径**：用 `Path(__file__).parent`。
2. **别把密钥贴进聊天**：`config.json`、`.credentials.yaml` 里是真实凭据。
3. **改之前先提交或备份**：`git add -A && git commit -m "savepoint"`。
4. **一次只改一处**：同时改三个地方，出问题时不知道是谁的锅。
