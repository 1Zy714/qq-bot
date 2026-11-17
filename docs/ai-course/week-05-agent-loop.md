# Week 5 · Agent 循环与核验

> 目标：读懂「观察 → 决策 → 动作 → 复核」的循环，并给它加一条预算规则。
> 相关：[../GAME-BACKEND.md](../GAME-BACKEND.md)、`game_loop.py`。

---

## Day 1 · 读主循环

```bash
sed -n '193,241p' game_loop.py     # _tick
sed -n '242,270p' game_loop.py     # _budget_reason
```

**要能回答**：一轮循环里，先检查什么、再观察什么、什么情况下才调模型？

---

## Day 2 · 读动作与核验

```bash
sed -n '272,307p' game_loop.py     # _do_tile_action：移动 / 多击 / 斜对角
sed -n '308,330p' game_loop.py     # _verify_tile：不信自报
```

**要能回答**：
- 为什么斜对角不能直接动手？
- 「上报成功但现场没变化」会怎么处理？

---

## Day 3 · 读懂预算

`_budget_reason` 现在检查三样：动作次数、模型调用次数、运行分钟数。超限时 `_tick` 会收工并汇报。

**任务**：把「模型调用」从「总次数」改成「每小时次数」。

**提示**：

```python
from collections import deque          # 文件顶部
# __init__ 里
self.model_stamps: deque[float] = deque(maxlen=500)
self.max_model_per_hour = int(budget.get("max_model_per_hour", 12))
# 每次调模型前（_decide_strategy 里 self.model_calls += 1 附近）
self.model_stamps.append(time.monotonic())
# _budget_reason 里
recent = sum(1 for t in self.model_stamps if time.monotonic() - t < 3600)
if recent >= self.max_model_per_hour:
    return f"每小时模型调用 {recent}/{self.max_model_per_hour}"
```

同时在 `config.example.json` 的 `game_loop.budget` 里补 `"max_model_per_hour": 12`。

---

## Day 4 · 测试

游戏没开也能测——用离线单测（参考 `test_game_backend.py` 的 FakeModel 写法）：

```python
loop.max_model_per_hour = 2
loop.model_stamps.extend([time.monotonic(), time.monotonic()])
check("每小时上限生效", loop._budget_reason().startswith("每小时模型调用"), True)
```

跑：

```bash
python3 test_game_backend.py
```

---

## Day 5 · 验证与记录

游戏开着时重启机器人，观察日志：触发收工时是否出现新的原因文案。
游戏没开就只验证单测，并在 `experiments/week-05.md` 写清「为什么没法做在线验证」。

---

## 验收清单

- [ ] 能画出 `_tick` 的流程图
- [ ] 能解释核验与失败记忆
- [ ] 新的每小时预算生效（单测或日志）
- [ ] 提交：`git commit -m "course: week-05 每小时预算"`

## 常见坑

| 现象 | 原因 |
|---|---|
| 一启动就收工 | 预算读错（字符串/整型），或 stamps 初始就有值 |
| 游戏不动 | 游戏没开 / 门控跳过 / 窗口失焦（世界暂停） |
| 动作一直失败 | 目标不可达；先看 `surroundings` 的 `passable` |
