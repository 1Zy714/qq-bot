# AI 工程实战课（结合 qq-bot）· 8 周详细讲义

这是 [../LEARN-AI-ROUTE.md](../LEARN-AI-ROUTE.md) 的**逐日讲义版**。
每周一份文档，含：目标、每日任务（命令/代码/预期输出）、验收清单、常见坑。

## 怎么用

```
每天：
  10 min 读当天任务
  35 min 敲命令/改代码
  10 min 在 experiments/week-NN.md 记 3 行（做了什么/结果/卡点）
   5 min git commit
```

## 讲义目录

| 周 | 讲义 | 主题 | 本周产出 |
|---|---|---|---|
| 1 | [week-01-model-runtime.md](week-01-model-runtime.md) | 模型与推理环境 | 3 组 max_tokens 耗时数据 |
| 2 | [week-02-prompt-context.md](week-02-prompt-context.md) | 提示词与上下文 | system/user 注入对比实验 |
| 3 | [week-03-tool-mcp.md](week-03-tool-mcp.md) | 工具调用与 MCP | 自写 `time_mcp.py` 并注册 |
| 4 | [week-04-memory-rag.md](week-04-memory-rag.md) | 记忆与检索 | 记忆新增来源 + 单测 |
| 5 | [week-05-agent-loop.md](week-05-agent-loop.md) | Agent 循环与核验 | 预算新规则 + 验证 |
| 6 | [week-06-evaluation.md](week-06-evaluation.md) | 评估与可观测 | `tools/stats.py` + 健康报告 |
| 7 | [week-07-capstone.md](week-07-capstone.md) | 综合实战 | 接入一个新能力 |
| 8 | [week-08-wrapup.md](week-08-wrapup.md) | 收尾 | 技术文档 + 简历素材 |

## 前置检查（开始前 5 分钟）

```bash
cd ~/qq-bot
~/.venvs/qqbot/bin/python -V                        # 3.12+
~/.venvs/qqbot/bin/python test_memory_search.py     # 全绿
curl -s http://127.0.0.1:8080/v1/models | head -c 200   # 本地模型在跑
bash svc.sh status                                   # 机器人状态
```

任何一项不过，先按 [../OPS.md](../OPS.md) 排障，别急着开始。

## 提交约定

每周结束提交一次：

```bash
git add -A && git commit -m "course: week-01 完成"
```

实验记录写在 `docs/ai-course/experiments/week-NN.md`，可以一起提交（不含密钥）。
