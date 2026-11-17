# Week 6 · 评估与可观测

> 目标：不靠感觉判断「变好没有」，用日志算出延迟与错误率，并写一份小报告。
> 相关：[../CHAT-PERF.md](../CHAT-PERF.md)、[../OPS.md](../OPS.md)。

---

## Day 1 · 从日志里拿数据

```bash
grep -oE '用时 [0-9.]+s' logs/official.log | tail -20
grep -c 'ERROR' logs/official.log
```

**要能回答**：最近 20 次回复里，最快/最慢各是多少？

---

## Day 2 · 写 `tools/stats.py`

要求：纯标准库，一条命令输出：

```
回复数: 20  平均: 5.2s  中位: 4.8s  最大: 12.1s
错误: 3 次（超时 2 / 空正文 1）
```

骨架：

```python
#!/usr/bin/env python3
import re, statistics, sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "official.log"

def main() -> int:
    text = LOG.read_text("utf-8", "replace") if LOG.exists() else ""
    times = [float(x) for x in re.findall(r"用时 ([0-9.]+)s", text)]
    if not times:
        print("没有找到回复记录")
        return 1
    errors = len(re.findall(r"ERROR|错误", text))
    print(f"回复数: {len(times)}  平均: {statistics.mean(times):.1f}s  "
          f"中位: {statistics.median(times):.1f}s  最大: {max(times):.1f}s")
    print(f"错误: {errors} 次")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

---

## Day 3 · 分错误类型

在日志里统计三类（可用 `grep -c`）：

| 类型 | 关键词 |
|---|---|
| 超时/连不上 | `ConnectError` / `Timeout` |
| 空正文 | `空正文` |
| 工具失败 | `跳过挂载` / `调用失败` |

把数字补进报告。

---

## Day 4 · 对照优化前后

如果做过第 2 周实验，对比「system 加时间戳」前后的平均耗时；解释差异。

---

## Day 5 · 写一页报告

`docs/ai-course/experiments/week-06-report.md`，结构：

```
1. 数据窗口（日志范围）
2. 延迟：平均 / 中位 / 最大
3. 错误分布
4. 结论：瓶颈在哪
5. 建议：下一步优化什么
```

---

## 验收清单

- [ ] `python3 tools/stats.py` 能输出统计
- [ ] 有一页报告，含结论与建议
- [ ] 提交：`git commit -m "course: week-06 统计脚本与报告"`

## 常见坑

| 现象 | 原因 |
|---|---|
| 统计为 0 | 日志路径不对 / 日志被轮转（看 `logs/`） |
| 平均被极值拉高 | 同时给中位数；说明长尾原因 |
| 错误计数偏高 | `ERROR` 也出现在堆栈里，按类型再细分 |
