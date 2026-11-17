# Week 7 · 综合实战：接入一个新能力

> 目标：把前 6 周学的「工具 + 记忆 + 提示词 + 评估」揉在一起，独立完成一个可演示的功能。
> 交付要求：**功能 + 错误处理 + 至少 2 个单测 + 一段文档**。

---

## 选题（三选一）

| 选题 | 用到什么 | 难点 |
|---|---|---|
| **A. 天气工具** | MCP 工具 + 提示词规则 | 外部 API 不稳定，要做降级 |
| **B. 日程提醒** | 记忆 + 定时任务 | 时间解析与持久化 |
| **C. 网页摘要** | 抓取 + 模型 + 记忆 | 正文清洗与长度控制 |

下面以 **A** 为主线写步骤，B/C 的差异在最后说明。

---

## A. 天气工具（推荐，1 周可完成）

### Day 1–2 · 先验证数据源

```bash
curl -s -m 10 "https://wttr.in/Guangzhou?format=3"
# 预期：Guangzhou: ⛅️ +28°C
```

若网络不通，改用**本地 fixture**：

```bash
echo '{"city":"Guangzhou","temp":28,"text":"cloudy"}' > tools/weather_fixture.json
```

（面试时说明「做了离线降级」反而加分。）

### Day 3 · 写 `tools/weather_mcp.py`

照抄第 3 周的 `time_mcp.py` 结构，把 `call_tool` 逻辑换成：

```python
def fetch_weather(city: str) -> str:
    # 先试网络，失败回退 fixture
    try:
        import urllib.request
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=3"
        with urllib.request.urlopen(url, timeout=8) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except Exception:
        path = Path(__file__).parent / "weather_fixture.json"
        return path.read_text("utf-8") if path.exists() else "天气服务暂不可用"
```

工具定义：`name="weather"`，参数 `city`（必填）。

**自测**：用 pipe 方式（同第 3 周）调用 `weather`，确认返回字符串。

### Day 4 · 注册 + 提示词

注册进 `dsh-qq.patch.yml`（insert，参考第 3 周）。

再在**人设**里加一句（`roles/` 下对应文件，或 `config.json` 的 persona）：

> 当主人问天气时，调用 `mcp__weather__weather` 工具，把结果用自己的话说出来。

重启机器人，私聊「广州今天天气怎么样」。

### Day 5 · 测试与文档

1. 单测：`tools/test_weather.py`（直接 import `fetch_weather`，用 fixture 断言返回非空）
2. 文档：在 `docs/` 下写 `WEATHER-TOOL.md`（背景/实现/降级/验证）

---

## B. 日程提醒（差异点）

- 存储：`memory/schedule.json`（`{"items":[{"when":"2026-11-18 10:00","text":"开会"}]}`）
- 解析：先只支持固定格式「明天 10 点 xxx」，用 `datetime` 算日期
- 提醒：`game_loop` 或独立 `asyncio` 任务每 60 秒检查一次，到点私聊
- 测试：直接调「添加 + 到点判断」两个函数

## C. 网页摘要（差异点）

- 抓取：`urllib` + 简单正文提取（去掉标签）
- 限制：正文截断到 4000 字再给模型
- 存记忆：摘要写进 `[记住:…]` 或成长附录

---

## 验收清单（硬性）

- [ ] 功能能演示（截图或日志）
- [ ] 外部依赖失败时有降级（不崩、给可读提示）
- [ ] 至少 2 个单测，`python3 test_*.py` 全绿
- [ ] 一篇 `docs/` 文档：背景 / 实现 / 降级 / 验证
- [ ] 提交：`git commit -m "course: week-07 接入天气工具"`

## 常见坑

| 现象 | 原因 |
|---|---|
| 模型不调工具 | 工具描述太含糊；人设里没写「何时用」 |
| 网络请求卡住 | 没设 timeout；一定要 `timeout=8` 之类 |
| 结果太长 | 工具返回先截断（如 500 字），再交给模型 |
| 降级没生效 | `except` 范围太窄（要覆盖 URLError/Timeout/JSON） |
