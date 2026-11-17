# Week 4 · 记忆与检索（RAG-lite）

> 目标：理解「不用向量库也能做检索」，并给记忆加一个新的数据来源。
> 相关：[../MEMORY-SEARCH.md](../MEMORY-SEARCH.md)、`memory_search.py`、`user_memory.py`。

---

## Day 1 · 读检索实现

```bash
sed -n '197,222p' memory_search.py    # search：关键词 + 时间衰减
sed -n '223,246p' memory_search.py    # digest：渲染证据块
sed -n '81,96p'  memory_search.py     # _tokens：中文二元组 + 英文整词
```

**要能回答**：
- 为什么中文用二元组而不是分词器？
- 30 天半衰期怎么体现？（`math.pow(0.5, age_days / half_life)`）

---

## Day 2 · 读写入合并

```bash
sed -n '342,364p' user_memory.py      # consolidate：存量合并
sed -n '249,300p' user_memory.py      # _merge_note：四条合并规则
```

**要能回答**：四条规则分别是什么？（精确重复 / 事实键 / 子串 / 共同前缀）

---

## Day 3 · 动手：加一个来源

在 `memory_search.py` 里加：

```python
    def _load_notes(self) -> list[Evidence]:
        folder = self.root.parent / "notes"      # 仓库根下的 notes/（若有）
        if not folder.exists():
            return []
        out = []
        for path in sorted(folder.glob("*.md"))[:10]:
            for line in path.read_text("utf-8", "replace").splitlines():
                line = line.strip()
                if len(line) >= 8 and not line.startswith("#"):
                    out.append(Evidence("笔记", path.stem, _clip(line), ts=path.stat().st_mtime))
        return out
```

并在 `all_items()` 里加 `+ self._source("notes", self._load_notes)`。

> 如果仓库没有 `notes/`，自己建一个放两篇测试 md。

---

## Day 4 · 补测试

在 `test_memory_search.py` 的 `build()` 里造一个 `notes/` 目录与文件，然后加断言：

```python
hits = engine.search("关键词")
check("笔记来源可检索", any(h.source == "笔记" for h in hits), True)
```

跑：

```bash
python3 test_memory_search.py
```

---

## Day 5 · 对比与结论

在 `experiments/week-04.md` 写 5 行：

1. 关键词检索适合什么、不适合什么？
2. 什么情况下才值得上向量库？
3. 时间衰减对「老记忆」的影响是否符合直觉？

---

## 验收清单

- [ ] `notes/*.md` 能被 `[回忆:…]` 检索到
- [ ] 单测新增用例通过
- [ ] 有一页对比结论
- [ ] 提交：`git commit -m "course: week-04 记忆来源扩展"`

## 常见坑

| 现象 | 原因 |
|---|---|
| 检索不到 | `_source` 的 cache key/mtime 没更新；重启或换 key |
| 全是噪声 | 每行都收太长；限制长度与来源篇数 |
| 中文命中差 | 关键词太短（<2 字）；补二元组或同义词 |
