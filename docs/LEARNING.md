# 用本项目学 Python

这是一份**边读代码边学 Python** 的指南。所有例子都来自本仓库真实代码，标注了 `文件:行号`，
你可以直接跳过去对照阅读。

> **为什么用这个项目学**：它体量正好（3 个文件、不到 1000 行），但覆盖了真实工程里
> 90% 的高频语法 —— 类型注解、`dataclass`、推导式、异常处理、`async/await`、`asyncio` 队列、
> 闭包、正则、`pathlib`、日志、配置分层、命令行参数。而且**它必须跑在真实环境里**
> （QQ + 本地大模型），出错不会只是"测试不通过"，而是"群里没人收到回复"，
> 反馈非常直接。

---

## 0. 这份文档怎么用

三步法，**不要一上来就读代码**：

| 步骤 | 做什么 | 时间 |
|---|---|---|
| **① 先跑通** | 让机器人真的回一句话，看到日志滚动 | 30 分钟 |
| **② 再读码** | 按第 2 节的顺序读，每读一段就改一小处、重启、看日志 | 2–3 周 |
| **③ 再动手** | 做第 8 节的练习，从「加一条指令」到「写单测」 | 持续 |

**前置要求**：会一点命令行，知道 `cd` / `ls` / `cat`。**不需要**预先会 Python 语法。

**唯一要养成的习惯**：每改一行代码，就 `bash svc.sh restart official && bash svc.sh log official`，
看日志有没有你的改动生效。**看得见的反馈**是自学编程最快的方式。

---

## 1. 先跑通（做完这一步再往下读）

```bash
cd ~/qq-bot
cp config.example.json config.json        # 复制配置模板
bash run.sh --selftest                    # 只测模型，不需要 QQ，最快验证环境
bash svc.sh status                        # 看进程状态
bash svc.sh log official                  # 实时日志（Ctrl-C 退出）
```

`--selftest` 会在终端打印一行模型回复和耗时。**看到这行字，说明 Python 环境和模型链路都通了**。

注意 `run.sh` 本身也是一份教材：它用 `bash` 写，但里面的 `pgrep`、`setsid`、日志重定向
是运维基本功。第 6 节会讲。

---

## 2. 代码地图：知识点 → 行号

**按这个顺序读**（从"程序的骨架"到"程序的肌肉"）：

| 顺序 | 读什么 | 学到什么 |
|---|---|---|
| 1 | `bot.py:456-479` | 程序入口、`argparse`、`if __name__ == "__main__"` |
| 2 | `bot.py:81-99` | `@dataclass`、`__getattr__`、`@classmethod` |
| 3 | `bot.py:112-139` | 字符串处理、f-string、`str.partition` |
| 4 | `bot.py:142-236` | 类、推导式、`lambda` 排序、正则 |
| 5 | `bot.py:239-341` | 构造函数、`asyncio.Queue`、`dict`/`deque`、早返回 |
| 6 | `bot.py:345-433` | 闭包、打包消息、`try/except/finally`、`async with` |
| 7 | `official_bot.py:154-228` | **闭包与回调**、继承、`@property`、`super()` |
| 8 | `web_tools.py:145-220` | 函数当值用（dict 派发）、循环与状态、JSON |
| 9 | `official_bot.py:36-89` | 正则、字符串链式调用、模块导入 hack |

**知识点速查表**（想学某个语法时直接跳）：

| 知识点 | 位置 | 一句话说明 |
|---|---|---|
| 程序入口 / 命令行参数 | `bot.py:456-479` | `argparse` 解析 `--config`、`--selftest` |
| 类型注解 | `bot.py:44`、`245-248` | `dict[str, Any]`、`Task[None] \| None` |
| `@dataclass` | `bot.py:81-85` | 自动生成 `__init__`，用 `field()` 处理可变默认值 |
| `__getattr__` | `bot.py:87-91` | 属性找不到时的兜底，让 `cfg.model` 可用 |
| `@classmethod` | `bot.py:93-99` | 工厂方法 `Config.load(path)` |
| 字符串方法 | `bot.py:114-121`、`web_tools.py:79-81` | `strip`、`partition`、`replace`、`re.sub` |
| `f-string` | `bot.py:134`、`web_tools.py:98` | `f"{index}. {item}"` |
| 推导式 | `bot.py:151-159` | 字典推导 / 列表推导，一行替代 for+append |
| `lambda` + `sorted(key=)` | `bot.py:216` | 按关键词长度倒序排，长的优先匹配 |
| 正则表达式 | `web_tools.py:128-132`、`official_bot.py:49` | `findall` / `search` / `sub` / `compile` |
| 继承与 `super()` | `official_bot.py:186-190` | `class QQBotClient(botpy.Client)` |
| `@property` | `official_bot.py:103-113` | 把方法伪装成属性，实现懒加载 |
| 闭包 / 回调 | `official_bot.py:202-204` | 嵌套函数捕获外层变量 `message` |
| 异常处理 | `bot.py:368-380`、`web_tools.py:155-158` | `try/except/finally`、吞异常与不吞异常的取舍 |
| `deque(maxlen=N)` | `bot.py:131`、`386` | 定长队列，满了自动丢最旧的 |
| `async def` / `await` | `web_tools.py:84-88` | 协程与等待 |
| `asyncio.Lock` | `official_bot.py:99`、`129` | 串行化并发请求 |
| `asyncio.Queue` | `bot.py:245`、`368-380` | 生产者-消费者队列 |
| `asyncio.create_task` | `bot.py:426`、`official_bot.py:193` | 后台任务 |
| `async with` | `bot.py:427`、`official_bot.py:129` | 异步上下文管理器 |
| 模块导入 | `official_bot.py:36-40` | `sys.path.insert` 让同目录模块可导入 |
| 文件读写 | `bot.py:97-98`、`198` | `pathlib.Path.read_text/write_text` |
| 日志 | `bot.py:102-109` | `logging.basicConfig` |
| `json` | `bot.py:98`、`web_tools.py:207` | `loads` / `dumps`，注意它会抛异常 |

---

## 3. 基础篇

### 3.1 程序入口：`main()` 与 `if __name__ == "__main__"`

看 `bot.py:456-479`：

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="OneBot v11 → Ollama QQ 机器人适配器")
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()          # 解析命令行
    ...
    if args.selftest:
        return asyncio.run(selftest(cfg))
    ...
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

**三个必懂的点**：

1. **`if __name__ == "__main__":`** —— 只有"直接运行这个文件"时才为真。
   当 `official_bot.py:39` 写 `from bot import Config` 时，`bot.py` 被导入，
   这段就不会执行。**否则一导入就把机器人启动了**，这是新手最常见的翻车点。
2. **`raise SystemExit(main())`** 而不是 `main()` —— 把函数的返回值变成进程退出码。
   这样 `run.sh` 里就能用 `$?` 判断成败。
3. **`argparse`** 的标准三连：建 parser → `add_argument` → `parse_args()`。
   `action="store_true"` 表示这是个开关（出现即为 `True`）。

**练习**：给 `bot.py` 加一个 `--version` 参数，打印版本号后退出。

---

### 3.2 类型注解：给变量贴上标签

Python 不强制类型，但**注解是给人（和 IDE）看的文档**，本项目全程使用：

```python
# bot.py:44        模块级变量 + 注解
DEFAULTS: dict[str, Any] = { ... }

# bot.py:245-248   实例变量
self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
self.history: dict[str, deque[dict[str, str]]] = {}
self.worker_task: asyncio.Task[None] | None = None

# bot.py:278       函数签名声明返回一个元组
def _trigger(self, event: dict[str, Any], text: str) -> tuple[bool, str]:
```

要认识的四种写法：

| 写法 | 含义 | 出处 |
|---|---|---|
| `dict[str, Any]` | 键是字符串、值任意类型的字典 | `bot.py:44` |
| `list[dict[str, Any]]` | 字典组成的列表（消息列表） | `bot.py:253` |
| `Task[None] \| None` | "Task **或** None"，`\|` 是"或" | `bot.py:248` |
| `-> tuple[bool, str]` | 返回"两个值"的元组 | `bot.py:278` |

看到文件顶部的 `from __future__ import annotations`（`bot.py:16`）了吗？
它让注解**延迟求值**——所以在老版本 Python 上写 `dict[str, Any]` 也不会报错。这是兼容性技巧。

> ⚠️ **注解不会在运行时检查类型**。`x: int = "hello"` 完全合法，跑起来也不会报错。
> 想要真检查，要装 `mypy` 或 `pyright`（见第 8 节练习 11）。

---

### 3.3 字符串：项目里最常用的操作

**f-string**（最常用的格式化方式）：

```python
# bot.py:134
parts.append(f"@{data.get('qq', '')} ")

# web_tools.py:98   enumerate 提供序号
lines = "\n".join(f"{index}. {item}" for index, item in enumerate(news, start=1))
```

**隐式字符串拼接**（括号里相邻的字面量自动合并）：

```python
# web_tools.py:23-26
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
```

**`str.partition` 切三段**（比 `split` 更适合"切一次"的场景）：

```python
# bot.py:115-119
for open_tag, close_tag in (("<think>", "</think>"), ("<thinking>", "</thinking>")):
    while open_tag in out and close_tag in out:
        head, _, rest = out.partition(open_tag)   # 前 / 分隔符 / 后
        _, _, tail = rest.partition(close_tag)
        out = (head + tail).strip()               # 把中间整块丢掉
```

`_` 是"我不关心这个值"的约定写法。

**其他高频方法**（都在本仓库出现过）：

| 方法 | 作用 | 出处 |
|---|---|---|
| `s.strip()` / `.strip("，。")` | 去两端空白 / 去指定字符 | `bot.py:114`、`221` |
| `s.startswith(prefix)` | 判断前缀 | `bot.py:217`、`292` |
| `s.lower()` | 转小写（做忽略大小写比较） | `bot.py:217` |
| `s.replace(a, b, 1)` | 替换（第 3 个参数限次数） | `bot.py:297` |
| `"、".join(list)` | 用顿号把列表拼成字符串 | `bot.py:194` |
| `s[:8]` / `s[:-1]` | 切片（取前 8 个 / 去掉最后一个） | `web_tools.py:95` |

**练习**：`clean_reply`（`bot.py:112-122`）目前只剥 `"` 和 `「」`。试着让它也剥掉
多余的换行和空格（提示：`re.sub(r"\n{2,}", "\n", out)`）。

---

### 3.4 容器：list / dict / deque

**列表推导式**（一行替代 `for` + `append`）：

```python
# bot.py:154
self.words: list[str] = [str(w) for w in (cfg.persona_words or [])]
```

**字典推导式**（把 key/value 转换后重建字典）：

```python
# bot.py:151-153
self.personas: dict[str, str] = {
    str(k): str(v) for k, v in (cfg.personas or {}).items()
}
```

> `cfg.personas or {}` 是**短路求值**：如果 `cfg.personas` 是 `None` 或空字典，
> 就用 `{}`。这是 Python 里非常常见的防御写法。

**`deque(maxlen=N)` —— 定长队列**（本项目用它存对话历史）：

```python
# bot.py:385-386
turns = max(1, int(self.cfg.history_turns)) * 2
buf = self.history.setdefault(session, deque(maxlen=turns))
```

`deque` 满了会自动**从左边丢掉最旧的**。所以"只保留最近 N 轮对话"不需要任何清理代码。
`dict.setdefault(key, default)` = "取 key，没有就设成 default 再返回"。

**字典的三种取值方式**（区别很重要）：

```python
cfg.values["model"]            # 键不存在 → 抛 KeyError
cfg.values.get("model")        # 键不存在 → 返回 None
cfg.values.get("model", "x")   # 键不存在 → 返回 "x"
```

本项目的选择：**配置读取用 `.get()`**（容错），**必须存在的用 `[]`**（快速失败）。
`bot.py:88-91` 是前者：

```python
def __getattr__(self, item: str) -> Any:
    try:
        return self.values[item]
    except KeyError as exc:
        raise AttributeError(item) from exc     # 转成 AttributeError 更符合直觉
```

**练习**：把 `bot.py:44-78` 的 `DEFAULTS` 里的 `reset_words` 改成一个 `set`，
想想为什么"判断某个词在不在里面"用 `set` 比 `list` 快。

---

### 3.5 控制流：早返回是主流写法

读 `bot.py:306-341` 的 `on_event`，注意它的结构：

```python
if event.get("post_type") != "message":
    return                                   # 不满足条件就立刻返回
text = segments_to_text(event.get("message") or [])
should, prompt = self._trigger(event, text)
session = self._session_key(event)

if should and prompt.strip() in [...]:
    ...
    return
if not should or not prompt.strip():
    return
...
```

**这是 Python 工程代码的典型风格：层层过滤 + 早返回**，而不是把整个函数套进
一个巨大的 `if` 里。好处是缩进浅、每个条件独立可读。

**`any()` + 生成器**（判断"有没有任意一个满足"）：

```python
# bot.py:286-289
mentioned = any(
    seg.get("type") == "at" and str((seg.get("data") or {}).get("qq", "")) == self_id
    for seg in event.get("message", [])
)
```

**`enumerate` 从 1 开始计数**（`web_tools.py:98`）：`enumerate(news, start=1)`。

**`for` 循环里的 `continue`**（`bot.py:218`、`227`、`235`）：跳过本轮，继续下一个。

**练习**：读懂 `bot.py:216` 这一行，它把两个列表合并后按关键词长度**从长到短**排序：

```python
candidates = [(w, True) for w in self.words] + [(w, False) for w in self.aliases]
for word, strict in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
```

`key=lambda item: len(item[0])` = "用每个元素的第 0 项的长度作为排序依据"。
想想为什么要**从长到短**（提示：`人格切换成小皮` 里同时有 `人格` 和 `切换成`）。

---

### 3.6 函数：默认参数、`**kwargs`、返回多值

**返回多个值**实际是返回元组，接收时解包：

```python
# bot.py:278 定义
def _trigger(self, event: dict[str, Any], text: str) -> tuple[bool, str]:

# bot.py:311 使用
should, prompt = self._trigger(event, text)
```

**默认参数**：

```python
# web_tools.py:165
async def chat_with_tools(client, cfg, messages, max_rounds: int = 2) -> str:
```

> ⚠️ **千万不要用可变对象当默认值**：`def f(items=[])` 是经典陷阱，列表会在多次调用间共享。
> 正确写法见 `bot.py:85`：`field(default_factory=lambda: dict(DEFAULTS))`，
> 每次调用生成新字典。

**`**kwargs` 收集任意关键字参数**（`official_bot.py:189-190`）：

```python
def __init__(self, responder: Responder, **kwargs: Any) -> None:
    super().__init__(**kwargs)     # 再原样转发给父类
```

`*args` / `**kwargs` 在**写包装器和中间层**时用得最多——你不关心具体参数，只想透传。

**`lambda`：一次性小函数**（`bot.py:216`、`bot.py:85`）。

**练习**：给 `web_tools.get_daily_news`（`web_tools.py:91`）加一个
`limit: int = 8` 参数，替换掉写死的 `[:8]`。

---

### 3.7 异常处理：吞与不吞

**必须吞的地方**——失败不能影响主流程：

```python
# bot.py:197-200  落盘人设失败，不影响本次切换
try:
    self.state_path.write_text(key + "\n", "utf-8")
except OSError:
    pass
```

```python
# web_tools.py:155-158  工具挂了，返回一句可读文本让模型继续作答
try:
    return await handler(client, args)
except Exception as error:
    return f"工具 {name} 调用失败：{type(error).__name__}"
```

**绝不能吞的地方**——worker 主循环（`bot.py:368-380`）：

```python
while True:
    job = await self.queue.get()
    try:
        await self.handle(job)
    except asyncio.CancelledError:
        raise                                    # ← 关键：取消信号必须继续往上抛
    except Exception:
        LOG.exception("handle failed")           # ← 记日志
        await self.send(job["ws"], job["event"], str(self.cfg.error_text))
    finally:
        self.queue.task_done()                   # ← 无论成败都要报告任务完成
```

**这段是异常处理的最佳教材**，三个要点：

1. `except asyncio.CancelledError: raise` —— `CancelledError` 在 Python 3.8+ 继承自
   `BaseException` 而非 `Exception`，本可以不写这行。但显式写出来**表明"我知道我在做什么"**：
   程序被要求停止时必须真的停下。
2. `LOG.exception(...)` 会**自动附带堆栈**，比 `print` 强得多。
3. `finally` 保证 `task_done()` 一定被调用——否则队列的 `join()` 会永远等下去。

**`raise ... from exc`**（`bot.py:91`）：保留原始异常链，调试时能看到"因为 KeyError，
所以抛了 AttributeError"。

**异常类型要具体**：本仓库出现过 `OSError`、`KeyError`、`json.JSONDecodeError`、
`urllib.error`、`Exception`。**越具体越好**，`except Exception` 只应该出现在
"必须继续运行"的边界上（如上面的 worker 和工具派发）。

**练习**：`bot.py:414-417` 处理了 JSON 解析失败。试着把它改成同时打印出错的原始内容长度，
方便排障。

---

### 3.8 类：`__init__`、`self`、继承、`@property`

**最小结构**（`bot.py:239-249`）：

```python
class Bot:
    """反向 WS 服务端 + 单并发推理队列 + 每会话历史。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg                                  # 实例属性
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
```

**`self` 就是"这个对象本身"**，等价于其他语言的 `this`，但必须显式写在第一个参数位置。
`self.cfg = cfg` 是"把参数存到对象上"。

**继承与 `super()`**（`official_bot.py:186-196`）：

```python
class QQBotClient(botpy.Client):
    def __init__(self, responder: Responder, **kwargs: Any) -> None:
        super().__init__(**kwargs)      # 先让父类完成初始化
        self.responder = responder      # 再加自己的

    async def on_ready(self) -> None:
        LOG.info("[qq] 机器人已上线: %s intents=%s", self.robot.name, self.intents)
```

这里 `on_ready` **覆盖（override）**了父类的方法——`botpy` 在连接建立后会调用它。
整个官方机器人就是靠"重写几个 `on_xxx` 方法"实现的，这是**框架的典型设计**。

**`@property`：把方法当属性用**（`official_bot.py:103-113`）：

```python
@property
def client(self) -> httpx.AsyncClient:
    """懒创建：连接池必须绑定在真正跑它的那个事件循环上。"""
    if self._client is None:
        self._client = httpx.AsyncClient(timeout=...)
    return self._client

@property
def overloaded(self) -> bool:
    return self._waiting >= int(self.cfg.queue_limit)
```

用的时候写 `self.client`（不带括号）、`self.model.overloaded`（不带括号），
但每次访问都会执行函数体。这实现了**懒加载**（第一次用才创建）和**只读计算属性**。

**私有属性的约定**：`self._client`、`self._waiting` 前面的下划线表示"内部使用，
外部别碰"。Python **不会真的阻止**你访问，这是纯约定。

**`@classmethod`：不用实例就能调用的方法**（`bot.py:93-99`）：

```python
@classmethod
def load(cls, path: Path) -> "Config":
    merged = dict(DEFAULTS)
    if path.exists():
        merged.update(json.loads(path.read_text("utf-8")))
    return cls(merged)
```

`cls` 是类本身。调用方式是 `Config.load(Path("config.json"))`——
**这是"工厂方法"模式**，比 `__init__` 更适合"从文件构造对象"这种场景。

**练习**：给 `Bot` 类加一个 `def stats(self) -> str` 方法，返回当前队列长度和历史会话数。

---

### 3.9 `@dataclass`：少写样板代码

`bot.py:81-91` 整个类只有三行有效代码：

```python
@dataclass
class Config:
    """运行配置，字段含义见 config.example.json。"""

    values: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
```

对比一下不用 `dataclass` 要写的：

```python
class Config:
    def __init__(self, values=None):
        self.values = dict(DEFAULTS) if values is None else values
    def __eq__(self, other): ...     # 还要手写比较
    def __repr__(self): ...          # 还要手写打印
```

`@dataclass` 自动生成 `__init__`、`__repr__`、`__eq__`。**规则**：类里只写
`字段名: 类型`，需要默认值时用 `= 值`。

**`field(default_factory=...)` 专治可变默认值**：直接写 `values: dict = DEFAULTS` 会让
所有实例共享同一个字典（改一个全变），`default_factory` 每次都新建。

**练习**：把 `Config` 改成真正的字段化写法（`model: str = "..."` 一个个列出来），
体会一下"字典配置"和"字段配置"各自的优缺点。这是第 8 节练习 8。

---

### 3.10 文件与路径：`pathlib` 优于字符串拼接

```python
# bot.py:41
HERE = Path(__file__).resolve().parent        # 本文件所在目录

# bot.py:97-98  读
merged.update(json.loads(path.read_text("utf-8")))
# bot.py:171-172  存在性判断 + 读
if self.state_path.exists():
    saved = self.state_path.read_text("utf-8").strip()
# bot.py:198  写
self.state_path.write_text(key + "\n", "utf-8")
```

**为什么用 `pathlib`**：`HERE / "persona.state"`（`bot.py:244`）中的 `/` 运算符会
自动处理路径分隔符，Windows 和 Linux 都对。字符串拼接 `HERE + "/" + "x"` 在
Windows 上会变成 `\` 混用。

**`__file__`** 是当前文件的路径，`Path(__file__).resolve().parent` 是**绝对路径的父目录**。
用它拼路径，脚本在任何地方运行都不会找错文件——这是**让脚本可被任意调用的关键**。

**编码一定要写** `"utf-8"`。本项目的配置文件里有大量中文，不写编码在 Windows 上会乱码。

---

## 4. 进阶篇

### 4.1 闭包与回调 ★

**这是本项目最值得学的模式**，在 `official_bot.py:198-206`：

```python
async def on_group_at_message_create(self, message: GroupMessage) -> None:
    LOG.info("[qq] 群消息 group=%s user=%s", message.group_openid, message.author.member_openid)

    async def sender(content: str, seq: int) -> None:
        await message.reply(msg_type=0, content=content, msg_seq=seq)   # ← 捕获了 message

    await self.responder.answer(f"group:{message.group_openid}", message.content or "", sender)
```

**发生了什么**：

1. `sender` 是定义在函数**内部**的函数（嵌套函数）。
2. 它没有 `message` 参数，但用了 `message` —— 这叫**闭包**：内层函数"记住"了
   外层函数的变量。
3. 外层把 `sender` 这个**函数本身**当参数传给 `Responder.answer`。

**为什么要这么绕？** 看 `Responder.answer`（`official_bot.py:162`）：

```python
async def answer(self, session: str, prompt: str, sender: Sender) -> None:
    ...
    for seq, part in enumerate(split_message(text), start=1):
        await sender(part, seq)          # ← 只管调用，不关心发到哪个群
```

`Responder` 负责"**生成什么内容**"，`sender` 负责"**发到哪里**"。
这样 `Responder` 就**完全不需要知道 botpy 的存在**，可以脱离 QQ 单独测试
（`main()` 里 `--selftest` 就是这么干的）。

这就是**依赖注入 + 回调**：把"变化的部品"作为参数传进去。

**类型别名让签名可读**（`official_bot.py:151`）：

```python
Sender = Callable[[str, int], Awaitable[Any]]
```

意思是"接收 `(str, int)` 两个参数、返回一个 await 得到的东西的函数"。
写成别名后，`sender: Sender` 比 `sender: Callable[[str, int], Awaitable[Any]]` 清爽得多。

**练习**：给 `Responder.answer` 传一个"只打印不发送"的 `sender`，用来做离线测试。

---

### 4.2 `async` / `await` 是什么

一句话：**`async def` 定义的函数不会立刻执行，调用它只是创建了一个"协程"；
`await` 才是"真的去跑，并在这里等它完成"**。

```python
# web_tools.py:84-88
async def _get_json(client: Any, url: str) -> Any:
    response = await client.get(url, timeout=FETCH_TIMEOUT, headers={...})
    response.raise_for_status()
    return response.json()
```

两个关键点：

1. **`await` 只能在 `async def` 里用**。普通函数里写 `await` 直接语法错误。
2. **`await` 的时候，事件循环可以去干别的**。所以一个 `asyncio` 程序能同时处理
   "等网络响应"和"处理新消息"——这就是本项目用它的原因：**机器人要一边等模型生成，
   一边接收新的 QQ 消息**。

**初学者最容易犯的错**：忘了写 `await`：

```python
result = client.get(url)        # ❌ 拿到的是一个协程对象，不是响应
result = await client.get(url)  # ✅
```

如果打印出来是 `<coroutine object ...>`，就是忘写 `await` 了。

---

### 4.3 `asyncio` 三件套

**① `asyncio.run()` —— 启动事件循环**（`bot.py:467`、`471`）：

```python
return asyncio.run(selftest(cfg))
```

整个程序**只应该调用一次**，它是"从同步世界进入异步世界"的入口。

**② `asyncio.create_task()` —— 让一个协程后台跑**（`bot.py:426`）：

```python
self.worker_task = asyncio.create_task(self.worker())
async with ws_serve(self.handler, ...):
    LOG.info("listening ws://%s:%s ...")
    await asyncio.Future()          # ← 永不完成的等待，让 run() 一直不退出
```

注意最后那行：**`await asyncio.Future()` 永远不会有结果**，所以 `run()` 会永远挂在这里，
直到进程被杀掉。这是"保持服务常驻"的极简写法。

**③ 并发控制**：

| 工具 | 作用 | 出处 |
|---|---|---|
| `asyncio.Lock` | 同一时刻只允许一个任务进入 | `official_bot.py:99`、`129` |
| `asyncio.Queue` | 生产者-消费者队列 | `bot.py:245` |
| `asyncio.Queue.task_done()` | 标记一个任务处理完毕 | `bot.py:380` |

**为什么需要 `Lock`**（`official_bot.py:125-142`）：

```python
async def ask(self, session: str, prompt: str) -> str:
    self._waiting += 1
    try:
        async with self._lock:              # ← 拿到锁才能往下走，否则在这里排队
            ...
            reply = await self.chat(messages)
            ...
            return reply
    finally:
        self._waiting -= 1
```

因为本机 `OLLAMA_NUM_PARALLEL=1`——**模型一次只能处理一个请求**。
两个群同时来消息，不加锁就会互相插队，两个都超时。

---

### 4.4 生产者-消费者：本项目的核心并发模型 ★

`bot.py:368-380` 的 `worker` 是唯一的消费者：

```python
async def worker(self) -> None:
    """唯一的推理消费者：保证同一时刻只有一个请求打在 Ollama 上。"""
    while True:
        job = await self.queue.get()          # 队列空 → 在这里等，不占 CPU
        try:
            await self.handle(job)
        ...
        finally:
            self.queue.task_done()
```

生产者是 `on_event`（`bot.py:341`）：

```python
await self.queue.put({"ws": ws, "event": event, "session": session, "prompt": prompt})
```

**完整流程**：

```
QQ 消息 → on_event（判断该不该回、冷却、队列是否满）
             │  该回 → queue.put(job)
             ▼
        asyncio.Queue  ← 缓冲
             │
             ▼
        worker（唯一）→ handle() → 调模型 → send()
```

**为什么这么设计**：

- 消息可能在 1 秒内来 5 条，但模型 8 tok/s 很慢 → **队列把"接收"和"处理"解耦**；
- 只有一个 worker → **天然保证模型不被并发打爆**，不需要复杂调度；
- 队列满了就回"我这边排队有点长"（`bot.py:334-337`）→ **过载保护**，比全部超时体验好。

`asyncio.Queue` 的关键行为：`await queue.get()` 在队列空时**挂起当前协程**（不消耗 CPU），
有数据时自动唤醒。这是 `asyncio` 最实用的部分。

---

### 4.5 `async with` 与资源生命周期

```python
# bot.py:427-429
async with ws_serve(self.handler, self.cfg.host, int(self.cfg.port), ping_interval=30):
    LOG.info("listening ws://%s:%s  model=%s", self.cfg.host, self.cfg.port, self.cfg.model)
    await asyncio.Future()
```

`async with` 保证**退出时一定会清理资源**（相当于自动调用 `close()`），
即使中间抛异常也会执行。和普通 `with` 的区别是它调用的是异步的 `__aenter__`/`__aexit__`。

**`official_bot.py:103-108` 那个 `@property` 的注释揭示了一个真实坑**：

```python
@property
def client(self) -> httpx.AsyncClient:
    """懒创建：连接池必须绑定在真正跑它的那个事件循环上。"""
```

`httpx.AsyncClient` 内部的连接池会**绑定到创建它的那个事件循环**。如果在
`LocalModel.__init__` 里就创建（那时 `asyncio.run()` 还没启动），后面用起来就会报
"attached to a different loop"。所以必须**用到时才创建**（懒加载）。

**这是异步编程最常见的坑之一**，值得记住：**异步资源要在事件循环里创建**。

---

### 4.6 正则表达式

**先编译再复用**（`official_bot.py:49`）：

```python
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
...
out = URL_PATTERN.sub("(链接已省略)", out)
```

编译一次反复用，比每次调用 `re.sub(pattern, ...)` 快。`\S` = 非空白字符。

**四个高频函数**（本项目全用到了）：

| 函数 | 作用 | 出处 |
|---|---|---|
| `re.sub(pat, repl, s)` | 替换 | `official_bot.py:66`、`bot.py:223` |
| `re.findall(pat, s, re.S)` | 找出全部匹配 | `web_tools.py:128` |
| `re.search(pat, s, re.S)` | 找第一个，返回 Match 对象 | `web_tools.py:131` |
| `match.group(1)` | 取第 1 个括号捕获的内容 | `web_tools.py:135` |

**非捕获组 `(?:...)`** 用于"只想分组、不想捕获"（`bot.py:223`）：

```python
rest = re.sub(r"^(?:切换|换成|换为|变为|变成|为|成|到)+", "", rest)
```

`^` = 开头，`+` = 一个或多个。作用：把 `人格切换成小皮` 里的 `切换成` 剥掉。

**`re.S`** 让 `.` 也能匹配换行符——抓 HTML 片段时必备（`web_tools.py:128`）。

**练习**：`web_tools.py:131-132` 用正则从 Bing 结果页抓标题和摘要。写个小脚本
把抓到的原始 HTML 存下来，试着调整这两个正则。

---

### 4.7 JSON

```python
# bot.py:98  读配置
merged.update(json.loads(path.read_text("utf-8")))

# bot.py:362  发消息（注意 ensure_ascii=False）
await ws.send(json.dumps(payload, ensure_ascii=False))
```

**`ensure_ascii=False` 很重要**：默认 `True` 会把中文转义成 `\u4f60\u597d`，
消息发出去就是乱码。加上这个参数才是真正的中文。

**`json.loads` 会抛异常**，必须处理（`web_tools.py:205-209`）：

```python
if isinstance(raw, str):
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
```

模型返回的工具参数是**字符串形式的 JSON**，可能不合法 → 容错成空字典，
而不是让整条回复崩掉。

**练习**：给 `bot.py:414-417` 那段加个保险——如果收到的不是合法 JSON，
把前 100 个字符记进日志。

---

### 4.8 动态特性：`__getattr__` 与猴子补丁

**`__getattr__`**（`bot.py:87-91`）只在**正常查找失败后**才被调用，所以它能实现
"字典当对象用"。注意它**不能**用于 `__init__` 里赋值之前的属性（会无限递归）。

**猴子补丁（monkey patch）** —— 运行时给类加方法。`AUTOCHAT.md` 第 1 节里那个
6 行补丁就是典型例子：

```python
import botpy.connection as _bc
from botpy.message import GroupMessage as _GroupMessage

def _parse_group_message_create(self, payload):
    self._dispatch("group_message_create", _GroupMessage(self.api, payload.get("id"), payload.get("d", {})))

_bc.ConnectionState.parse_group_message_create = _parse_group_message_create
```

原理：`botpy/connection.py:84-88` 在 `__init__` 里用 `inspect.getmembers` 扫描
所有 `parse_*` 方法建分发表，所以**在实例化之前**给类加上方法，就会被自动注册。

**为什么能这么写**：Python 的类在运行时是可变的（不像 Java/C++）。
**什么时候该用**：修补第三方库、临时绕过限制。
**代价**：升级库后可能失效，且阅读代码时"这个方法哪来的"很难找。所以本项目把它
写在文档里而不是代码里。

---

### 4.9 装饰器：`@` 到底是什么

`@decorator` 是"把这个函数/类交给 `decorator` 处理后再赋值回原名"的语法糖。
本项目用到的全是**标准库提供的**：

| 装饰器 | 作用 | 出处 |
|---|---|---|
| `@dataclass` | 自动生成 `__init__`/`__repr__`/`__eq__` | `bot.py:81` |
| `@property` | 方法变属性 | `official_bot.py:103` |
| `@classmethod` | 类方法，第一个参数是 `cls` | `bot.py:93` |

**自己写装饰器**是下一步的练习。核心模板：

```python
import functools

def log_calls(func):
    @functools.wraps(func)                    # 保留原函数的名字和文档
    def wrapper(*args, **kwargs):
        print(f"调用 {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

@log_calls
def hello(name):
    return f"hi {name}"
```

**练习**：写一个 `@retry(times=3)` 装饰器，给 `web_tools._get_json` 加上网络失败重试。

---

### 4.10 模块导入：`official_bot.py:36-40` 那个 hack

```python
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))              # ← 把本目录加进"搜索路径"

from bot import Config, PersonaStore, clean_reply   # noqa: E402
from web_tools import chat_with_tools              # noqa: E402
```

Python 导入模块时会依次搜索 `sys.path` 里的目录。默认包含"当前工作目录"，
但**不包含"脚本所在目录"**。所以 `official_bot.py` 想 `import bot`，
要么把本目录塞进 `sys.path`（本项目的做法），要么**把它做成包**：

```
qq-bot/
├── qqbot/
│   ├── __init__.py
│   ├── bot.py
│   ├── official_bot.py
│   └── web_tools.py
└── pyproject.toml
```

然后用 `from qqbot.bot import Config`。`# noqa: E402` 是告诉代码检查工具
"我知道我在 import 之前写了代码，别报警"。

**练习**：把项目重构成包结构（这是练习 12，能真正学会 Python 的模块系统）。

---

## 5. 工程实践

### 5.1 配置分层：默认 → 文件 → 环境变量

```python
# bot.py:93-99
@classmethod
def load(cls, path: Path) -> "Config":
    merged = dict(DEFAULTS)                        # ① 内置默认
    if path.exists():
        merged.update(json.loads(path.read_text("utf-8")))   # ② 配置文件覆盖
    return cls(merged)
```

```python
# official_bot.py:235-236
cfg.values.setdefault("qq_appid", os.environ.get("QQ_APPID", ""))   # ③ 环境变量兜底
```

**为什么要分层**：默认值让程序"零配置可跑"；配置文件放用户自定义；
**环境变量放密钥**——因为配置文件和代码会被提交到 git，而环境变量不会。

### 5.2 日志：别用 `print`

```python
# bot.py:102-109
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
```

用法（`%s` 是**延迟格式化**，比 f-string 好——日志被过滤掉时不浪费计算）：

```python
LOG.info("enqueue session=%s prompt=%r qsize=%s", session, prompt[:60], self.queue.qsize())
LOG.warning("queue full (%s), rejecting session=%s", ...)
LOG.exception("handle failed")        # 自动带堆栈
```

**日志级别**：`DEBUG` < `INFO` < `WARNING` < `ERROR`。生产环境看 `INFO` 就够，
排障时再开 `DEBUG`。

**`%r`** 打印 `repr()`（带引号），比 `%s` 更容易看清字符串边界。

### 5.3 写脚本的三条规矩

看 `run.sh` 和 `svc.sh`，它们体现了运维脚本的成熟做法：

1. **`set -uo pipefail`** —— 未定义变量报错、管道中任一环失败就算失败。
2. **幂等**：`svc.sh:73` 先检查"是不是已经在跑"，在跑就提示而不是再起一个。
3. **绝对路径 + 精确匹配进程**：`svc.sh:31-43` 扫 `/proc` 并要求命令行里出现**绝对路径**。
   因为 `pkill -f 'bot.py'` 会同时命中 `official_bot.py` 和**正在执行这条命令的 shell 自己**
   —— 作者为此踩了三次坑。

### 5.4 写测试

本项目目前没有测试文件，但 `PLAN.md` 第 11 节记录过一次手工单测（5 个用例）。
**适合上测试的纯逻辑**（不依赖网络和 QQ）：

- `PersonaStore.handle_command`（`bot.py:203`）—— 输入字符串，输出"是否处理 + 回复"
- `Bot._trigger`（`bot.py:278`）—— 输入事件字典，输出"是否响应 + 提问"
- `clean_reply`（`bot.py:112`）—— 输入字符串，输出字符串
- `segments_to_text`（`bot.py:125`）
- `split_message`（`official_bot.py:70`）
- `sanitize`（`official_bot.py:58`）

**为什么这几个函数好测**：它们**没有 `self` 之外的状态**、不碰网络、不碰文件。
这种"纯函数"是单元测试的最佳目标——设计时就该往这个方向靠。

写测试的第一步（先不用框架）：

```python
# test_personas.py
from bot import Config, PersonaStore
from pathlib import Path

def test_not_a_command():
    cfg = Config({"personas": {"小皮": "你是小皮"}, "persona_words": ["人格"],
                  "persona_alias_words": ["换成"], "persona_aliases": {}})
    store = PersonaStore(cfg, Path("/tmp/does-not-exist"))
    assert store.handle_command("换成什么好呢") == (False, "")   # 日常话不该被误判
```

然后装 `pytest` 跑它：`~/.venvs/qqbot/bin/pip install pytest && pytest`。

### 5.5 安全：凭据永不入版本库

`.gitignore` 是本项目安全设计的核心：

```gitignore
qq.env            # QQ AppID / AppSecret
config.json       # OneBot token
config.json.bak
*.log             # 含真实 openid
persona.state
__pycache__/
```

**三条铁律**：

1. 密钥放**环境变量**或**被忽略的文件**，绝不写进代码；
2. 提交前跑 `git status` 确认没混进敏感文件；
3. 万一提交了，**改密码比删文件重要**——git 历史里删不干净。

---

## 6. 四周学习路线

| 周 | 目标 | 读 | 做 |
|---|---|---|---|
| **第 1 周** | 看懂程序骨架，能改文字 | 第 2 节全部 + `bot.py:456-479`、`112-139`、`81-99` | 练习 1、2、3 |
| **第 2 周** | 掌握类与数据结构 | `bot.py:239-341`、`142-236` | 练习 4、5、6 |
| **第 3 周** | 入门异步编程 | 第 4.1–4.5 节 + `bot.py:345-433`、`official_bot.py:154-228` | 练习 7、8、9 |
| **第 4 周** | 工程化 | 第 5 节 + `web_tools.py:145-220` | 练习 10、11、12 |

---

## 7. 练习清单（由易到难）

**改行为**

1. 加 `/ping` 指令：收到就回 `pong`（改 `bot.py:314` 附近）。
2. `busy_text` 改成从 3 句话里随机选一句（用 `random.choice`，参考 `bot.py:231`）。
3. 群聊冷却从固定 5 秒改成"随机 3–8 秒"。

**改逻辑**

4. 给 `clean_reply` 加一条规则：剥掉连续的多个换行。
5. 给 `get_daily_news` 加 `limit` 参数（参考 3.6 节）。
6. `trigger_prefixes` 支持正则（把 `startswith` 换成 `re.match`）。

**写新功能**

7. 加一个 `/stats` 指令，输出今日回复条数（提示：在 `handle` 里累加计数）。
8. 加"按用户区分人设"（`PERSONAS.md` 第 7 节提到过这个需求）：
   把 `PersonaStore.current` 从单个字符串改成 `dict[str, str]`。
9. 加一个新联网工具 `get_weather`，并注册到 `web_tools.py:147` 的 `handlers`。

**工程化**

10. 加 `pytest` 测试，覆盖 `handle_command` 的 5 种输入（参考 5.4 节）。
11. 装 `ruff` 和 `mypy` 跑一遍，把报的问题改掉：
    `~/.venvs/qqbot/bin/pip install ruff mypy && ruff check . && mypy bot.py`
12. 把项目重构成包结构（`qqbot/` 目录 + `pyproject.toml`），去掉 `sys.path.insert`。
13. 写一个 `@retry(times=3)` 装饰器，加给 `web_tools._get_json`。
14. 加优雅退出：收到 `Ctrl-C` 时先 `await self.client.aclose()` 再退出。
15. 把日志改成 JSON 格式，方便用 `jq` 过滤。

---

## 8. 本项目踩过的 Python 坑（真实）

| 坑 | 现象 | 正确做法 | 出处 |
|---|---|---|---|
| 可变默认参数 | 所有实例共享一个字典 | `field(default_factory=...)` | `bot.py:85` |
| 忘写 `await` | 拿到 `<coroutine object>` | `result = await client.get(...)` | `bot.py:253` |
| 异步资源在循环外创建 | `attached to a different loop` | 懒创建（`@property`） | `official_bot.py:103` |
| `json.dumps` 默认转义中文 | 消息里全是 `\u4f60` | `ensure_ascii=False` | `bot.py:362` |
| `except Exception` 吞掉取消信号 | 进程杀不掉 | `except asyncio.CancelledError: raise` | `bot.py:374` |
| `deque(maxlen)` 静默丢数据 | 历史莫名少了几轮 | 知道它会自动淘汰最旧的 | `bot.py:386` |
| 忘了 `queue.task_done()` | `queue.join()` 永远不返回 | 放在 `finally` 里 | `bot.py:380` |
| 用 `time.time()` 算耗时 | 系统时间跳变导致负数 | 用 `time.monotonic()` | `bot.py:330`、`391` |
| 字典用 `[]` 取不存在的键 | 抛 `KeyError` 让机器人崩掉 | 容错处用 `.get()` | `bot.py:130` |
| 不写文件编码 | Windows 上中文乱码 | `read_text("utf-8")` | `bot.py:98` |
| `__getattr__` 无限递归 | `RecursionError` | 只访问已存在的属性 | `bot.py:87` |

另外有个**不是 Python 但极其重要**的坑：`pkill -f 'bot.py'` 会匹配到
`official_bot.py`，**以及正在执行这条命令的 shell 自己**。作者为此自杀过三次
（`svc.sh:13-17` 有完整注释）。用 `svc.sh` 就对了。

---

## 9. 术语表

| 英文 | 中文 | 一句话解释 |
|---|---|---|
| `coroutine` | 协程 | `async def` 定义的函数，调用后返回的对象 |
| `event loop` | 事件循环 | 异步程序的调度中心，`asyncio.run()` 启动 |
| `await` | 等待 | 在此处让出控制权，等结果回来 |
| `closure` | 闭包 | 内层函数记住了外层函数的变量 |
| `decorator` | 装饰器 | `@xxx`，把函数交给另一个函数加工 |
| `generator` | 生成器 | 用 `yield` 逐个产出值的函数 |
| `comprehension` | 推导式 | `[x for x in y if z]` 一行生成容器 |
| `dataclass` | 数据类 | 自动生成 `__init__` 等方法的类 |
| `dunder` | 双下划线方法 | `__init__`、`__getattr__` 这类特殊方法 |
| `monkey patch` | 猴子补丁 | 运行时修改类/模块 |
| `producer-consumer` | 生产者-消费者 | 一边塞队列、一边取队列的并发模式 |
| `type hint` | 类型注解 | `x: int`，给人看的，运行时不强制 |

---

## 10. 推荐资源

- **官方教程**（中文，质量最高）：https://docs.python.org/zh-cn/3/tutorial/
- **`asyncio` 官方文档**：https://docs.python.org/zh-cn/3/library/asyncio.html
- **Real Python**（英文，例子多）：https://realpython.com/
- **PEP 8 代码风格**：https://peps.python.org/pep-0008/

**学 Python 最有效的方式是"改一个能跑的项目然后看它坏掉"** ——
准备好 `bash svc.sh log official` 开着，然后大胆改。改坏了 `git checkout .` 就能还原。
