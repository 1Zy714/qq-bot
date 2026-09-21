# QQ 机器人 · 本地大模型接入

把**本地 Ollama 上的大模型**接到 QQ 里聊天。提供两条互不冲突的接入路线，共用同一套配置、人设库和联网工具：

- **NapCat 路线**（`bot.py`）— OneBot v11 反向 WebSocket，功能最全，自用/小群
- **官方开放平台路线**（`official_bot.py`）— 腾讯官方 `botpy` SDK，合规、长期稳定

全程只走 `127.0.0.1`，**不需要公网 IP、不开任何防火墙端口**。

---

## 特性

| 特性 | 说明 |
|---|---|
| 双路线共用一套逻辑 | 配置、人设、历史、联网工具、回复清洗全部共享 |
| 单并发排队 | 适配器内部一把锁串行推理，避免请求在 Ollama 里互相插队导致全员超时 |
| 多套人设 + 聊天内切换 | 发「人格 小皮」即可切换并落盘，重启后仍生效 |
| 联网工具 | 今日新闻 / 微博头条抖音热榜 / Bing 搜索，**由模型自己判断**要不要联网 |
| 前缀缓存友好 | system prompt 逐字固定、历史追加式增长，冷缓存 1.35 s → 热缓存 0.22 s |
| 触发策略可配 | 私聊全响应；群聊只认 `@机器人` 或前缀，带同群冷却 |
| 失败兜底 | 超时/异常回固定话术，不把堆栈丢进群里 |
| 不污染环境 | 依赖装在 `~/.venvs/qqbot`，模型始终只监听回环 |

---

## 架构

```
┌──────────────── Windows ─────────────────┐   ┌──────────── WSL2 ────────────┐
│  QQ NT 客户端（NapCat 一键版自带）        │   │  bot.py                      │
│        │                                 │   │  ├ 反向 WS 服务 :6199        │
│        ▼                                 │   │  │  （NapCat 主动连进来）    │
│  NapCat ── OneBot v11 反向 WebSocket ────┼──▶│  ├ 单并发队列（串行）        │
│                                          │   │  └ 会话历史（每群环形缓冲）  │
└──────────────────────────────────────────┘   │            │                 │
                                               │            ▼                 │
   mirrored 网络：localhost 双向互通           │  Ollama :11434               │
                                               │  /v1/chat/completions        │
                                               │  + reasoning_effort:none     │
                                               └──────────────────────────────┘
```

官方路线的区别只在最左边那一段：换成 `official_bot.py` 用 `botpy` 反连腾讯网关，
右侧的模型、人设、工具、清洗逻辑完全复用。

**为什么用反向 WebSocket（bot 当服务端）**：只有 NapCat 主动连出来，bot 不需要向 QQ 侧暴露端口，
天然躲开「公网 OneBot 服务被打」那类问题。

---

## 目录结构

| 文件 | 作用 |
|---|---|
| `bot.py` | OneBot v11 反向 WS ↔ Ollama 适配器（队列 / 历史 / 清洗 / 触发） |
| `official_bot.py` | QQ 开放平台 `botpy` ↔ 本地模型的桥接（群 @ / 单聊） |
| `web_tools.py` | 联网工具：新闻 / 热榜 / 搜索，两条路线共用 |
| `config.example.json` | 配置模板（复制成 `config.json` 后修改） |
| `run.sh` | NapCat 路线：建 venv、装依赖、后台常驻启动 |
| `svc.sh` | 统一启停：两个机器人 + Ollama |
| `PLAN.md` | 方案与选型（含本机硬约束实测） |
| `OPS.md` | 运维与排障（含 3 个已踩过的故障） |
| `PERSONAS.md` | 人设库：定义、切换、调参 |
| `TOOLS.md` | 联网工具的可用性实测与扩展方法 |
| `OFFICIAL.md` | 官方开放平台路线说明 |
| `AUTOCHAT.md` | 自动聊天方案（**仅设计**，未实现） |
| `LEARNING.md` | 用本项目学 Python 的指南（知识点 → 真实行号、4 周路线、15 个练习） |

---

## 快速开始

### 0. 前置：本地模型服务

需要一个 OpenAI 兼容端点。本项目在 Ollama 上实测：

```bash
ollama pull qwen3.8-27b-local:latest     # 或任意你有的模型
export OLLAMA_KEEP_ALIVE=-1              # 关键：常驻，否则空闲后重新加载要 28–38 秒
curl -s http://127.0.0.1:11434/api/version
```

### 1. NapCat 路线

**Windows 侧**：下载 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 的
`NapCat.Shell.Windows.OneKey.zip` → 运行 `NapCatInstaller.exe` → `napcat.bat` → 用**小号**扫码登录。

在 NapCat WebUI（默认 `http://127.0.0.1:6099`）新增一个**反向 WebSocket** 客户端：

| 项 | 值 |
|---|---|
| URL | `ws://127.0.0.1:6199/onebot/v11/ws` |
| Token | 随机串，与 `config.json` 的 `token` 一致 |
| 消息格式 | **array**（不要选 CQ 码字符串） |

**WSL 侧**：

```bash
cd ~/qq-bot
cp config.example.json config.json     # 改 token / 模型名 / 触发前缀
bash run.sh                            # 首次自动建 venv 并装 httpx + websockets
bash run.sh --selftest                 # 只测模型，不需要 NapCat
bash run.sh -f                         # 前台运行，看实时日志
```

验收：`ss -ltn | grep 6199` 有监听，日志出现 `napcat connected`。

### 2. 官方开放平台路线

```bash
# 到 https://q.qq.com 创建机器人，拿到 AppID / AppSecret
cat > ~/qq-bot/qq.env <<'EOF'
QQ_APPID=你的AppID
QQ_SECRET=你的AppSecret
EOF
chmod 600 ~/qq-bot/qq.env

~/.venvs/qqbot/bin/pip install -U qq-botpy
bash ~/qq-bot/svc.sh start official
bash ~/qq-bot/svc.sh log   official     # 实时日志
```

`svc.sh` 会自动 `source qq.env`；也可以用环境变量覆盖。

> ⚠️ 官方平台的**主动消息**受客户端开关与频控限制（群 20 条/分·群、1000 条/天·群）。
> 本仓库的自动聊天方案见 `AUTOCHAT.md`，**尚未实现**。

---

## 配置说明（`config.json`）

| 字段 | 默认 | 作用 |
|---|---|---|
| `host` / `port` | `127.0.0.1` / `6199` | NapCat 反向 WS 监听地址，**不要改成 0.0.0.0** |
| `token` | — | OneBot 鉴权 token，**必填** |
| `ollama_base` | `http://127.0.0.1:11434/v1` | 模型端点（OpenAI 兼容） |
| `model` | `qwen3.8-27b-local:latest` | 模型名 |
| `system_prompt` | 见模板 | 保持逐字固定，这是前缀缓存能命中的前提 |
| `persona` / `personas` | 5 套 | 当前人设 / 人设库 |
| `max_tokens` | `70` | **最有效的延迟旋钮**：每多 10 token ≈ 多 1.2 秒 |
| `temperature` | `0.8` | 皮系人设 0.8–0.9；认真人设 0.5–0.7 |
| `history_turns` | `2` | 每轮多带 2 条历史，影响连贯感与延迟 |
| `queue_limit` | `3` | 队列满就回「忙」，避免积压后全员等到超时 |
| `max_reply_chars` | `200` | 双保险，超出直接截断 |
| `trigger_prefixes` | `["/ai","#ai"]` | 群聊触发前缀（群里建议只用前缀，避免刷屏） |
| `respond_to_private` | `true` | 私聊是否全响应 |
| `group_cooldown_seconds` | `5.0` | 同群冷却 |
| `reply_with_at` | `true` | 群回复是否带 @ |
| `reset_words` | `["重置","reset","清空"]` | 清空当前会话上下文 |
| `tools_enabled` | `true` | 关掉就退回纯聊天 |

---

## 常用命令

```bash
bash ~/qq-bot/svc.sh status              # 看两个机器人 + Ollama（含显存占用）
bash ~/qq-bot/svc.sh start official      # 官方机器人（自动读 qq.env）
bash ~/qq-bot/svc.sh start napcat        # OneBot 适配器（等 NapCat 连入 6199）
bash ~/qq-bot/svc.sh start ollama        # 起本地模型服务
bash ~/qq-bot/svc.sh restart official    # 改完 config.json 后重启生效
bash ~/qq-bot/svc.sh stop all            # 停两个机器人（不含 ollama）
bash ~/qq-bot/svc.sh stop ollama         # 停模型服务并释放显存
bash ~/qq-bot/svc.sh log official        # 实时日志
```

> `svc.sh` 认进程时直接扫 `/proc` 并要求命令行里出现**绝对路径**，绝不按名字模糊匹配 ——
> 因为 `pkill -f 'bot.py'` 会同时命中 `official_bot.py` 和正在执行命令的 shell 自己。

---

## 人设

内置 5 套：`千绘莉`（俄罗斯蓝猫小女孩）、`小皮`（皮系陪聊）、`暴脾气`（刀子嘴护崽）、
`雪乃`（雪之下雪乃）、`嘎子`（模仿谢孟伟直播风格）。

在聊天里直接发指令即可切换，**不受群冷却和排队限制**：

| 你发 | 效果 |
|---|---|
| `人格` | 列出当前人设和全部可选 |
| `人格 小皮` | 切换并**落盘**（重启后依然生效） |
| `换成雪之下雪乃` / `扮演 yukino` | 别称也能用 |
| `人格 随机` | 随机挑一套 |
| `重置` | 清空该会话上下文 |

群聊里要 `@机器人 人格 小皮` 或 `/ai 人格 小皮`；单独发 `人格` 不会触发（刻意的）。
新增人设见 `PERSONAS.md` 第 4 节。

---

## 联网工具

模型**自己判断**该不该联网——问新闻、热搜、最新事实时调工具，问「在吗」直接回答。

| 工具 | 触发场景 | 数据源 |
|---|---|---|
| `get_daily_news` | 新闻、时事 | `60s.viki.moe/v2/60s` |
| `get_hot_topics` | 热搜、热榜 | `60s.viki.moe/v2/{weibo,toutiao,douyin}` |
| `web_search` | 最新信息、模型不知道的事 | `cn.bing.com` |

三个源都免费无 key。联网要跑两轮模型（先决定调什么工具，再看着结果作答），
实测普通回复 6.2 s、联网回复 11.1 s。扩展方法见 `TOOLS.md` 第 5 节。

---

## 实测性能

本机：**WSL2 + RTX 5080 16G + Ollama**。模型常驻时：

| 场景 | 耗时 |
|---|---|
| 普通回复（system ~120 tok、无历史、回复 ~35 tok） | **2.5–3.3 s** |
| 联网回复 | 11.1 s |
| 冷加载（模型被换出后第一条） | 28–38 s |

解码速度 **约 8 tok/s** 是硬上限，所以：

- **回复越长等得越久** —— 想压体感就调 `max_tokens`，而不是砍人设；
- **人设长度几乎不是瓶颈** —— llama.cpp 会复用缓存前缀（875 tok 人设 prefill 仅 0.8 s）；
- **真正的成本是「8 tok/s × 输出长度」**，每多 10 token ≈ 多 1.2 秒。

---

## 安全与合规

**凭据**：`qq.env`（QQ AppID/Secret）、`config.json`（OneBot token）、
`*.log`（含真实 openid）、`persona.state` 均已由 `.gitignore` 排除，**从未进入版本历史**。
克隆后请自行 `cp config.example.json config.json` 并填自己的 token。

**网络**：所有服务只绑 `127.0.0.1`；用反向 WS；Token 必填；**绝不做公网端口映射**。
OneBot 端口一旦公网可达，等于把 QQ 账号控制权交出去。

**合规**：

| 路线 | 风险 |
|---|---|
| NapCat（OneBot） | ⚠️ 第三方协议端，修改/注入 QQ 客户端可能违反腾讯用户协议，**存在冻结风险**。建议用小号、不群发、不加好友、不营销 |
| 官方开放平台 | ✅ 合规，但群聊能力与主动消息受配额限制 |

**内容责任**：群里生成的内容由你负责；`system_prompt` 里已加约束，必要时再加敏感词过滤。

---

## 依赖

- Python 3.10+
- NapCat 路线：`httpx`、`websockets`（`run.sh` 自动安装到 `~/.venvs/qqbot`）
- 官方路线：`qq-botpy`
- 本地模型服务：Ollama（或任何 OpenAI 兼容端点）

---

## 许可

本仓库未指定开源许可证，默认保留所有权利。如需复用请先联系作者。
