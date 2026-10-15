# 把本地 Qwen3.8-27B 接入 QQ 机器人 — 方案

> 目标机：WSL2（mirrored 网络）+ RTX 5080 16G + Ollama 0.32.15 + `qwen3.8-27b-local:latest`
> 本文所有"实测"结论都来自本机实际执行，不是估算。

---

## 0. 一句话结论

**Windows 侧跑 NapCat（负责 QQ 登录），WSL 侧跑一个薄的 OneBot v11 适配器 + Ollama，全程走 `127.0.0.1`，不开任何防火墙端口、不改任何绑定。**

理由：mirrored 模式下 Windows 与 WSL 的 localhost 双向互通（已实测：Windows `Invoke-WebRequest http://localhost:11434/api/version` 返回了 WSL 里 Ollama 的版本号），所以"QQ 登录在 Windows、模型在 WSL"这种最自然的切分不需要任何网络暴露。

---

## 1. 硬约束（本机实测）

| 约束 | 实测值 | 对方案的影响 |
|---|---|---|
| 模型速度 | 解码 **7.6 tok/s**，prefill **97.7 tok/s** | 回复必须短、上下文必须小，否则一条消息等几分钟 |
| 并发 | `OLLAMA_NUM_PARALLEL=1`、`MAX_LOADED_MODELS=1` | 适配器必须**串行排队**，不能并发打过去 |
| 显存 | 15790 / 16303 MiB，53/66 层在 GPU | 已接近满载，DSH 和 QQ 机器人同时用会互相排队 |
| 上下文 | 32768，KV q8_0 | 够用，但**别填满**，prefill 时间线性增长 |
| 思考开关 | `reasoning_effort:"none"` 有效；`think:false`、`chat_template_kwargs.enable_thinking=false`、提示词 `/no_think` **实测均无效** | 必须有一层能注入参数的适配器（或代理），否则会白等一大段推理再吐出正文 |
| 网卡 | WSL 里有 `192.168.1.3`（Windows 2.5G 网卡）和 `26.46.238.146`（Radmin VPN） | 需要给别的设备用时才能用上；本方案用不到 |
| Docker Hub | **不可达**（`registry-1.docker.io`） | ❌ 排除 NapCat/AstrBot 的 Docker 部署路线 |
| GitHub 直连 | 被 Steam++ 劫持 + 证书不在 Linux 信任链，直连 60–75 KB/s | 下载 NapCat 用镜像站或 `curl -k` |
| sshd | 未安装/未运行 | 不要指望 SSH 隧道来访问，本方案也不需要 |

---

## 2. 三条路线对比

| | A. 轻量自研（推荐） | B. AstrBot / NoneBot2 | C. QQ 开放平台官方 |
|---|---|---|---|
| QQ 侧 | NapCat（Windows） | NapCat 或官方适配器 | 官方审核通过的机器人 |
| 适配层 | 本方案的 `bot.py`（~200 行） | 现成框架 + WebUI | 官方 SDK `botpy` |
| 上手成本 | 低（一个文件 + 一个 venv） | 中（装框架、配 provider、调插件） | 高（注册开发者、建应用、审核、沙箱） |
| 参数可控 | ✅ 完全可控（`reasoning_effort`、排队、裁剪都自己写） | ⚠️ 取决于 provider 是否暴露额外 body 参数 | ✅ |
| 资源占用 | 极低 | 较高（常驻 WebUI + 插件） | 低 |
| 合规 | ⚠️ 第三方协议端，有封号风险 | ⚠️ 同上 | ✅ 官方合规 |
| 适用 | 自用、小群、要控延迟 | 需要插件生态、多平台、TTS/绘图 | 长期稳定运营、对公 |

**建议**：先走 A 打通并调好延迟；如果后面想要插件生态，把适配器的"取回复"那一层换成调 AstrBot 的接口即可，QQ 侧不用重做。要长期对外，才升级到 C。

---

## 3. 推荐架构

```
┌─────────────────── Windows ───────────────────┐   ┌─────────── WSL2 ───────────┐
│  QQ NT 客户端 (NapCat 一键版自带，不动你现有QQ) │   │                            │
│        │ 注入                                  │   │  bot.py                    │
│        ▼                                       │   │  ├ 反向 WS 服务 :6199      │
│  NapCat ── OneBot v11 反向 WebSocket ──────────┼──▶│  │   (NapCat 主动连过来)    │
│         (连 ws://127.0.0.1:6199/onebot/v11/ws) │   │  ├ 单并发队列 (串行)       │
│                                                │   │  └ 会话历史 (每群环形缓冲) │
└────────────────────────────────────────────────┘   │            │               │
                                                     │            ▼               │
   mirrored 网络：localhost 双向互通（已实测）        │   Ollama :11434            │
                                                     │   /v1/chat/completions     │
                                                     │   + reasoning_effort:none  │
                                                     └────────────────────────────┘
```

端口与绑定：

| 组件 | 位置 | 监听 | 可见性 |
|---|---|---|---|
| NapCat | Windows | 本地 + 反向 WS 出站 | 不出网 |
| bot.py | WSL | `127.0.0.1:6199` | 仅本机（Windows 侧可连） |
| Ollama | WSL | `127.0.0.1:11434` | 仅本机 |

**用反向 WebSocket（bot 当服务端）而不是正向**：这样只有 NapCat 主动连出来，bot 不需要向 QQ 侧暴露任何端口，也天然躲开了"公网 OneBot 服务被打"那类问题（参见 [2025-09-05 OneBot 服务遭攻击事件](https://wesley-young.github.io/2025-09-05-attack-on-onebot-service)）。

---

## 4. 落地步骤

### Step 1 — Windows 侧装 NapCat

1. 从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 **`NapCat.Shell.Windows.OneKey.zip`**（无头绿色版，自带 QQ，**不会动你现有的 QQ 安装**）。
   - 你的 GitHub 直连很慢且证书被 Steam++ MITM，用镜像前缀下载或加 `-k`：
     `curl -kL -o NapCat.Shell.Windows.OneKey.zip https://github.moeyy.xyz/https://github.com/NapNeko/NapCatQQ/releases/download/<tag>/NapCat.Shell.Windows.OneKey.zip`
2. 解压 → 运行 `NapCatInstaller.exe` 自动配置 → 进 `NapCat.XXXX.Shell` 目录 → 运行 `napcat.bat`。
   - 参考官方文档：[NapCat Shell 启动教程](https://doc.napneko.icu/guide/boot/Shell)
3. 用**小号**扫码登录（不要用主号，理由见第 7 节）。
4. 打开 NapCat WebUI（默认 `http://127.0.0.1:6099`），在网络配置里**新增一个「反向 WebSocket」客户端**：
   - URL：`ws://127.0.0.1:6199/onebot/v11/ws`
   - Token：填一个随机串（和 Step 2 的 `config.json` 里一致）
   - 消息格式：`array`（不要选 CQ 码字符串，适配器按 array 解析）

**验收**：NapCat 日志里出现「反向 WS 连接成功」。

### Step 2 — WSL 侧起适配器

```bash
cd ~/qq-bot
cp config.example.json config.json    # 改 token / 模型名 / 触发前缀
bash run.sh                           # 首次会自动建 venv 并装 httpx + websockets
```

**验收**：`ss -ltn | grep 6199` 有监听，且日志出现 `napcat connected`。

### Step 3 — 联调

在 QQ 里私聊机器人发一句，或群里 `@机器人 你好`。

**验收**：10–30 秒内收到回复（首响时间见第 5 节预算）。

### Step 4 — 调参（决定体验的一步）

在 `config.json` 里按需收紧：

| 旋钮 | 默认 | 作用 |
|---|---|---|
| `history_turns` | `4` | 每轮多带 4 条历史 ≈ 多 400 tok ≈ 多 4 秒 prefill |
| `max_tokens` | `160` | 上限 160 tok ≈ 21 秒；群里建议 80–120 |
| `system_prompt` | 简短版 | **保持 ≤150 tok 且内容固定**，这是前缀缓存能命中的前提 |
| `queue_limit` | `3` | 队列超过就回"忙"，避免积压后全员等到超时 |
| `trigger_prefixes` | `["/ai","#ai"]` | 群里建议只用前缀，避免刷屏 |

### Step 5 — 常驻

```bash
# 手动：两个脚本都幂等
bash ~/qwen/bin/serve.sh      # Ollama
bash ~/qq-bot/run.sh          # 适配器（后台，日志 ~/qq-bot/bot.log）
bash ~/qq-bot/run.sh -f       # 前台，实时看日志
```

建议在 `~/qwen/bin/serve.sh` 里再加一行 `export OLLAMA_KEEP_ALIVE=-1`：否则模型空闲 5 分钟后卸载，下一条群消息要等 **28–38 秒**重新加载（`~/qwen/README.md` 实测值）。常驻机器人场景这行是必须的。

开机自启需要 systemd + sudo（你目前没有配置，`~/qwen/README.md` 第八节也记录过这条）；`run.sh` 里已用 `setsid nohup` 语义，终端关掉不会死。

---

## 5. 延迟预算（按实测 97.7 prefill / 7.6 decode）

| 组成 | token | 耗时 |
|---|---|---|
| system prompt | ~120 | 1.2 s |
| 历史 4 轮 | ~400 | 4.1 s |
| 生成 120 tok | 120 | 15.8 s |
| **合计（完整回复）** | | **≈ 21 s** |

首字约 5 s 之后开始出字。群里"等 20 秒"是能接受的下限，想更快只有三条路：
1. **换小模型**（最有效）：同一套代码，`config.json` 里把 `model` 换成 8B/4B 级量化模型，速度可到 30–60 tok/s；
2. **换更小量化**：`~/qwen/README.md` 提到的 `IQ4_XS`（15.48 GB）能多上 1–2 层，收益有限；
3. **砍上下文**：`history_turns: 2` + `system_prompt` 压到 60 tok。

> **实测对照**：模型已常驻时，上面配置（system ~120 tok、无历史、回复 ~35 tok）端到端 **2.5–3.3 s**。也就是说 21 s 基本全来自"历史 + 长回复"，这两项是你要盯的主要旋钮。

> 不建议把 27B 当作活跃群聊机器人：7.6 tok/s 是硬上限，群消息密集时会持续排队。更实用的分工是**本地 27B 做私聊/低频问答，群聊挂云端 API**——两者在适配器里只是换一个 base_url 和 key。

---

## 6. 关键工程点（都在 `bot.py` 里实现了）

1. **`reasoning_effort: "none"` 必须显式发送**——本机实测：只有它能关掉思考。`think:false`、`chat_template_kwargs`、提示词 `/no_think` 全部无效（`/no_think` 那次模型仍产出了推理块）。
2. **单并发队列**：`OLLAMA_NUM_PARALLEL=1`，适配器内部只有一个 worker 串行取任务，避免多个请求在 Ollama 里互相插队导致全员超时。
3. **前缀缓存友好**：system prompt 逐字固定 + 历史追加式增长；超预算时**整体重置**而不是从中间裁——从中间裁会让整个前缀变化，缓存全失效，下一条消息又要完整 prefill 一遍。
4. **回复清洗**：`reasoning` 字段绝不转发到群里；去掉 `<think>…</think>`、包裹的引号、多余换行。
5. **触发策略**：私聊全响应；群聊只认 `@机器人` 或前缀，且同一群有冷却，避免 bot 之间互相触发。
6. **失败兜底**：请求异常/超时回一句固定话术，不把堆栈丢进群里。

---

## 7. 风险与合规

| 风险 | 说明 | 缓解 |
|---|---|---|
| **封号** | NapCat 属于第三方协议端，修改/注入 QQ 客户端可能违反腾讯用户协议，实际存在冻结风险 | 用小号；不群发、不加好友、不营销；做好随时换号的心理准备 |
| **端口暴露** | OneBot 端口一旦公网可达，等于把 QQ 账号控制权交出去 | 只绑 `127.0.0.1`；反向 WS；Token 必填；绝不做公网端口映射 |
| **GPU 抢占** | DSH 与 QQ 机器人抢同一张卡，`MAX_LOADED_MODELS=1` 会导致反复换出/加载（每次 28–38 s） | 错峰使用；或给机器人单独跑一个小模型 |
| **内容责任** | 群里生成的内容由你负责 | 精简 system prompt 加约束；必要时加敏感词过滤后再发送 |
| **模型幻觉** | 27B Q4 在群里容易被当成"官方客服" | 在 system prompt 里写明"这是本地小模型，回答可能出错" |

---

## 8. 备选：QQ 开放平台官方机器人

适合"要长期稳定、不怕审核"的场景：

- 到 QQ 开放平台注册开发者 → 创建机器人应用 → 提交审核；
- 用官方 `botpy` SDK 走 **WebSocket 模式**（不需要公网回调地址），把生成部分接到同一个 Ollama 端点；
- 代价：个人开发者的机器人在**群聊场景受限**（需被邀请、消息频率有配额、部分能力要企业资质），且沙箱环境与正式环境行为不同。

如果接受这些限制，这条路线的适配层代码可以复用本方案的 `ollama_chat()` 函数，只需把"收发消息"那层从 OneBot 换成 `botpy`。

---

## 9. 回滚

```bash
pkill -f 'qq-bot/bot.py'      # 停适配器
# Windows 侧：NapCat 托盘/控制台退出
rm -rf ~/qq-bot               # 删方案文件（Ollama 与 ~/qwen 完全不受影响）
```

---

## 10. 交付物清单

| 文件 | 作用 |
|---|---|
| `~/qq-bot/PLAN.md` | 本文档 |
| `~/qq-bot/bot.py` | OneBot v11 反向 WS ↔ Ollama 适配器（含队列/历史/清洗/触发） |
| `~/qq-bot/config.example.json` | 配置模板（`run.sh` 首次会提示复制成 `config.json`） |
| `~/qq-bot/run.sh` | 建 venv、装依赖、后台常驻启动；`-f` 前台、`--selftest` 只测模型 |

依赖装在 `~/.venvs/qqbot`（httpx 0.28.1 + websockets 17.1），不污染系统 Python。

---

## 11. 已完成的验证（本机实测）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 触发/清洗逻辑单测 | 5 个用例（@我 / 前缀 / 普通群聊 / @别人 / 私聊） | 全部 PASS；`<think>` 与包裹引号被剥掉 |
| 模型链路自检 | `bash run.sh --selftest` | ✅ 3.3 s 返回正文，思考已关 |
| 反向 WS + 鉴权 + 群回包 | 假 OneBot 客户端连 `ws://127.0.0.1:6199` | ✅ 错误 token 被 `1008` 拒；正常连接后群 @ 触发生成，收到 `send_group_msg`（带 at 段），端到端 2.5 s |

**尚未验证**：NapCat 真实实例（第 4 节 Step 1）——需要你在 Windows 侧装好并配好反向 WS 才能跑。上面三项已覆盖除 NapCat 之外的全部链路，Step 3 联调若与预期不符，把 `~/qq-bot/bot.log` 和 NapCat 日志给我即可。

> 小坑提醒（你 `~/qwen/README.md` 第六节踩过同一个）：`pkill -f 'bot.py'` 如果写在一行复合命令里、而那条命令本身含 `bot.py` 字样，会把自己一起杀掉。脚本内已用 `[b]ot\.py` 写法规避。
