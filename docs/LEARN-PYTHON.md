# Python 入门：用这个项目当教材

> 目标：**能看懂、能改、能加功能**。不追求学全，只学这个项目用到的。
> 每节都有「本项目里的真实例子」，括号里是文件位置。

---

## 0. 先跑通（5 分钟）

```bash
cd ~/qq-bot
~/.venvs/qqbot/bin/python -V          # 应该是 Python 3.12+
~/.venvs/qqbot/bin/python test_user_memory.py
~/.venvs/qqbot/bin/python test_memory_search.py
```

能跑出「✓ 全部通过」就说明环境没问题。改代码后也用这两条快速自检。

---

## 1. 变量、类型、注释

```python
name = "official_bot"     # str
turns = 4                 # int
timeout = 90.0            # float
enabled = True            # bool
```

- 不需要声明类型，名字 = 值 就行。
- 注释用 `#`，三引号 `"""..."""` 是文档字符串（函数的说明书）。
- 类型注解只是**提示**，不强制：`def ask(session: str, prompt: str) -> str:`。

例子：`user_memory.py` 顶部 `MAX_NOTE_CHARS = 200` 这种全大写是「常量约定」，表示别改。

---

## 2. 字符串：这个项目用得最多

```python
text = f"[群友 {name}] {prompt}"       # f-string 插值
clean = text.strip()                    # 去首尾空白
body = " ".join(parts)                  # 拼接
if "回忆" in text: ...                  # 子串判断
slice_ = text[:120] + "…"               # 切片
```

清洗用户输入（`memory_search.py` 的 `_clip`）：

```python
import re
text = re.sub(r"\s+", " ", str(text or "")).strip()
```

> 中文按「字」算长度，`len("杭州")` 是 2。

---

## 3. 列表、字典、集合

```python
notes = ["爱喝茶", "在杭州"]        # list：有序、可重复
profile = {"name": "e", "notes": notes}   # dict：键值对
tags = {"搞笑", "无语"}                  # set：去重
```

本项目：`memory/users.json` 的结构就是「字典套字典」：

```json
{"users": {"<openid>": {"name": "e", "notes": ["爱喝茶"]}}}
```

遍历（`user_memory.py` 的 `consolidate`）：

```python
for uid, profile in self.users.items():
    for note in profile.notes:
        ...
```

列表推导式（一行生成新列表）：

```python
names = [p.name for p in self.users.values() if p.name]
```

---

## 4. 条件与循环

```python
if not notes:
    return 0
elif len(notes) > 10:
    notes = notes[-10:]      # 只留最后 10 条
else:
    ...
```

`for ... range(...)`、`for i, item in enumerate(items)`、`while True:` 都在项目里出现。

例子：`game_loop.py` 的 `_build_candidates` 遍历格子、按优先级排序：

```python
out.sort(key=lambda c: (-c["priority"], c["dist"]))
```

> `lambda` 就是「临时小函数」，这里表示先按 priority 降序、再按距离升序。

---

## 5. 函数：默认参数、返回多个值

```python
def parse_recall(text: str) -> tuple[str, str]:
    """返回 (去掉标记的正文, 关键词)。"""
    ...
    return cleaned, query      # 返回元组
```

调用：

```python
prompt, query = parse_recall(prompt)
```

**默认参数**（`cross_session.py`）：

```python
def record(self, session, uid, name, text, msg_id="", now=None):
```

> 坑：默认值别用可变对象（`def f(x=[])`），会被所有调用共享。

---

## 6. dataclass：少写样板代码

```python
from dataclasses import dataclass

@dataclass
class Profile:
    uid: str
    name: str = ""
    notes: list[str] = field(default_factory=list)   # 可变默认值要这样写
```

自动生成 `__init__`、`__repr__`，非常适合作「数据袋子」。项目里 `Profile`、`Evidence`、`Record` 都是这样。

---

## 7. 异常处理：出错时别把主流程弄崩

```python
try:
    data = json.loads(raw)
except ValueError:
    return []          # 文件坏了就当空
```

原则（项目里到处体现）：

- **能降级就降级**：OCR/记忆检索失败 → 返回空，不影响回复
- **该记日志就记**：`except Exception: LOG.exception(...)`
- 不要 `except: pass` 吞掉一切

---

## 8. 文件与 JSON（pathlib 更现代）

```python
from pathlib import Path
import json

path = Path(__file__).resolve().parent / "memory" / "users.json"
data = json.loads(path.read_text("utf-8"))
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
```

- `ensure_ascii=False`：中文不转成 `\uXXXX`
- `Path(__file__).parent`：以脚本位置为基准，不依赖你从哪运行

---

## 9. async / await：为什么机器人能同时干几件事

```python
async def answer(...):
    reply = await self.model.ask(...)   # 等模型时让出 CPU
```

要调用异步函数，必须在 `async def` 里 `await`；入口用：

```python
asyncio.run(main())
```

**坑**：同步的阻塞调用（比如 SDK、`time.sleep`）放进异步函数会卡住整个循环。
项目里的处理（`dsh_agent.py`）：

```python
reply = await asyncio.to_thread(self._run, session, text)   # 丢到线程里跑
```

---

## 10. 模块与导入

```python
from user_memory import UserMemory, Speaker     # 同目录模块
import json                                     # 标准库
from deepseek_harness import DeepSeekHarness    # 第三方包（在 venv 里）
```

脚本直接跑时，项目常加：

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
```

这样 `import xxx` 才能找到同目录文件。

---

## 11. 日志：比 print 靠谱

```python
import logging
LOG = logging.getLogger("qq")
LOG.info("模型返回空正文 session=%s", session)   # 用占位符，别 f-string 拼
```

项目用 botpy 的 logger（`LOG = botpy_logging.get_logger()`），日志进 `logs/official.log`。

---

## 12. 测试：这个项目不用 pytest

每个 `test_*.py` 都是一个可执行脚本，自带 `check()`：

```python
FAILURES = []
def check(name, actual, expected):
    if actual == expected:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)

def main():
    ...
    return 1 if FAILURES else 0

if __name__ == "__main__":
    raise SystemExit(main())
```

好处：零依赖、能直接跑；坏处：没有 fixture。够用就行。

---

## 13. 练习：给机器人加一条命令

目标：在私聊里发「提醒」，机器人回一句固定话，不走模型。

步骤：

1. 打开 `official_bot.py`，找到 `Responder` 里的 `growth_command`（现成的「严格前缀命令」例子）。
2. 照着写：

```python
def remind_command(self, text: str) -> tuple[bool, str]:
    if text.strip() in ("提醒", "提醒一下"):
        return True, "该喝水啦，主人。"
    return False, ""
```

3. 在 `answer()` 里、`growth_command` 调用之后，照抄三行调用它。
4. 重启机器人：`bash svc.sh restart official`，在 QQ 里发「提醒」验证。

> 这个练习覆盖：函数、字符串判断、分支、返回值元组、在现有流程里插桩——正好是日常改需求的最小闭环。

---

## 14. 常见坑清单

| 坑 | 现象 | 解法 |
|---|---|---|
| 缩进混用 Tab/空格 | `IndentationError` | 统一 4 空格 |
| 可变默认参数 | 多次调用共享一个列表 | `field(default_factory=list)` |
| 忘写 `await` | 得到 `<coroutine ...>` 而不是结果 | 加 `await`，或确认是 async 函数 |
| 中文文件没写 encoding | `UnicodeDecodeError` | 读写都写 `encoding="utf-8"` |
| 整数除法 | `5/2 == 2.5`，想要 2 用 `//` | `5 // 2` |
| 修改列表时遍历 | 行为诡异 | 先复制 `list(notes)` |
| 脚本导入同目录文件失败 | `ModuleNotFoundError` | `sys.path.insert` 或从项目根运行 |

---

## 15. 下一步读什么

- 想把本项目的代码逐段读懂 → [LEARNING.md](LEARNING.md)（1109 行，按知识点映射到行号）
- 想搞懂机器人整体架构 → [README.md](../README.md) + [DSH-BRIDGE.md](DSH-BRIDGE.md)
- 想学 Linux 操作 → [LEARN-LINUX.md](LEARN-LINUX.md)
