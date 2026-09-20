# 运维与排障（QQ 机器人 + 本地模型）

## 1. 服务启停

```bash
bash ~/qq-bot/svc.sh status              # 看两个机器人 + Ollama
bash ~/qq-bot/svc.sh start official      # 官方开放平台机器人（自动读 qq.env）
bash ~/qq-bot/svc.sh start napcat        # OneBot 适配器（等 NapCat 反向连入 6199）
bash ~/qq-bot/svc.sh restart official    # 改完 config.json 后重启生效
bash ~/qq-bot/svc.sh stop all
bash ~/qq-bot/svc.sh log official        # 实时日志
bash ~/qwen/bin/serve.sh                 # 启动 Ollama（幂等）
```

日志位置：`~/qq-bot/official.log`、`~/qq-bot/bot.log`、`~/qwen/logs/ollama.log`。

## 2. 一分钟健康检查

```bash
bash ~/qq-bot/svc.sh status
curl -s http://127.0.0.1:11434/api/ps | python3 -c "import sys,json;d=json.load(sys.stdin);[print(f\"VRAM {m.get('size_vram',0)/2**30:.2f} GiB / 总 {m['size']/2**30:.2f} GiB\") for m in d.get('models',[])]"
grep -a "机器人已上线" ~/qq-bot/official.log | tail -1
grep -ac "用时" ~/qq-bot/official.log       # 累计成功回复数
grep -aE 'Traceback|ERROR' ~/qq-bot/official.log | tail -3
```

判断标准：

| 检查项 | 健康值 |
|---|---|
| `VRAM` | **≈13–14 GiB**（低于 5 GiB 就是掉到 CPU 了，见故障 1） |
| 机器人进程 | 有 PID，`/proc` 里能看到 |
| 日志 | 无 `Traceback` / `ERROR` |
| 单条回复耗时 | 2–8 秒 |

## 3. 故障 1：回复突然变得极慢（30–60 秒一条）

**症状**：`/api/ps` 显示 `VRAM` 只有几百 MiB、总大小仍是 17.65 GiB；日志里出现

```
llama-server GPU discovery watchdog timed out
failure during llama-server GPU discovery
CUDA0 (RTX 5080): 0 layers, 375 MiB used, 846 MiB free
load_tensors: offloaded 0/66 layers to GPU     ← 一层都没上卡
```

**根因**：加载那一刻显卡可用显存不够（常见于上一个实例还没释放显存、新实例就开始加载；或 Windows 侧当时在占显存）。Ollama 判定放不下就整机跑 CPU，速度从 ~10 tok/s 掉到 ~1.6 tok/s。

**修复**（GPU 空出来之后执行，重载约 8–40 秒）：

```bash
M=qwen3.8-27b-local:latest
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\":\"$M\",\"keep_alive\":0}"   # 卸载
sleep 4
curl -s http://127.0.0.1:11434/api/generate \
  -d "{\"model\":\"$M\",\"prompt\":\"hi\",\"stream\":false,\"think\":false,\"keep_alive\":-1,\"options\":{\"num_predict\":1}}" >/dev/null
grep -a "offloaded" ~/qwen/logs/ollama.log | tail -1    # 应显示 53/66 layers
```

`keep_alive: -1` 让本次加载永久常驻（`/api/ps` 的 `expires_at` 会变成很远的未来），**不必重启 `ollama serve`**。

**预防**：`~/qwen/bin/serve.sh` 里已加 `export OLLAMA_KEEP_ALIVE=-1`，下次重启 Ollama 后默认就常驻，不再反复换出。

## 4. 故障 2：回复偏慢但速度正常（10–30 秒）

先把耗时拆开看，别凭感觉调参。用原生 `/api/chat` 能直接读到两段耗时：

```bash
python3 - <<'PY'
import json,time,urllib.request
body=json.dumps({"model":"qwen3.8-27b-local:latest","messages":[{"role":"user","content":"在吗"}],
 "think":False,"stream":False,"options":{"num_predict":70}}).encode()
t0=time.monotonic()
req=urllib.request.Request("http://127.0.0.1:11434/api/chat",data=body,headers={"Content-Type":"application/json"})
with urllib.request.urlopen(req,timeout=300) as r: d=json.load(r)
print(f"prefill {d['prompt_eval_count']} tok / {d['prompt_eval_duration']/1e9:.2f}s")
print(f"生成   {d['eval_count']} tok / {d['eval_duration']/1e9:.2f}s  ({d['eval_count']/(d['eval_duration']/1e9):.1f} tok/s)")
print(f"墙钟   {time.monotonic()-t0:.1f}s")
PY
```

**本机实测（RTX 5080 / 27B Q4_K_M）的结论**：

| 环节 | 冷缓存 | 热缓存 |
|---|---|---|
| prefill 749 tok | 1.35 s（557 tok/s） | **0.22 s（3434 tok/s）** |
| 生成 | **8.1 tok/s（稳定）** | 8.1 tok/s |

也就是说：

- **人设长度几乎不是瓶颈**——把 914 字人设砍到 518 字（prompt 749→426 tok），总耗时只差 0.4–1 秒，热缓存下几乎无差别。**不要为了速度砍人设。**
- **真正的成本是"生成 8.1 tok/s × 输出长度"**：每多 10 个 token ≈ 多 1.2 秒。两句话（~28 tok）≈ 3.4 秒生成，这是物理下限。
- 因此唯一有效的旋钮是 `max_tokens` 和"最多几句话说"这类**输出长度约束**。当前配置 `max_tokens: 70` + 人设"最多两句话"，实测 2.4–4.6 秒。
- **想再快只能换更小的模型**（8B 级 Q4 约 30–40 tok/s，可到 1–1.5 秒）。注意 16 GB 显存放不下两个模型，换了就要在"群聊快"和"27B 质量"之间取舍（`OLLAMA_MAX_LOADED_MODELS=1` 时切换要重载 30 秒）。

另外两个正常现象，不是故障：

1. **消息排队**：`OLLAMA_NUM_PARALLEL=1`，适配器内部用一把 `asyncio.Lock` 串行化。群里同时来 3 条，第 3 条要等前两条生成完。日志里表现为多个 `on_xxx_message_create` 后面才逐个出现 `用时`。
2. **前缀缓存失效**：llama.cpp 只有一份上下文，单聊和群聊交错时缓存前缀不断被打断，日志里的证据是 `forcing full prompt re-processing due to lack of cache data`。热缓存 0.22 秒 vs 冷缓存 1.35 秒就是这个差距。

## 5. 故障 3：命令把自己杀了（`killed by signal: SIGTERM`）

`pkill -f 'bot.py'` 会匹配到 `official_bot.py`，也会匹配到**正在执行这条命令的 shell 自己**。已踩过三次。

- 用 `bash ~/qq-bot/svc.sh stop/restart ...`（内部扫 `/proc` 且要求绝对脚本路径 + python 可执行文件，不会误伤）
- 万不得已手写时，用 `pkill -f '[o]fficial_bot\.py'`，并确保同一条命令行里没有其他含 `bot.py` 的内容

## 6. 重启后需要人工做的事

WSL / Windows 重启后（没有配 systemd 自启）：

```bash
bash ~/qwen/bin/serve.sh                 # 1. 起 Ollama
bash ~/qq-bot/svc.sh start official      # 2. 起官方机器人
# 3. （可选）装好 NapCat 后：bash ~/qq-bot/svc.sh start napcat
```

顺序无所谓，但 Ollama 没起来时机器人会回「本地模型好像卡住了」。
