# 单卡多模型编排：显存账本、切换流程与故障复盘

> 适用场景：**一张 16 GB 显卡**要交替服务两类负载——本地大模型（QQ 机器人、DSH Agent）
> 与图像生成（Qwen-Image-2.1）。
> 本文所有数字来自本机实测（2026-09-21），日志片段为原文摘录。
> 相关文档：[`../DEPLOY.md`](../DEPLOY.md)（环境部署）、[`../QWEN-IMAGE-DEPLOY.md`](../QWEN-IMAGE-DEPLOY.md)（生图部署）、`~/qwen/README.md`（本地模型部署）。

---

## 0. 一句话结论

**本机 16 GB 显存只能同时容纳一个负载。** 大模型需约 12 GiB、ComfyUI 常驻约 14.3 GiB，
二者之和远超 16 GB。因此"整合"的本质不是让它们共存，而是**建立一套可靠的让位（handoff）流程**，
并且知道**怎么判断让位是否真的生效**。

---

## 1. 资源基线（实测）

| 资源 | 实测值 | 说明 |
|---|---|---|
| GPU | RTX 5080 Laptop，**16303 MiB**，sm_120 | 单卡 |
| 系统内存 | WSL 内 **23 GiB** + 6 GiB swap | 双负载同时驻留时会换页 |
| 本地大模型 | `qwen3.8-27b-local:latest`，Q4_K_M | 常驻时占 **15790 / 16303 MiB** |
| 生图模型 | Qwen-Image-2.1，int8 权重 | ComfyUI 自报总 15.89 GiB、**空闲仅 1.57 GiB** |

---

## 2. 显存账本：为什么装不下两个

### 2.1 大模型（Ollama）

单次加载尝试时 `ollama.log` 记录的各缓冲区大小：

| 缓冲区 | 大小 |
|---|---|
| `load_tensors: CUDA0 model buffer` | **10661.93 MiB** |
| `llama_kv_cache: CUDA0 KV buffer` | 748.00 MiB |
| `llama_memory_recurrent: CUDA0 RS buffer` | 102.87 MiB |
| `sched_reserve: CUDA0 compute buffer` | 375.81 MiB |
| **合计** | **≈ 11.6 GiB** |

加上推理时的激活与碎片，实测常驻约 **15.4 GiB**（`15790 MiB`）。

### 2.2 生图（ComfyUI + int8 权重）

`comfy.log` 里三个组件的 staged 大小：

| 组件 | 大小 |
|---|---|
| 文本编码器 `QwenImage21TEModel_` | **8916 MB** |
| DiT `QwenImage21` | **6920 MB** |
| VAE `WanVAE` | 644 MB |
| **合计** | **≈ 16.1 GB** |

ComfyUI 靠**分阶段动态换入换出**，峰值只按单阶段最大者计算（≈9 GB），所以 16 GB 卡能跑 2K
——这是它可行的原因，也是它**必须独占显卡**的原因。

> **结论**：`11.6 + 16.1 ≫ 16`。任何一个在跑，另一个就无法加载。

---

## 3. 故障复盘：生图成功了，聊天却报"卡住了"

这是一次真实事故。**表面症状和真实原因完全相反**，值得完整记录。

### 3.1 现象

用户在生图的同时/之后与 QQ 机器人聊天，机器人回复：

> 本地模型好像卡住了，等会儿再试试。

### 3.2 时间线（`ollama.log` + `official.log`）

| 时间 | 事件 | 证据 |
|---|---|---|
| ≈22:50:38 | 机器人向 Ollama 发 `/v1/chat/completions` | 该请求最终耗时 `1m34s` |
| ≈22:50:39 | Ollama 开始加载 27B | `load_tensors: CUDA0 model buffer = 10661.93 MiB` |
| 此刻 | **显存已被 ComfyUI 占满** | ComfyUI 自报 `空闲 1.57 GiB` |
| 22:52:08.850 | 客户端断开，加载被中止 | `client connection closed before llama-server finished loading, aborting load` |
| 22:52:08.854 | 加载判定失败 | `Load failed ... timed out waiting for llama-server to start: context canceled` |
| 22:52:12 | 请求以 499 结束 | `499 \| 1m34s \| POST "/v1/chat/completions"` |
| — | 机器人捕获超时并回兜底话术 | `httpx.ReadTimeout` → `config.json` 的 `error_text` |

`config.json` 里 `request_timeout = 90.0`，与"1m34s 后被客户端中止"吻合：
**是机器人自己等够 90 秒放弃了**，不是模型崩了。

### 3.3 三个反直觉结论

1. **生图是成功的。** ComfyUI 队列为空、历史里 18 个任务全部 `status: success`。
   用户看到的报错来自**聊天链路**，与生图任务本身无关。
2. **模型没有 OOM 崩溃，是被"等到超时"中止的。** 日志是
   `client connection closed ... aborting load`，不是 out of memory。
   也就是说：**调大 `request_timeout` 理论上能让请求"成功"，但会退化成 CPU 推理（约 1.6 tok/s），
   体验比失败更糟**。正确做法是腾显存，不是加超时。
3. **生图结束后显存不会自动回来。** 队列已空、任务已成功，`nvidia-smi` 仍显示
   `15113 MiB 已用 / 865 MiB 空闲`。这是下次聊天仍然失败的直接原因。

### 3.4 同时打满的不止显存

| 指标 | 当时 | 后果 |
|---|---|---|
| 系统内存 | 20 GiB / 23 GiB，可用 **629 MiB** | 触发换页 |
| Swap | **4.7 GiB / 6.0 GiB** | 严重换页 |
| CPU load | 2.47 / 16 线程（**正常**） | 所以"看 load 不高"会误导排查方向 |

`comfy.log` 里出现过 `Generating tokens: 1/1024 [01:15<21:20:56, 75.13s/it]`
——**75 秒/token**，这就是内存换页在 GPU 工作流里的表现。

> **排查启示**：这类故障要看 **`nvidia-smi` 的显存**和 **`free -h` 的 swap**，
> 而不是 `uptime` 的 load average。CPU 负载指标在这里完全正常。

---

## 4. 关键机制：ComfyUI 什么时候才真的放开显存

这一节的结论来自读源码，不是猜测。

### 4.1 文档默认行为 vs 实测现象

`comfy/cli_args.py:169` 对 `--highvram` 的说明是：

> "By default models will be unloaded to CPU memory after being used.
> This option keeps them in GPU memory."

即**默认应当"用完卸载到系统内存"**。但实测在队列清空后，显存仍被占住
（`0 models unloaded.` 反复出现在日志里）。

**现象与文档默认行为不一致，根因未完全定位**——怀疑与本版 ComfyUI 的
dynamic VRAM loading（日志中的 `prepared for dynamic VRAM loading ... Staged`）有关。
**因此不要依赖"它会自己放"，要显式释放。**

### 4.2 `POST /free` 的真实语义（源码级）

```bash
curl -s -X POST http://127.0.0.1:8188/free \
  -H 'Content-Type: application/json' \
  -d '{"unload_models":true,"free_memory":true}'
```

| 参数 | 源码行为 |
|---|---|
| `unload_models: true` | `server.py:1200` 设标志 → `main.py:389` 调 `unload_all_models()` → 对每个 torch 设备执行 `free_memory(1e30, device)` |
| `free_memory: true` | 额外执行 `e.reset()`，**清空执行缓存**（节点缓存里的 latent / VAE 结果） |

三个容易踩的点：

1. **两个参数都要传。** 只传 `unload_models` 只卸权重，不清执行缓存，显存可能仍被缓存占着。
2. **它是异步生效的。** `server.py:1195` 只做 `set_flag`，真正执行发生在
   `main.py:389` 的队列循环里 → **调用后要 `sleep 2~3` 再验证**，立刻查会以为没生效。
3. **NVIDIA 后端不受 no-op 影响。** `model_management.py:221` 提到
   "single-GPU ROCm users get an empty list which silently turns `unload_all_models()` into a no-op"
   —— 那是其他后端的坑；NVIDIA 分支会正常返回 CUDA 设备列表。

### 4.3 `--disable-smart-memory` 不是"释放"，是"换到内存"

`cli_args.py:192` 原文：

> "Force ComfyUI to agressively offload to **regular ram** instead of keeping models in vram when it can."

它把权重从显存搬到**系统内存**，显存是空出来了，但内存压力会变大。
本机 RAM 只有 23 GiB 且已经用掉 4.7 GiB swap，**开启它可能把瓶颈从显存转移到 swap**，
进而导致上面那种 `75 s/it` 的灾难。

**建议**：只有在"内存充裕、且需要 ComfyUI 长期在线"时才开它；否则用第 5 节的显式让位流程。

---

## 5. 标准作业流程（SOP）

### 场景 A：从聊天切到生图

```bash
# 1) 让 Ollama 卸载模型（保留服务，下次请求自动重载）
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3.8-27b-local:latest","keep_alive":0}' >/dev/null
sleep 4

#    想彻底停服务也可以（svc.sh 会先请求卸载再停进程）
#    bash ~/qq-bot/svc.sh stop ollama

# 2) 验收：显存应降到 2 GiB 以下（不含 Windows 侧占用）
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

# 3) 确认模型确实卸载了（应输出 {"models":[]}）
curl -s http://127.0.0.1:11434/api/ps

# 4) 生图（ComfyUI 已在跑则直接出图；没跑就）
bash ~/qwen-image/bin/serve.sh
```

**验收标准**：`memory.used` 明显下降，且 `/api/ps` 为空。

### 场景 B：从生图切回聊天

```bash
# 1) 释放 ComfyUI 的权重与执行缓存（两个参数都要）
curl -s -X POST http://127.0.0.1:8188/free \
  -H 'Content-Type: application/json' \
  -d '{"unload_models":true,"free_memory":true}'
sleep 3          # ← 必须等，它是异步生效的

# 2) 验收
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

# 3) 预热大模型，避免第一条聊天等 28~38 秒
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3.8-27b-local:latest","prompt":"hi","stream":false,"think":false,"keep_alive":-1,"options":{"num_predict":1}}' >/dev/null

# 4) 验收：模型已上卡
curl -s http://127.0.0.1:11434/api/ps | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('未加载' if not d.get('models') else f\"已加载 {d['models'][0]['size_vram']/2**30:.2f} GiB\")"
```

**验收标准**：`/api/ps` 显示已加载且 `size_vram` ≈ 13–15 GiB。

> **如果第 1 步之后显存没降下来**：说明 `/free` 没生效（见第 10 节待确认项），
> 退路是重启 ComfyUI：
> ```bash
> pkill -f '[m]ain.py' && sleep 3 && nvidia-smi --query-gpu=memory.used --format=csv,noheader
> ```
> 需要生图时再 `bash ~/qwen-image/bin/serve.sh`。

### 场景 C：两者都要长期在线（不推荐）

唯一可行的组合是**让 ComfyUI 用 `--disable-smart-memory`、并把机器人 `request_timeout` 调大**，
但代价是：

| 代价 | 说明 |
|---|---|
| 生图变慢 | 每次生成后权重被换出，下次要重新加载 |
| 聊天变慢 | 27B 被挤到 CPU，约 1.6 tok/s，一条回复几十秒 |
| swap 恶化 | 权重搬到系统内存，23 GiB RAM 会被吃满 |

**结论：场景 C 只适合"偶尔用一下"的场景，不适合把两个都当常驻服务。**

---

## 6. 建议的切换脚本（草案，未实现）

把 SOP 固化成一个命令，减少手工失误。骨架：

```bash
#!/usr/bin/env bash
# ~/qwen-image/bin/gpu-mode.sh  —— 一键切换 GPU 归属
# 用法: gpu-mode.sh image | chat | status
set -uo pipefail
OLLAMA=http://127.0.0.1:11434
COMFY=http://127.0.0.1:8188
MODEL=qwen3.8-27b-local:latest

vram_used() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits; }

case "${1:-status}" in
  image)   # 让位给生图
    curl -s "$OLLAMA/api/generate" -d "{\"model\":\"$MODEL\",\"keep_alive\":0}" >/dev/null
    sleep 4 ;;
  chat)    # 让位给聊天
    curl -s -X POST "$COMFY/free" -H 'Content-Type: application/json' \
         -d '{"unload_models":true,"free_memory":true}' >/dev/null
    sleep 3 ;;
  status)  : ;;
  *) echo "用法: $0 {image|chat|status}" >&2; exit 2 ;;
esac

echo "显存已用: $(vram_used) MiB"
curl -s "$OLLAMA/api/ps" | python3 -c "
import sys,json;d=json.load(sys.stdin)
print('大模型:', '未加载' if not d.get('models') else f\"已加载 {d['models'][0]['size_vram']/2**30:.2f} GiB\")"
```

**验收点**：`image` 模式应看到大模型"未加载"；`chat` 模式执行后显存应明显回落。

---

## 7. 排障对照表

| 症状 | 根因 | 处理 |
|---|---|---|
| 机器人回「本地模型好像卡住了」 | 显存被占，27B 加载不完 | 场景 B 的第 1 步；**不要**只调大 `request_timeout` |
| 生图报 OOM | Windows 侧或大模型占着显存 | 场景 A；并关掉浏览器硬件加速/游戏 |
| 生图每步几十秒 | 换页（swap 打满） | 看 `free -h`；降分辨率、关 `--disable-smart-memory`、调大 WSL 内存 |
| 生图完仍无法聊天 | ComfyUI 不自动放显存 | `POST /free` 两个参数都传 |
| 调完 `/free` 显存没变 | 它是异步生效 | `sleep 3` 后再看；仍不变则重启 ComfyUI |
| `Load failed ... context canceled` | 客户端先超时断开 | 同上，**先腾显存**再请求 |
| 网页打不开 `localhost:8188` | 服务没起 | `bash ~/qwen-image/bin/serve.sh` |

---

## 8. 非显存瓶颈：系统内存与 swap

23 GiB RAM 跑这套组合偏紧，表现为 GPU 工作流里出现 `75 s/it` 这种反常速度。

排查与改善：

```bash
free -h                    # 看 available 与 swap used
uptime                     # load average（这里正常不代表没问题）
```

改善手段（按收益）：

1. **不要同时驻留**——这是最有效的（见第 5 节）；
2. **调大 WSL 内存**：Windows 侧 `/mnt/c/Users/<你>/.wslconfig` 的 `memory=` 值，
   改完 `wsl --shutdown` 生效（**会重启 WSL**，注意保存工作）；
3. 回收 `~/qwen` 的冗余空间（约 35 GB，见 `~/qwen/README.md` 第 7 节）——
   这省的是磁盘不是内存，但对"换页到磁盘"的场景有帮助。

---

## 9. 健康检查（一条命令）

```bash
{
  echo "--- 显存"
  nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader
  echo "--- 内存/swap"
  free -h | sed -n '1,3p'
  echo "--- 服务"
  for s in ollama main.py official_bot.py; do
    printf '%-18s %s\n' "$s" "$(pgrep -f "$s" >/dev/null && echo 运行中 || echo 未运行)"
  done
  echo "--- 大模型"
  curl -s --max-time 5 http://127.0.0.1:11434/api/ps | head -c 200
  echo; echo "--- ComfyUI 显存自报"
  curl -s --max-time 5 http://127.0.0.1:8188/system_stats 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for v in d.get('devices',[]): print(v['name'],'空闲',round(v['vram_free']/2**30,2),'GiB')
except Exception: print('未运行')"
}
```

**判读**：`memory.free` < 2 GiB 而你想聊天 → 必然失败，先执行场景 B。

---

## 10. 待确认项（诚实边界）

本文的机制部分来自源码阅读，**以下三点未在本机实测**，使用前请自行验证：

1. **`POST /free` 在本机能否真的把 14.3 GiB 降下来。**
   源码逻辑成立（NVIDIA 分支不会 no-op），但**我没有对你的服务执行过状态变更**，
   所以"实测有效"这句话我不写。第一次使用请按场景 B 的第 2 步验收。
2. **ComfyUI 为何在队列清空后仍占住显存。**
   文档默认行为是"用完卸载到 CPU 内存"，实测与之不符，根因未定位。
   怀疑与 dynamic VRAM loading 的 staged 权重有关，但未证实。
3. **`--disable-smart-memory` 在本机的实际收益/代价。**
   方向明确（换到系统内存），但本机 RAM 已紧张，净收益可能为负。

---

## 11. 参考

- 本机日志：`~/qwen/logs/ollama.log`（`Load failed` / buffer 明细）、
  `~/qwen-image/logs/comfy.log`（staged 大小 / `0 models unloaded`）、`~/qq-bot/official.log`（`httpx.ReadTimeout`）
- ComfyUI 源码（本地检出 `~/qwen-image/ComfyUI`，HEAD `b0f4b7b`）：
  - `comfy/cli_args.py:169`（`--highvram` 默认行为）、`:192`（`--disable-smart-memory`）
  - `server.py:1195-1204`（`/free` 接口）、`main.py:387-394`（标志处理）
  - `comfy/model_management.py:219-221`（no-op 注释）、`:2121`（`unload_all_models`）
- [`../DEPLOY.md`](../DEPLOY.md) 第 1 节（显存预算与切换流程）
- [`../QWEN-IMAGE-DEPLOY.md`](../QWEN-IMAGE-DEPLOY.md)（生图部署与实测性能）
- `~/qwen/README.md`（本地模型实测占用与性能基线）
