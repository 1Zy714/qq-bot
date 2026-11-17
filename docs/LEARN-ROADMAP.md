# 学习路线图（能力地图版）

> 和 [LEARN-PLAN-1H.md](LEARN-PLAN-1H.md) 的区别：
> 那份是**日历**（第几周干什么），这份是**地图**（要具备哪些能力、怎么自评、Java 经验怎么迁移）。
> 配套：[LEARN.md](LEARN.md) 总入口、[LEARN-LINUX.md](LEARN-LINUX.md)、[LEARN-PYTHON.md](LEARN-PYTHON.md)、[LEARN-EXERCISES.md](LEARN-EXERCISES.md)。

---

## 一、定位：从 Java 后端到「能独立维护这个 AI 机器人」

前提假设：有 Java/Spring 后端经验（MES 那套：Spring Boot、MyBatis、Redis、MQ），
但没系统用过 Linux 命令行、Python 和 AI 应用层。

目标不是「转行做算法」，而是：**能读懂、能部署、能扩展这个 Python + LLM 项目**。

```
                          ┌────────────────────────────┐
                          │  M3  能扩展 AI 能力         │
                          │  MCP 工具 / 记忆 / Agent 循环│
                          └────────────▲───────────────┘
                                       │
                          ┌────────────┴───────────────┐
                          │  M2  能读能改这个项目       │
                          │  架构 / 代码精读 / 加功能   │
                          └────────────▲───────────────┘
                                       │
                          ┌────────────┴───────────────┐
                          │  M1  能跑、能排障           │
                          │  Linux 命令 / 启停 / 日志   │
                          └────────────▲───────────────┘
                                       │
     ┌──────────────┬──────────────────┴─────────┬──────────────────┐
     │ Linux 服务器 │        Python 语言         │   网络与数据      │
     │ 进程/端口/日志│ 语法/文件/async/异常       │ HTTP/JSON/curl    │
     └──────────────┴────────────────────────────┴──────────────────┘
                                       │
                          ┌────────────┴───────────────┐
                          │  AI 基础（最后叠上去）      │
                          │ token/提示词/本地模型/工具调用│
                          └────────────────────────────┘
```

---

## 二、里程碑与自评（0=没概念 1=见过 2=能照着做 3=能独立做）

### M1 · 能跑能排障（约 2 周，每周 5 小时）

| 能力 | 3 分的标准 |
|---|---|
| 看进程 | 不看文档能用 `ps`/`pgrep` 找到机器人并判断是否活着 |
| 看端口 | 用 `ss` / `curl` 判断 3080/8080/7842 是否可用 |
| 看日志 | `tail -f` + `grep` 定位最近一次报错的时间与原因 |
| 启停服务 | 独立完成 `svc.sh start/stop/restart/status` 并解释每一步 |
| 权限/环境变量 | 能给脚本加执行权限、能设 `LOCAL_LLM_KEY` 并验证 |

### M2 · 能读能改（约 4 周）

| 能力 | 3 分的标准 |
|---|---|
| 架构 | 能画出「消息 → answer → 模型 → 发送」的链路 |
| 读代码 | 能说清 `Responder.answer` 里指令判断、检索、模型调用、分片的顺序 |
| Python | 看懂 dataclass/异常/文件 JSON/async，能改一个常量并验证 |
| 记忆 | 能解释「写入合并 / 按需检索 / 每天整理」三件事 |
| 排障 | 遇到「不回/很慢/空正文」能按文档定位到具体一层 |

### M3 · 能扩展（约 6 周）

| 能力 | 3 分的标准 |
|---|---|
| 工具化 | 能把一个 HTTP 接口包成 MCP 工具（参考 `tools/nagi_mcp.py`） |
| Agent 循环 | 能解释「观察 → 决策 → 动作 → 复核」并给游戏循环加一个模式 |
| AI 概念 | 能说清 token/上下文/温度、本地模型与云端差异、提示词分层 |
| 工程化 | 改完能补测试、写文档、提交 PR 级 commit |

---

## 三、Java 经验怎么迁到 Python（省时间的关键）

| Java 里你熟悉的 | Python 里对应 | 差异提醒 |
|---|---|---|
| `class` + 构造器 + getter | `@dataclass` | 不用写样板代码 |
| `List`/`Map`/`Set` | `list`/`dict`/`set` | 内置、无泛型擦除 |
| 接口 + 实现 | 鸭子类型 / 协议 | 不需要显式接口 |
| Spring 依赖注入 | 手动构造 + 传参 | 项目里就是 `Responder(model, cfg, personas)` |
| Maven/Gradle | `venv` + `pip` | 依赖装在 `~/.venvs/qqbot` |
| 线程池 / `CompletableFuture` | `asyncio` + `await` | 单线程事件循环，阻塞调用要 `to_thread` |
| `try/catch/finally` | 一样 | 但更鼓励「降级返回」 |
| 注解 `@Transactional` | 装饰器 / 上下文管理器 | 语法像，语义不同 |
| JVM GC | 引用计数 + 循环 GC | 不必太关心，但要避免循环引用 |
| 日志 slf4j | `logging` / botpy logger | 占位符写法 `LOG.info("x=%s", x)` |

> **建议**：学 Python 时别从零背语法，先看 [LEARN-PYTHON.md](LEARN-PYTHON.md) 的「Java 对照」思路，
> 每天用 Java 的经验去猜 Python 怎么写，再验证——速度会快很多。

---

## 四、建议投入与节奏

| 里程碑 | 周数 | 每周投入 | 验收方式 |
|---|---|---|---|
| M1 | 2 周 | 5 小时（5 天 × 1h） | 独立完成一次「查进程 → 看日志 → 重启」 |
| M2 | 4 周 | 5 小时 | 完成练习册 L1/L2 + 一个小功能 |
| M3 | 6 周 | 5 小时 | 把某个 HTTP 接口包成 MCP 工具 + 一个游戏模式 |

合计约 12 周（与 [LEARN-PLAN-1H.md](LEARN-PLAN-1H.md) 的日历版一致）。

---

## 五、自检清单（每周日花 10 分钟）

- [ ] 本周我让什么**跑起来**了？
- [ ] 我能不看文档复述哪一步？
- [ ] 我改的代码有测试/日志能证明它生效吗？
- [ ] 下周要补的最小缺口是什么？

---

## 六、资源索引

| 想要 | 去这里 |
|---|---|
| 总路线 | [LEARN.md](LEARN.md) |
| 日历（12 周） | [LEARN-PLAN-1H.md](LEARN-PLAN-1H.md) |
| Linux 命令 | [LEARN-LINUX.md](LEARN-LINUX.md) |
| Python 语法 | [LEARN-PYTHON.md](LEARN-PYTHON.md) |
| 架构地图 | [LEARN-ARCHITECTURE.md](LEARN-ARCHITECTURE.md) |
| 代码精读 | [LEARN-CODE-WALK.md](LEARN-CODE-WALK.md) |
| 练习册 | [LEARN-EXERCISES.md](LEARN-EXERCISES.md) |
