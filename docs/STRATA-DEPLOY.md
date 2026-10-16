# Strata 部署实录：在 RTX 5080 上跑 125B MoE 模型

> 部署时间：2026-10-15 深夜 ~ 2026-10-16
> 环境：Windows 11（原生执行）+ WSL2 Ubuntu-24.04（操作与机器人侧）
> 结果：**成功**。Qwen3.8-Flash-Next IQ2_XS 落地，实测生成 **70+ tok/s**，QQ 机器人已切换过去。
> 配套的"当晚暂停快照"见 [STRATA-PROGRESS.md](STRATA-PROGRESS.md)。

---

## 一、为什么要换掉 Ollama

原方案是 Ollama 跑 `qwen3.8-27b-local`（Q4_K_M，17.65 GiB 权重），而显卡只有 16.3 GB 显存，**装不下**，只能被迫分层 offload。

实测（同一句提示词、多个配置对照）：

| 配置 | 生成速度 | 预填充 | 图分割（graph splits） |
|---|---|---|---|
| 默认（53/66 层上 GPU，ctx 32K） | 1.98 tok/s | 28 tok/s | 211 |
| ctx 8K，56 层上 GPU | 2.98 tok/s | 26 tok/s | — |
| ctx 8K，40 层上 GPU | **4.85 tok/s** | 32 tok/s | — |
| 纯 CPU（num_gpu=0） | 2.22 tok/s | 16 tok/s | — |
| ctx 32K，40 层 | 4.15 tok/s | 28 tok/s | 420 |

GPU 侧功耗只有 73 W、利用率 21%——**算力在划水**，瓶颈是 CPU/GPU 之间反复同步（图分割达 420 次）。

Strata 的思路正好相反：**不要求整个模型装进显存**。它把模型当作 24,576 个小专家的团队，每个 token 只用其中 10 个：

- **显存**：attention/DeltaNet、router、shared experts、输出头、MTP 草稿层、KV cache，外加一个**自适应专家缓存**（最常用的几千个专家）；
- **内存**：全部专家常驻，CPU 与 GPU **并行**计算没被缓存的那批；
- **SSD**：28.8 GB 的 n-gram 查找表，每 token 只读几行。

再叠加**投机解码**（模型自带 MTP 层先猜 3 个 token，一过 48 层验证，平均每次通过 2.4~3.2 个，快 1.6~1.8 倍）和**大块预填充**（一次 8192 token，专家经 PCIe 流式预取）。

官方数据（RTX 5070 12 GB）：Q2_0 94 tok/s、IQ2_XS 79 tok/s。参考 [Strata README](https://github.com/Niko1221/Strata/blob/main/README.zh-CN.md)。

---

## 二、硬件盘点与方案选型

### 2.1 本机硬件（`setup --check` 原始输出）

```
[ok] GPU: NVIDIA GeForce RTX 5080, 15.9 GB VRAM, compute capability 12.0, driver 617.42
[ok] RAM: 47 GB
[ok] CPU: AMD Ryzen 7 9700X 8-Core Processor (AVX-512)
[ok] PCIe: 4.0 x16 (the card supports 5.0, the board 4.0)
```

| Strata 要求 | 本机 | 判定 |
|---|---|---|
| NVIDIA RTX 20-50 / ≥12 GB 显存 | RTX 5080 **16 GB**（sm_120） | ✅ |
| 驱动 ≥ 580 | 617.42 | ✅ |
| CPU 支持 AVX2（AVX-512 更快） | Ryzen 9700X（**有 AVX-512**） | ✅ |
| OS：Windows 10/11 或 Linux | Windows 11 + WSL2 | ✅ |
| 内存 ≥ 32 GB | **47.1 GB** | ⚠️ 卡在 48 GB 档下沿 |
| 磁盘 70~80 GB | 部署前 Y 盘剩 226 GB | ✅ |

### 2.2 规格选择

setup 对各规格的判定（原话）：

```
Q2_0     needs ~48 GB RAM: tight
IQ2_XS   needs ~48 GB RAM: tight          ← 最终选择
IQ3_XXS  needs ~60 GB RAM: fits in the low-RAM mode
IQ3_S    needs ~62 GB RAM: fits in the low-RAM mode
IQ1_M    needs ~32 GB RAM: fits
UD-IQ4_XS / UD-Q4_K_XL / 更高         does not fit
```

选 **IQ2_XS**，理由：

- **不选 Q2_0**：文档明确 Q2_0 在 AVX-512 的 CPU 上会额外写一份 **~40 GB 的专家副本**供快速核使用，磁盘开销翻倍；IQ2_XS 是 i-quant，无此开销，质量还略好。
- **不选 IQ1_M**：虽然只要 32 GB 内存最宽松，但它是 Coder 专用（只保留一半专家），**中文能力弱**，不适合 QQ 机器人这种中文场景。
- **不选 IQ3_XXS/IQ3_S**：质量更好，但会切到 low-RAM 模式（GPU 只能放 22~25% 专家），本场景优先要速度。

### 2.3 部署位置：Windows 原生，而不是 WSL

| 方案 | 内存可用 | 结论 |
|---|---|---|
| WSL2 内跑 | 只有 **23 GiB**（默认取物理内存 50%），而 Strata 要往内存灌 35~55 GB | ❌ 除非改 `.wslconfig` 并重启 WSL（会中断会话） |
| **Windows 原生跑** | **全部 47 GB 可用**，GPU 也是原生性能 | ✅ 采用 |

跨系统访问不成问题：`.wslconfig` 里已经开了 `networkingMode=mirrored` + `hostAddressLoopback=true`，所以 **WSL 里用 `127.0.0.1:8080` 就能直连 Windows 侧的服务**——QQ 机器人正好在 WSL 里。

### 2.4 数据盘：Y 盘

- C 盘只剩 25 GB，**绝不能放**；
- WSL 发行版虚拟盘位于 `Y:\wsl\`（`Ubuntu-24.04 → Y:\wsl\`，由注册表 `HKCU:\...\Lxss` 查得）；
- Y 盘部署前剩余 226 GB，放 63 GB 模型 + 引擎绰绰有余。

---

## 三、部署全过程

> 全程通过 WSL 调用 Windows 命令（interop）完成；也可以直接在 Windows 终端里做，把 `/mnt/c/Windows/System32/cmd.exe /c '...'` 外层换成 cmd 即可。

### 3.0 前置：选一条能用的下载通道（这一步决定成败）

本机直连 GitHub 实测：**Windows 侧 ~25 KB/s，WSL 侧 ~40 KB/s**。而需要下载的东西有：源码 zip（21.6 MB）、引擎包（148 MB）、llama.cpp 源码（39.6 MB）。

测速要用**持续速度**，不能被突发骗了：

```bash
URL="https://github.com/Niko1221/Strata/releases/download/v0.1.44.1/strata-windows-x64.zip"
for m in "https://gh-proxy.com/" "https://ghfast.top/"; do
  printf "%-24s " "$m"
  timeout 26 curl -sS -o /dev/null --max-time 23 -r 100000000-135000000 \
    -w "HTTP=%{http_code} 持续速度=%{speed_download} B/s\n" "${m}${URL}"
done
```

实测结论：

| 通道 | 持续速度 | 说明 |
|---|---|---|
| 直连 GitHub | 25~40 KB/s | 不可用 |
| `ghfast.top` | **335 KB/s** | ✅ 但前 12 秒是突发（能到 2.7 MB/s），之后掉到 ~9 KB/s，**别按突发速度估时间** |
| `gh-proxy.com` | **313 KB/s** | ✅ 稳定 |
| `gh.llkk.cc` / `github.moeyy.xyz` / `ghproxy.cc` / `hub.gitmirror.com` | 不可用 | 000 或 302 |
| `api.github.com`（查 release 元数据） | 正常 | 直连即可，用于查 tag 和 asset 大小 |

**关键坑**：Windows 侧到镜像比 WSL 侧慢约 100 倍（实测 25 KB/s vs 325 KB/s）。所以**大文件一律先在 WSL 下载，再 copy 到 Y 盘**（走 `\\wsl.localhost`，215 MB 只需 0.56 秒）：

```bash
# WSL 侧下载
cd /home/lzy959 && curl -sSL --retry 4 -o strata-windows-x64.zip \
  "https://ghfast.top/https://github.com/Niko1221/Strata/releases/download/v0.1.44.1/strata-windows-x64.zip"

# Windows 侧复制到 Y 盘
/mnt/c/Windows/System32/cmd.exe /c 'copy /Y \\wsl.localhost\Ubuntu-24.04\home\lzy959\strata-windows-x64.zip Y:\Strata-prebuilt\'
```

### 3.1 获取 Strata 源码

```bash
# WSL 侧下载 main 分支快照（22.6 MB，约 10 分钟 @ 37 KB/s）
cd /home/lzy959 && curl -sSL -C - --retry 5 -o Strata-main.zip \
  https://codeload.github.com/Niko1221/Strata/zip/refs/heads/main

# Windows 侧解压到 Y 盘并改名
/mnt/c/Windows/System32/cmd.exe /c 'tar -xf \\wsl.localhost\Ubuntu-24.04\home\lzy959\Strata-main.zip -C Y:\ && move Y:\Strata-main Y:\Strata'
```

Windows 自带 `bsdtar`，不需要装解压工具。

### 3.2 预置引擎包（绕开 GitHub 慢速）

引擎包有三个，**要选对**：

| 文件 | 大小 | archs | 用途 |
|---|---|---|---|
| **`strata-windows-x64.zip`** | 148 MB | `[75, 86, 89, **120**]` | ✅ CUDA 13，**原生支持 RTX 5080 的 sm_120** |
| `strata-windows-x64-cuda12.zip` | 215 MB | `[60,61,70,75,80,86,89]` | CUDA 12，sm_120 只能靠 PTX JIT 兜底 |
| `strata-windows-x64-hip.zip` | 604 MB | — | AMD 显卡 |

```bash
# WSL 侧下载 CUDA 13 版（155 MB @ 2.15 MB/s，约 75 秒）
cd /home/lzy959 && curl -sSL --retry 4 -o strata-windows-x64.zip \
  "https://ghfast.top/https://github.com/Niko1221/Strata/releases/download/v0.1.44.1/strata-windows-x64.zip"

# 复制到本地 prebuilt 目录
/mnt/c/Windows/System32/cmd.exe /c 'mkdir Y:\Strata-prebuilt 2>nul & copy /Y \\wsl.localhost\Ubuntu-24.04\home\lzy959\strata-windows-x64.zip Y:\Strata-prebuilt\'
```

### 3.3 预置 llama.cpp 源码（这一步最容易卡住）

**问题**：`setup.py:5478` 在获取引擎**之前**会**无条件**调用 `get_llama_cpp()`，即使你用的是预编译引擎，它也会先去 GitHub 拉 llama.cpp 源码（39.6 MB）——以本机 25 KB/s 的速度，这一步就能卡住整晚。

**官方后门**（`setup.py:1969-1971`）：只要这两个条件满足，它就完全跳过下载——

- `third_party/llama.cpp/ggml/CMakeLists.txt` 存在
- `third_party/llama.cpp/gguf-py/` 是目录

**做法**：把 zip 放到位并**打上 `.done` 标记**（`download()` 看到标记就直接跳过联网校验，`setup.py:1613`），剩下的解压交给 setup 自己做（它会自动跳过 `tools/ui/`，规避 Windows 260 字符路径限制）：

```bash
# WSL 侧下载源码（39.6 MB @ 620 KB/s）
cd /home/lzy959 && curl -sSL --retry 4 -o llama.cpp-3cf0325.zip \
  "https://gh-proxy.com/https://github.com/ggml-org/llama.cpp/archive/3cf03257f219afbe7334045ff7c6a06ac68c627d.zip"

# 复制 + 打标记（zip 文件名必须与 setup 期望的一致）
/mnt/c/Windows/System32/cmd.exe /c 'mkdir Y:\Strata\third_party 2>nul & copy /Y \\wsl.localhost\Ubuntu-24.04\home\lzy959\llama.cpp-3cf0325.zip Y:\Strata\third_party\ & echo done>Y:\Strata\third_party\llama.cpp-3cf0325.zip.done'
```

> commit 值取自 `setup.py` 的 `LLAMA_CPP_COMMIT`。

### 3.4 执行安装

```bash
/mnt/c/Windows/System32/cmd.exe /c 'cd /d Y:\Strata & set "STRATA_PREBUILT_URL=Y:\Strata-prebuilt" & set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" & START-HERE.bat --model IQ2_XS --data-dir Y:\Strata-data --source modelscope --prebuilt Y:\Strata-prebuilt --yes'
```

各参数的作用（都是踩过坑才配齐的）：

| 参数 | 作用 | 备注 |
|---|---|---|
| `--model IQ2_XS` | 规格 | |
| `--data-dir Y:\Strata-data` | 模型与打包数据存放位置 | 放 Y 盘，别碰 C 盘 |
| `--source modelscope` | 模型走 ModelScope 下载 | 实测 **33.8 MB/s**，远超 HuggingFace |
| **`STRATA_PREBUILT_URL=Y:\Strata-prebuilt`** | **环境变量**指定本地引擎目录 | ⚠️ 必须用环境变量：`setup.py:5376` 直接读它，**命令行 `--prebuilt` 在这一分支会被忽略** |
| `--prebuilt Y:\Strata-prebuilt` | 命令行同义参数 | 留着做双保险 |
| `PIP_INDEX_URL=清华源` | pip 装 CUDA 库加速 | nvidia-cublas / nvidia-cuda-runtime 约 0.4 GB |
| `--yes` | 所有问题取推荐答案 | 非交互 |

setup 选择的参数：**context 65536**、**KV cache int8**、images off、speed projection off。

### 3.5 安装流程与耗时

```
Step 1  检查 PC                              —— 秒级
Step 2  选择（model/size/context/vision）     —— 秒级
Step 3  Python 依赖                           —— 已就绪
Step 4  引擎：llama.cpp 源码（本地已就绪）+ 引擎解包 + CUDA 库
Step 5  下载模型（63.35 GB，分两片）           —— 约 30 分钟 @ 33.8 MB/s
Step 6  准备：专家打包 packs/iq2_xs；MTP 草稿层下载 + 量化
Step 7  写配置与启动脚本 strata-iq2_xs.json / run-iq2_xs.bat
```

Step 4 的关键输出：

```
[ok] llama.cpp source already downloaded
[ok] llama.cpp 3cf0325 (gguf-py, ggml, mtmd)
[ok] Strata engine copied
[!]  could not get a SHA-256 ... so it was NOT verified. Installing it as it is.
[ok] ready-made engine 0.1.44.1 for sm_75, sm_86, sm_89, sm_120 (CUDA 13.0)
[ok] NVIDIA CUDA libraries (cuBLAS, CUDA runtime; ~0.4 GB) installed
[ok] engine: Y:\Strata\engine\strata.exe
```

（`.done` 标记存在时，本地 prebuilt 不做 SHA-256 校验——这是有意的：校验要联网去 GitHub 取摘要，我们正是要避开它。）

Step 5/6 的关键输出：

```
checking ...-00001-of-00002.gguf (SHA-256, 39.2 GB) ... [ok] downloaded
checking ...-00002-of-00002.gguf (SHA-256, 28.8 GB) ... [ok] downloaded
[ok] model files present
[ok] model prepared: Y:\Strata-data\packs\iq2_xs        (1079 tensors, arena 1.43 GiB)
[ok] MTP draft layer: Y:\Strata-data\mtp\rt             (5.213 GB BF16 → 0.889 GB)
[ok] start script: run-iq2_xs.bat
```

### 3.6 首次启动

```
[strata] experts loaded: 33.02 GiB at 3.27 GiB/s (52 s so far)
[strata] filling the GPU's expert cache (2756 experts, 3.71 GiB of VRAM) ...
ready: http://127.0.0.1:8080/v1
```

首次启动会把 35~55 GB 载入内存并锁一部分给显卡，**期间整机可能卡顿 1~3 分钟，属正常**。

---

## 四、踩坑清单（本次最值钱的部分）

1. **GitHub 直连不可用**：Windows 侧 25 KB/s、WSL 侧 40 KB/s。必须走镜像。
2. **镜像要按"持续速度"选**：`ghfast.top` 前 12 秒能冲到 2.7 MB/s，随后掉到 ~9 KB/s；`gh-proxy.com`/`ghfast.top` 的**持续**速度约 320~335 KB/s。
3. **Windows 侧到镜像比 WSL 侧慢约 100 倍**：大文件一律 WSL 下载 → `copy` 到 Y 盘（走 `\\wsl.localhost`，215 MB 仅 0.56 秒）。
4. **setup.py 即使用预编译引擎，也会先拉 llama.cpp 源码**（`setup.py:5478` 无条件调用）。绕过方式：预置 zip + `.done` 标记（见 3.3）。
5. **`--prebuilt` 在 CUDA 引擎分支被忽略**：`setup.py:5376` 读的是环境变量 `STRATA_PREBUILT_URL`。必须**同时**设置环境变量。
6. **引擎包名要选对**：CUDA 13 用 `strata-windows-x64.zip`（archs 含 120，原生支持 RTX 50 系）；`-cuda12` 那个不含 120。
7. **ModelScope 实际速度 33.8 MB/s**，远高于官方文档写的 11~14 MB/s；且支持 Range 续传。
8. **NTFS/9p 元数据延迟会骗人**：下载过程中 `dir` 会显示 `.part` 为 **0 字节**，但磁盘剩余空间在实打实地减少。**判断进度要用剩余空间**，别信 `dir`：
   ```bash
   powershell.exe -NoProfile -Command "'{0:N1} GB' -f ((Get-PSDrive Y).Free/1GB)"
   ```
   （另：PowerShell 的 `Measure-Object -Property Length` 对混合对象会报 `PSArgumentException`，加 `-File` 才准。）
9. **WSL 侧无法写 `/mnt/y`**（沙箱限制），但**读可以**；所有写操作走 Windows 侧命令。
10. **中断不要紧**：`download()` 用 `.part` + HTTP Range 续传；shard 1 下完后带 `.done` 标记，重跑直接跳过。当晚暂停在 85%，次日重跑一条命令就接上了。

---

## 五、部署后：QQ 机器人迁移

### 5.1 改配置（两行）

`~/qq-bot/config.json`：

```json
"ollama_base": "http://127.0.0.1:8080/v1",     // 原 http://127.0.0.1:11434/v1
"model":       "qwen3.8-flash-next-iq2_xs",    // 原名 qwen3.8-27b-local:latest
```

改前已备份为 `config.json.bak-20261016-2313`。**机器人代码一行没改**——`official_bot.py` 走的就是 OpenAI 兼容协议。

### 5.2 `svc.sh` 适配新后端

原脚本用 Ollama 的 `/api/ps` 查显存，Strata 没有该接口。改为**优先读 Strata**、Ollama 作回退：

```bash
STRATA_URL="${STRATA_URL:-http://127.0.0.1:8080}"
# 显存取自 /v1/status 的 machine.gpu.used_mib
```

`status` 现在长这样：

```
[napcat]   未运行
[official] 运行中 PID 15271
[strata]   运行中，模型 qwen3.8-flash-next-iq2_xs（显存 13.4 GiB，API http://127.0.0.1:8080/v1）
```

### 5.3 给机器人加健康守护

部署当晚发现过两次异常：一次腾讯网关请求超时导致 botpy 抛 `TypeError` 崩溃；一次进程**还在 `ps` 里但已卡死在启动阶段**（不写日志、无 TCP 连接）的"活僵尸"。

于是加了 `~/qq-bot/watchdog.sh` + systemd user timer（每 2 分钟一次）：

- **健康判据**：① 进程存在；② 日志里最后一次「正在连接网关」之后出现过「机器人已上线」（这条专治"活僵尸"）；
- **暂停开关**：`svc.sh stop official` 会创建 `.watchdog-paused`，守护随即停手；`start` 会移除它；
- **已开 linger**（`loginctl enable-linger`），无登录会话也照跑。

故障演练已验证：`kill -9` 后在 20 秒内被自动拉起并成功上线。

### 5.4 出图与聊天的显存互斥（已解决）

`image_gen.py` 的 `release_chat_model()` 原本靠 Ollama 的 `/api/generate`（`keep_alive: 0`）卸载聊天模型来腾显存。
**Strata 没有这个 API**，所以原机制失效——16.3 GB 显存装不下"Strata 13.1 GB + 生图峰值 9.4 GB"。

解决办法（2026-10-16 完成并实测）：

1. **新增切换脚本** `~/qwen-image/bin/switch.sh`：
   - `switch.sh image` —— 停 Strata、起 ComfyUI（实测 13 s）
   - `switch.sh chat` —— 停 ComfyUI、起 Strata（实测 42 s，含加载 39 GB）
   - `switch.sh status` / `stop-all`
   - ⚠️ **关键坑**：Strata 的服务前端（`serve/server.py`）带 `auto_load`，**只杀引擎 `strata.exe` 不够**——
     下次请求它会自动把模型重新拉回来抢显存。必须按 8080 的监听 PID 连服务前端一起停（`taskkill /F /T /PID`）。
   - ⚠️ 另一个坑：**不能从 WSL 用 `cmd /c start` 启动 Strata**（WSL 启动 cmd 时当前目录是 UNC 路径
     `\\wsl.localhost\...`，`start` 会报"系统找不到文件 \\"）。改用 PowerShell
     `Start-Process -FilePath 'Y:\Strata\run-iq2_xs.bat' -WorkingDirectory 'Y:\Strata'`。
2. **`image_gen.py` 支持双后端**：Strata 在跑 → 调 `switch.sh image` 停掉它并留下 `.strata-stopped` 标记；
   下一次聊天请求时 `release_image_model()` 清除标记并在**后台**重启 Strata（不阻塞回复）。
   Ollama 路径保持不变（它本就按需加载，无需恢复）。

实测结果：出图 136 KB 成功；双向切换 13 s / 42 s；代码逻辑（检测 → 停 → 标记 → 恢复）四项全通过。


---

## 六、验证结果

### 6.1 服务状态（`GET /v1/status`）

```
模型: qwen3.8-flash-next-iq2_xs   引擎: 0.1.44.1   已加载: True
上下文: 65536   KV: int8
显存: 13865/16303 MiB   内存: 46.7/47.1 GiB
```

### 6.2 速度对照

| 指标 | 旧方案 Ollama | 新方案 Strata | 提升 |
|---|---|---|---|
| 模型规模 | 27B dense | **125B MoE** | 4.6× |
| 生成速度 | 4.15 tok/s | **70.2 ~ 72.9 tok/s** | **≈17×** |
| 预填充 | 28 tok/s | **416 tok/s** | ≈15× |
| 显存占用 | 15.8 GB（超售） | 13.9 GB（专家缓存命中率 80.6%） | — |
| 机器人端到端（一句话） | 16.0 s | **2.4 s** | ≈6.7× |

专家缓存：2756 个专家 / 3.71 GiB 显存；缓存命中 80.6%，其余约 10~12% 的路由专家经 PCIe 由 CPU 并行计算。

### 6.3 端到端

```bash
bash ~/qq-bot/run.sh --selftest
# INFO  HTTP Request: POST http://127.0.0.1:8080/v1/chat/completions "HTTP/1.0 200 OK"
# [selftest] 2.4s  ...
```

---

## 七、日常运维手册

```bash
# —— 模型侧（Windows）——
Y:\Strata\run-iq2_xs.bat          # 启动模型（或 START-HERE.bat）；关掉它的窗口即停止
curl http://127.0.0.1:8080/v1/status     # 服务状态
# 浏览器界面：http://127.0.0.1:8080

# —— 机器人侧（WSL）——
bash ~/qq-bot/svc.sh status               # 一键看机器人 + 后端
bash ~/qq-bot/svc.sh start official       # 启动（并恢复守护）
bash ~/qq-bot/svc.sh stop official        # 停止（并暂停守护）
tail -f ~/qq-bot/logs/watchdog.log        # 看守护干过什么
systemctl --user list-timers qqbot-watchdog.timer   # 看下次巡检

# —— 对外接口 ——
# OpenAI 兼容：   http://127.0.0.1:8080/v1
# Anthropic 兼容：http://127.0.0.1:8080/v1/messages
# 模型名随便填，API key 随便填；WSL 侧可用 127.0.0.1 直连（mirrored 网络）
```

**回滚方案**（要退回 Ollama 时）：

1. 恢复 `~/qq-bot/config.json.bak-20261016-2313`；
2. `bash ~/qq-bot/svc.sh start ollama`；
3. 关掉 Strata 的模型窗口，显存与内存即释放。

---

## 八、关键路径速查

| 内容 | 路径 |
|---|---|
| Strata 程序 | `Y:\Strata\` |
| 本地预置引擎包 | `Y:\Strata-prebuilt\` |
| llama.cpp 源码 | `Y:\Strata\third_party\llama.cpp\` |
| 引擎可执行文件 | `Y:\Strata\engine\strata.exe` |
| 模型文件（63.35 GB） | `Y:\Strata-data\models\IQ2_XS\` |
| 专家打包 | `Y:\Strata-data\packs\iq2_xs\` |
| MTP 草稿层 | `Y:\Strata-data\mtp\rt\` |
| 运行配置 | `Y:\Strata\strata-iq2_xs.json` |
| 启动脚本 | `Y:\Strata\run-iq2_xs.bat` |
| 机器人配置 | `~/qq-bot/config.json` |
| 机器人守护 | `~/qq-bot/watchdog.sh` + `~/.config/systemd/user/qqbot-watchdog.{service,timer}` |
