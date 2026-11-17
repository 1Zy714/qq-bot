# Week 2 · 提示词与上下文

> 目标：亲手验证「system 稳定才快」，理解项目为什么把动态内容放 user。
> 相关：[../CHAT-PERF.md](../CHAT-PERF.md)、[../LEARN-AI-BASICS.md](../LEARN-AI-BASICS.md) 第 2–4 节。

---

## Day 1 · 找到 system 是怎么拼的

```bash
grep -n 'def system_context' -A 12 official_bot.py
grep -n 'system_suffix' official_bot.py | head
```

**要能回答**：system 后缀包含哪几块？（表情包提示、人物档案摘要、成长附录）

---

## Day 2 · 理解「为什么要稳定」

读 `user_memory.py` 的 `digest`：

```bash
grep -n 'def digest' -A 22 user_memory.py
```

**要能回答**：
- 它为什么按 uid 排序？
- 为什么不放「最近发言数」这类会变的字段？

（答案在上面的注释里：system 逐字稳定 → llama.cpp 前缀缓存命中 → 变慢就白做。）

---

## Day 3 · 实验 A：往 system 里塞时间戳

**改**（临时，做完改回来）：在 `Responder.system_context()` 末尾加一行：

```python
suffix += f"\n\n【当前时间】{time.strftime('%Y-%m-%d %H:%M:%S')}"
```

**跑**：重启机器人，私聊发同样一句话 3 次，从日志里抄「用时」：

```bash
bash svc.sh restart official
tail -f logs/official.log | grep '用时'
```

**记录**：3 次用时（秒）。

---

## Day 4 · 实验 B：把动态内容挪到 user

**改**：删掉 Day 3 加的时间戳，改在 `answer()` 里把时间拼进 **prompt**（user 消息）：

```python
prompt = f"{prompt}\n（现在 {time.strftime('%H:%M')}）"
```

**跑**：同样发 3 次，记录用时。

**对比结论**：A 与 B 的平均用时差多少？为什么？

---

## Day 5 · 人设实验（可选但推荐）

```bash
ls roles/          # 看人设文件
# 改一句话，重启，发同样的话看语气变化
```

---

## 本周产出

`docs/ai-course/experiments/week-02.md`：

| 实验 | 平均用时 | 观察 |
|---|---|---|
| A（system 加时间戳） | | |
| B（时间进 user） | | |

结论 3 行：为什么 A 更慢、项目为什么选 B。

## 验收清单

- [ ] 能画出 system 后缀的组成
- [ ] 有 A/B 两组数据与结论
- [ ] 代码已改回原样（`git diff` 应为空）

## 常见坑

| 现象 | 原因 |
|---|---|
| 改完没效果 | 没重启，或改的是另一个 profile |
| 用时波动大 | 模型首次加载/显存紧张；多跑几次取平均 |
| 忘了改回 | 用 `git checkout -- official_bot.py` 恢复 |
