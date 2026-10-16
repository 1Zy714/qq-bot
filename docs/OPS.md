# 运维与排障手册（QQ 机器人 + 本地模型）

> 最后更新：**2026-10-17**　适用：QQ 官方开放平台路线 + Strata 本地模型
> 上一版是 Ollama 时代（27B / 11434），已按现在的架构重写；
> 旧版里仍有效的经验（`pkill` 陷阱、耗时拆解）保留在文末。

---

## 0. 一分钟体检（出问题先跑这四条）

```bash
bash ~/qq-bot/svc.sh status                       # ① 机器人 + 模型 + 显存
curl -s http://127.0.0.1:8080/v1/status | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('模型', d['model'], '| loaded', d['loaded'], '| 显存', d['machine']['gpu']['used_mib'], 'MiB')"
grep -ac "用时" ~/qq-bot/logs/official.log        # ③ 累计成功回复数（应持续增长）
grep -aE "Traceback|ERROR" ~/qq-bot/logs/official.log | tail -3   # ④ 有没有报错
```

| 检查项 | 健康值 |
|---|---|
| 机器人进程 | `[official] 运行中 PID …` |
| 模型显存 | **13~14 GiB**（掉到 < 5 GiB 说明模型没上卡，会极慢） |
| 机器人日志 | 无 `Traceback` / `ERROR` |
| 单条回复耗时 | **1~3 秒**（Strata 125B；两句话的短回复） |

---

## 1. 服务启停（全清单）

| 服务 | 启动 | 停止 | 端口 |
|---|---|---|---|
| **QQ 机器人**（大肥鱼） | `bash ~/qq-bot/svc.sh start official` | `svc.sh stop official` | — |
| **本地模型**（Strata） | 双击 `Y:\Strata\run-iq2_xs.bat` | 关掉那个终端窗口 | 8080 |
| **画图**（ComfyUI） | `bash ~/qwen-image/bin/switch.sh image` | `switch.sh chat`（切回聊天） | 8188 |
| **DSH Web GUI** | `dw`（= 源码启动） | 终端 Ctrl+C | 3080 |
| Ollama（**已停用**） | `bash ~/qwen/bin/serve.sh` | `svc.sh stop ollama` | 11434 |

**注意**：机器人有 systemd 守护（每 2 分钟巡检），用 `svc.sh stop` 停会自动暂停守护；
想彻底关掉守护：`systemctl --user stop qqbot-watchdog.timer`。

---

## 2. 日志在哪、怎么看

| 日志 | 路径 | 看什么 |
|---|---|---|
| 机器人主日志 | `~/qq-bot/logs/official.log` | 接话判定、回复内容、错误堆栈 |
| 健康守护 | `~/qq-bot/logs/watchdog.log` | 只有重启时才写（平时为空是正常的） |
| 本地模型 | `Y:\Strata\strata-iq2_xs.log` | 模型加载、每轮速度 |
| ComfyUI | `~/qwen-image/logs/comfy.log` | 出图流程 |
| DSH | `~/.dsh/logs/` | GUI / 启动错误 |

**最常用的五条 grep**：

```bash
grep -a "判定=" ~/qq-bot/logs/official.log | tail -8        # 最近的接话判定（排障第一现场）
grep -ac "用时" ~/qq-bot/logs/official.log                  # 累计成功回复数
grep -a "重复消息已跳过" ~/qq-bot/logs/official.log | tail   # 去重在工作
grep -a "发送熔断" ~/qq-bot/logs/official.log | tail         # 熔断在工作
grep -aE "Traceback|ERROR|失败" ~/qq-bot/logs/official.log | tail -5
```

---

## 3. 控制台检查（网页界面）

| 控制台 | 地址 | 能看什么 | 怎么打开 |
|---|---|---|---|
| **DSH Web GUI** | `http://127.0.0.1:3080` | 会话、任务、插件、工具调用 | 日常用 `dw` 启动 |
| **Strata 模型** | `http://127.0.0.1:8080` | 模型状态、直接聊天测试 | 浏览器打开（模型在跑时） |
| **ComfyUI** | `http://127.0.0.1:8188` | 出图工作流、队列、显存 | 出图模式下打开 |

**用命令行做同样的检查**（没有浏览器时）：

```bash
# 模型：是否加载、跑多快、占多少显存
curl -s http://127.0.0.1:8080/v1/status | python3 -m json.tool | head -30

# 模型：实测一次推理（看 prefill / 生成速度）
# ⚠️ max_tokens 必须给够（≥200）：Qwen3.8 是推理模型，会先输出 reasoning_content；
#    给少了预算全被"思考"吃光，content 会是 null，看起来像模型坏了（实测 16 → null，200 → 正常）。
curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"你好"}],"max_tokens":200}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin); m=d['choices'][0]['message']; t=d.get('timings',{})
c=m.get('content') or '(content 为空：max_tokens 给少了，预算被思考吃光)'
print('回复:', repr(c[:40]))
print('prefill %.0f tok/s | 生成 %.0f tok/s' % (t.get('prompt_per_second',0), t.get('predicted_per_second',0)))"

# ComfyUI：服务与显存
curl -s http://127.0.0.1:8188/system_stats | python3 -m json.tool | head -20

# 机器人：一条命令看全貌
bash ~/qq-bot/svc.sh status
```

---

## 4. 故障排查总表（按症状查）

| 症状 | 第一反应 | 深入命令 |
|---|---|---|
| **群里 @ 它不回** | 看判定是不是 `mentioned` | `grep -a "判定=" logs/official.log \| tail -5` |
| **群里不 @ 不接话** | 概率没中 / 冷却 / 静默 | 同上，看 `no_trigger` / `cooldown` / `quiet_hours` |
| **私聊正常但群里没反应** | 群不在白名单，或手机 QQ 没开"获取群内全部消息" | 看 `group_not_allowed` |
| **完全没反应（连私聊都不回）** | 进程挂了 / 网关断了 | `svc.sh status`；看日志有没有「机器人已上线」 |
| **回复特别慢（30 秒+）** | 模型没上卡 | `curl 127.0.0.1:8080/v1/status` 看显存 |
| **画图失败** | 显存被聊天模型占着 | `bash ~/qwen-image/bin/switch.sh status` |
| **进程活着但没反应** | "活僵尸"卡死 | 守护 2 分钟内会拉起；急就 `svc.sh restart official` |
| **日志里刷"发送熔断"** | 连续发送失败 | 正常保护，60 秒后半开重试 |
| **手动测试模型时 `content` 返回 null** | `max_tokens` 给小了，预算被"思考"吃光（Qwen3.8 是推理模型） | 调到 **≥200** |

---

## 5. 深入：机器人为什么不回某条消息（判定值对照）

每条群消息都会留下一行 `判定=xxx(层级)`，**看懂它就等于看懂了机器人的大脑**：

```bash
grep -a "判定=" ~/qq-bot/logs/official.log | tail -10 | sed 's/.*\[qq\]//'
```

| 判定 | 层级 | 含义 | 要动什么 |
|---|---|---|---|
| `mentioned` | must | **@ 了机器人**，必答 | ✅ 正常 |
| `keyword:大肥鱼` | must | 命中关键词，必答 | ✅ 正常 |
| `question` | must | 问句（需开 `reply_when_question`） | 可选开启 |
| `probability` | chance | 概率命中，接话 | ✅ 正常 |
| `no_trigger` | none | 没 @、没关键词、概率也没中 | 想更活跃 → 调高 `probability` |
| `quiet_hours` | gate | 落在静默时段 | 清空 `quiet_hours` |
| `group_not_allowed` | gate | 群不在白名单 | 改 `allowed_groups`（`["*"]`=不限） |
| `cooldown` | gate | 同群冷却中 | 调小 `cooldown_seconds` |
| `daily_limit` | gate | 当日额度用完 | 调大或设 0 |
| `too_short` / `too_long` | gate | 字数不在 `min_chars`~`max_chars` 之间 | 调整区间 |
| `disabled` | gate | `auto_chat.enabled=false` | 打开总开关 |
| `no_group_whitelisted` | gate | 白名单为空（=一个群都不接） | 填群 ID 或 `*` |

**三层顺序**：闸门 → 必答（@/关键词/问句）→ 概率。
闸门不过就直接拒，连概率都不摇。

---

## 6. 资源监控

```bash
# 显存 / 功耗 / 温度
nvidia-smi --query-gpu=memory.used,memory.total,power.draw,temperature.gpu --format=csv

# 内存（模型常驻约 46 GiB / 47 GiB，很紧）
free -h

# 磁盘（家目录 + Y 盘）
df -h /home
/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command "'{0:N1} GB 可用' -f ((Get-PSDrive Y).Free/1GB)"

# 谁的显存（模型在 Windows 侧，WSL 的 nvidia-smi 看不到进程名）
/mnt/c/Windows/System32/cmd.exe /c "netstat -ano | findstr :8080"
```

---

## 7. 备份与恢复

**要备份的四样东西**：

| 内容 | 路径 | 重要性 |
|---|---|---|
| 配置（含密钥） | `~/qq-bot/config.json` | ⭐⭐⭐ |
| **人设** | `~/qq-bot/roles/` | ⭐⭐⭐ |
| 认人记忆 | `~/qq-bot/memory/` | ⭐⭐ |
| 表情库 | `~/qq-bot/stickers/` | ⭐ |

```bash
# 一键打包
cd ~/qq-bot && tar czf ~/archive/backups/qqbot-$(date +%Y%m%d).tar.gz \
  config.json roles/ memory/ stickers/index.json
```

**恢复**：解开覆盖回去，然后 `bash ~/qq-bot/svc.sh restart official`。

---

## 8. 升级与变更流程

| 改什么 | 要重启吗 | 怎么做 |
|---|---|---|
| **人设**（`roles/*.md`） | ❌ **不用** | 改完保存，下一条消息即生效 |
| 行为参数（`config.json` 的 `auto_chat`） | ✅ 要 | `svc.sh restart official` |
| 代码（`*.py`） | ✅ 要 | 同上；改完先 `python -m py_compile` 验语法 |
| 守护（`watchdog.sh`） | ❌ | systemd 每次现读 |
| 模型（Strata 侧） | ✅ | 关窗口重开 |

**改代码后的自检顺序**：

```bash
cd ~/qq-bot
~/.venvs/qqbot/bin/python -m py_compile official_bot.py bot.py image_gen.py web_tools.py
bash ~/qq-bot/svc.sh restart official
sleep 8 && bash ~/qq-bot/svc.sh status
tail -5 ~/qq-bot/logs/official.log | sed 's/\x1b\[[0-9;]*m//g'
```

---

## 9. 重启后要做的事

**WSL/Windows 重启后**：

```bash
# ① 本地模型（Windows 侧，不在 WSL 里）
#    双击 Y:\Strata\run-iq2_xs.bat，等它打印 ready（加载约 40~60 秒）

# ② 机器人（WSL 侧）
bash ~/qq-bot/svc.sh start official

# ③ 检查
bash ~/qq-bot/svc.sh status
```

**已经做到自启的部分**：
- 机器人**健康守护**：systemd user timer + `linger`（无登录会话也会跑），
  `systemctl --user list-timers qqbot-watchdog.timer` 可看下次巡检。

**还没有自启的部分**：本地模型（Strata）需要手动开窗口；ComfyUI 按需启动。

---

## 10. 保留自 Ollama 时代的旧经验（仍然有效）

### 10.1 `pkill` 会把自己杀掉

`pkill -f 'bot.py'` 会匹配到 `official_bot.py`，**也会匹配到正在执行这条命令的 shell 自己**（已踩过三次）。

- 用 `bash ~/qq-bot/svc.sh stop/restart ...`（内部扫 `/proc`，要求绝对脚本路径 + python 可执行文件，不会误伤）
- 万不得已手写时用 `pkill -f '[o]fficial_bot\.py'`

### 10.2 排队与缓存（架构未变，现象一样）

- **消息排队**：模型一次只跑一条，群里同时来 3 条，第 3 条要等前面生成完。
- **前缀缓存**：单聊与群聊交错时前缀缓存被打断，冷缓存比热缓存慢 1 秒左右，属正常。

### 10.3 旧版针对 Ollama 的故障（现已不适用，留档备查）

- 「回复突然极慢（30–60 秒）」当时是 Ollama 加载时显存不够、整机掉 CPU 导致的
  （`offloaded 0/66 layers`）。**换 Strata 后架构不同**：模型专家常驻内存 + GPU 专家缓存，
  不会再出现"一层都没上卡"，但仍有类似的"显存被别的东西占了"的风险 —— 现在查
  `curl 127.0.0.1:8080/v1/status` 的 `machine.gpu.used_mib` 即可。

---

## 附：一条命令看全局

```bash
bash ~/qq-bot/svc.sh status && \
echo "--- 模型 ---" && curl -s http://127.0.0.1:8080/v1/status | python3 -c "
import json,sys; d=json.load(sys.stdin); m=d['machine']
print('loaded', d['loaded'], '| 显存 %.1f/%.1f GiB | 内存 %.1f/%.1f GiB' % (
  m['gpu']['used_mib']/1024, m['gpu']['total_mib']/1024, m['ram']['used_gib'], m['ram']['total_gib']))" && \
echo "--- 最近判定 ---" && grep -a "判定=" ~/qq-bot/logs/official.log | tail -3 | sed 's/.*\[qq\]//'
```
