# Week 1 · 模型与推理环境

> 目标：搞清楚「本地模型是怎么被调用的、成本来自哪里」，能独立验证模型服务是否就绪。
> 相关：[../LEARN-AI-BASICS.md](../LEARN-AI-BASICS.md) 第 1、5 节。

---

## 前置

```bash
cd ~/qq-bot
curl -s http://127.0.0.1:8080/v1/models | head -c 300    # 本地模型（Strata）
bash svc.sh status
```

如果 8080 不通：先看 [../OPS.md](../OPS.md)，模型没起时后面的实验都没意义。

---

## Day 1 · 认识调用链

```bash
grep -n 'provider\|model\|baseURL' dsh-qq.patch.yml | head
sed -n '86,110p' dsh_agent.py        # _harness_for：provider/model 怎么传
```

**要能回答**：
- 机器人用的 provider 叫什么？（`local-qwen`）
- 它指向哪个地址？（`http://127.0.0.1:8080/v1`）
- 模型 id 是什么？（`qwen3.8-flash-next-iq2_xs`）

---

## Day 2 · 看模型服务本身

```bash
curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool | head -20
```

**要能回答**：模型名、上下文长度（`n_ctx`）、是否已加载（`status.value`）。

> 本机 Strata 跑在 Windows 侧、WSL 通过 mirrored 网络访问 `127.0.0.1:8080`；
> 换机器时地址可能不同，以 `dsh-qq.patch.yml` 里的 `baseURL` 为准。

---

## Day 3 · 第一次手搓请求

```bash
time curl -s -m 60 http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.8-flash-next-iq2_xs","messages":[{"role":"user","content":"用一句话介绍你自己"}],"max_tokens":64}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])"
```

**要能回答**：`time` 里的 real/user/sys 分别是什么？为什么 real 远大于 user？

---

## Day 4 · max_tokens 实验（本周产出）

跑 3 组，记录到 `docs/ai-course/experiments/week-01.md`：

| 组 | max_tokens | 耗时(s) | 回复字数 | 是否被截断 |
|---|---|---|---|---|
| A | 16 | | | |
| B | 64 | | | |
| C | 256 | | | |

用同一条 prompt（如「介绍一下星露谷这个游戏」），每组跑 2 次取平均。

**结论要写出**：耗时随输出长度增长的关系；被截断的表现（没有句号/半句话）。

---

## Day 5 · 写 5 行笔记

在 `experiments/week-01.md` 里回答：

1. 本地推理的成本主要来自哪三样？
2. 为什么「模型越大越好」在 16GB 显存上不成立？
3. 这次实验里你观察到的最大延迟是多少？出现在什么情况？

---

## 验收清单

- [ ] 能说出 provider / baseURL / model 三个值
- [ ] 能不发文档、手搓一次 `curl` 请求拿到回复
- [ ] 有 3 组 max_tokens 数据与结论
- [ ] 提交：`git add -A && git commit -m "course: week-01"`

## 常见坑

| 现象 | 原因 |
|---|---|
| `curl` 一直转圈 | 模型没加载完（首次加载可能 10~60s） |
| 返回 `content: null` | max_tokens 太小，被思考过程吃光；调大再试 |
| 8080 拒绝连接 | 模型服务没起 / 端口不对（看 patch 里的 baseURL） |
