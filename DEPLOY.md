# 本机 AI 环境部署手册

覆盖三条负载：**DSH**（Agent 宿主）、**本地大模型**（Ollama + Qwen）、**Qwen-Image-2.1**（本地生图）。

> **本文的性质**：第 1、2 节基于**本机实际部署记录与实测数据**。
> 第 4 节（Qwen-Image-2.1）初稿按「未验证」编写，**但核实后发现该模型已于 2026-09-21
> 在本机部署完成并全部验收通过**，因此第 4 节已改为结论摘要 + 指向完整实测文档
> [`QWEN-IMAGE-DEPLOY.md`](QWEN-IMAGE-DEPLOY.md)。版本号与路径均为 2026-09-21 实测值。

---

## 0. 本机基线

| 项目 | 实测值 |
|---|---|
| 系统 | WSL2（`6.6.87.2-microsoft-standard-WSL2`），mirrored 网络 |
| GPU | NVIDIA RTX 5080，**16303 MiB**，驱动 616.92（架构 sm_120 / Blackwell） |
| 磁盘 | `/` 1007 G，已用 94 G，**剩余 863 G** |
| Node / pnpm / npm | v22.22.1 / 10.32.1 / 10.9.4 |
| Ollama | **0.32.15**（用户态安装，无 root） |
| 本地模型 | `qwen3.8-27b-local:latest`（Q4_K_M，17.44 GB） |
| DSH | 源码检出 `~/project/deepseek-harness`，`@deepseek-ai/dsh-root 0.1.6-alpha.1`，已 build |
| Python venv | `~/.venvs/qqbot`（httpx / websockets / botpy）、`~/.venvs/tools`（zstandard） |
| 生图环境 | **已部署并验收**：`~/qwen-image/`（ComfyUI + int8 权重 17 GB + torch 2.14.0+cu130） |

---

## 1. 三条负载抢一张 16 GB 卡 ★

**这是部署前必须理解的约束**，否则会出现"莫名其妙的慢"和 OOM。

### 1.1 显存预算

| 负载 | 占用 | 依据 |
|---|---|---|
| Qwen3.8-27B（Ollama，32768 上下文，KV q8_0） | **15790 / 16303 MiB** | 本机实测（`~/qwen/README.md`） |
| Qwen-Image-2.1（7B，**int8 权重**，ComfyUI 分阶段换出） | 2K 峰值约 **14.1 GiB** | 本机实测（见 `QWEN-IMAGE-DEPLOY.md`） |
| DSH Web / QQ 机器人进程本身 | ≈ 0（不碰 GPU） | 它们只是 HTTP 客户端 |

结论：**27B 大模型和生图模型无法同时常驻**。DSH 和 QQ 机器人本身不吃显存，
但它们调用本地模型时会占用——真正冲突的是**大模型和生图模型**。

### 1.2 切换流程（生图前腾显存）

```bash
# 1. 只卸载模型（保留 ollama 服务，下次请求重新加载 28~38 秒）
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3.8-27b-local:latest","keep_alive":0}' >/dev/null
sleep 4

# 2. 确认显存真的回来了
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
# 期望看到 used ≈ 几百 MiB

# 3. 用完生图想切回大模型：什么都不用做，下一次请求会自动加载
```

彻底停服务（连进程一起停，`svc.sh` 会先请求卸载再停）：

```bash
bash ~/qq-bot/svc.sh stop ollama     # 停模型服务并释放显存
bash ~/qq-bot/svc.sh start ollama    # 需要时再起
```

> ⚠️ `serve.sh` 里设了 `OLLAMA_KEEP_ALIVE=-1`（模型永久常驻）。这对"怕冷的聊天机器人"
> 是对的，但意味着**它不会自己让出显存**，必须用上面的 `keep_alive:0` 显式卸载。

---

## 2. DSH 部署

### 2.1 两条路线

| 路线 | 命令 | 适用 |
|---|---|---|
| **npm**（推荐先用） | `npx @deepseek-ai/dsh web` | 只想用，不想碰源码 |
| **源码** | `git clone … && pnpm install && pnpm run build && pnpm dsh web` | 要改代码 / 用最新提交 |

默认在 `http://127.0.0.1:3080` 启动 Web UI。通过 SSH 启动时只打印宿主机 URL，
因为本地转发地址由 SSH 客户端持有；加 `--no-open` 可只起服务不开浏览器。

**前置要求**：Node.js `^22.19.0 || >=24.0.0`（`package.json:9`）。本机 v22.22.1 ✅

### 2.2 本机现状（已经装好了）

| 路径 | 作用 |
|---|---|
| `~/project/deepseek-harness` | 源码检出，`node_modules` 与 `apps/web/dist` 均已构建 |
| `~/.npm-global/bin/dsh` | npm 全局安装的 `dsh` |
| `~/.local/bin/dsh-web` | 从**源码**启动 Web GUI（等价 `cd 仓库 && pnpm dsh web`，带端口占用检查） |
| `~/.local/bin/dsh-dev` | 从源码跑任意子命令，如 `dsh-dev --profile headless "任务"` |
| `~/.local/bin/dsh-web-open` | 启动后自动抓取带 token 的链接、复制到剪贴板并开浏览器 |
| `~/.dsh/` | `DSH_HOME`：`settings.yaml`、`.credentials.yaml`(600)、`sessions/`、`attachments/` |

三个 wrapper 都支持 `DSH_REPO` 环境变量指定仓库位置。

### 2.3 从零部署（换机器时照这个走）

```bash
# 1. Node.js（本机已有 v22.22.1）
node -v

# 2A. 最省事：npm 直跑
npx @deepseek-ai/dsh web --no-open

# 2B. 或从源码
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build          # 准备仓库产物
pnpm dsh web            # 直接用已构建产物，不会重新构建
```

> **本机注意**：`github.com` 在这台机器上被 Steam++ 写进了 hosts 并做 SNI 代理，
> `git clone` 慢（实测握手 10 秒）。HTTPS 直连会报
> `SSL certificate problem: unable to get local issuer certificate`，**加 `-k` 即通**
> ——详见第 3.1 节的网络实况表。

### 2.4 把 DSH 接到本机大模型 ★

DSH 不绑定任何特定厂商。本机已有 OpenAI 兼容端点 `http://127.0.0.1:11434/v1`，
**不用 API key、不花钱、不出网**。

**做法一：图形界面**（推荐）

**设置 → 模型 → 添加自定义提供方**，填：

| 字段 | 值 |
|---|---|
| Provider ID | `local-ollama`（小写；**永久不可改**，改名要新建一个） |
| 显示名称 | 本地 Ollama |
| API 地址 | `http://127.0.0.1:11434/v1` |
| API 协议 | `openai-completions` |
| API 密钥 | 随便填（Ollama 不校验，但字段不能空） |
| 模型 ID | `qwen3.8-27b-local:latest` |

也可以点**获取可用模型**让它调 `GET /models` 自动列出（Ollama 支持这个端点）。

**做法二：直接写 `~/.dsh/settings.yaml`**

```yaml
llm-pi-ai:
  providers:
    local-ollama:
      apiKeyEnv: OLLAMA_API_KEY
      api: openai-completions
      baseURL: http://127.0.0.1:11434/v1
      compat:
        supportsDeveloperRole: false     # Ollama 不认 role: "developer"
        maxTokensField: max_tokens       # Ollama 只认 max_tokens
      models:
        - id: qwen3.8-27b-local:latest
          reasoningEfforts:
            off: none                    # ★ 关键，见下
            high: high
```

**关键点一：必须让 `off` 带上值。** `providers.zh.md:122` 明确写了：
留空的 `off` 什么都不发送。而本机模型是**「不明确关闭就会思考」**的类型
——`~/qq-bot/PLAN.md` 实测过：`think:false`、`chat_template_kwargs`、提示词 `/no_think`
**全部无效**，只有显式发送 `reasoning_effort: "none"` 才能关掉思考。

所以 `off: none` 这一行不是可选项：留空的话 DSH 会把 `<think>` 推理块也算进输出，
**又慢又占上下文**。

**关键点二：`compat` 那两个开关。** DSH 会按 OpenAI 的规范发请求，但很多兼容网关
至少会拒绝其中一样（`providers.zh.md:144-160`）。Ollama 会拒绝以 `developer`
角色承载的 system prompt，也只认 `max_tokens`——所以上面两个开关都要写。

**关键点三：性能预期。** 本机 27B 解码只有 **7.6 tok/s**（`~/qwen/README.md`）。
DSH 的 Agent 循环一次任务动辄几千 token，**用本地模型跑 Agent 会非常慢**
（一句话回复 2.5–6.7 秒，但长任务要几分钟到几十分钟）。
实用分工是：**DSH 走云端 API 做主力，本地模型留给短对话和离线场景**。

### 2.5 DSH 排障

| 现象 | 排查 |
|---|---|
| `127.0.0.1:3080` 打不开 | 端口被旧实例占用（`ss -ltn \| grep 3080`）；或没加 `--no-open` 时服务其实还没起来 |
| 页面要求 token | Web UI 的 URL **带 `?token=…`**，用 `dsh-web-open` 会自动抓取并打开 |
| `MISSING_CREDENTIAL` | 在模型页保存密钥，或提供被 `apiKeyEnv` 引用的环境变量 |
| `UNKNOWN_MODEL` | 选一个已配置的模型，或把缺失的模型加进提供方 |
| 模型探测返回 401 | 密钥不对；模型发现走 `GET /models` |
| 密钥地址都对但每个请求都被拒 | 先设 `compat.supportsDeveloperRole: false` 与 `compat.maxTokensField: max_tokens` |
| 只有推理模型失败 | 同上，`developer` 角色被拒 |
| 需要走代理 | 见 `docs/user/guide/network-proxy.zh.md`；本机 Steam++ 会干扰，必要时做直连例外 |

---

## 3. 本地模型部署（Ollama + Qwen3.8-27B）

本机已完成部署，`~/qwen/README.md` 是完整记录。这里保留**可复现的步骤和踩坑**，
换机器或换模型时照做。

### 3.1 先认清这台机器的网络（关键，别再踩）

| 现象 | 真相 |
|---|---|
| WSL 里 `github.com` 解析到 `127.0.0.1` | Windows hosts 被 **Steam++ / Watt Toolkit** 写入（`# Steam++ Start` 段），mirrored 网络下 WSL 共用该解析 |
| `curl https://github.com` 报 `(60) unable to get local issuer certificate` | Steam++ 的 `Accelerator.exe` 监听 `0.0.0.0:443` 做 SNI 代理，MITM 根证书只在 Windows 证书store → **加 `-k` 即通** |
| GitHub 通了但极慢 | release 资产直连仅 **60–75 KB/s** |
| `registry.ollama.ai` 官方模型源 | 单流 275 KB/s → 17.44 GB 要 18 小时，**不可行** |
| Docker Hub | `registry-1.docker.io` **不可达** → vLLM/NapCat 的容器路线不通 |
| ✅ 清华 conda-forge | Ollama 二进制 0.61 GB，国内满速 |
| ✅ hf-mirror.com | GGUF 源，12 流峰值约 15 MB/s，17.44 GB 约 1 小时 |
| ✅ 清华 PyPI | 工具依赖 |

**本次实测（2026-09-21）连通性**：

| 目标 | 结果 |
|---|---|
| `hf-mirror.com` | ✅ 200，0.9 s |
| `modelscope.cn` | ✅ 302（正常跳转），0.2 s |
| `pypi.tuna.tsinghua.edu.cn` | ✅ 301，0.3 s |
| `pypi.org` | ✅ 200，3.3 s |
| `huggingface.co` | ✅ 200，2.5 s（现在直连也通，但大文件仍建议走镜像） |
| `github.com` | ⚠️ 200，但握手 **10 s** |
| `download.pytorch.org` | ❌ **超时（000）** —— 装 PyTorch 时这条要绕（见 4.3） |

### 3.2 安装运行时：清华 conda-forge 镜像

不需要 conda、不需要 root、不需要 nvcc：

```bash
curl -L -o ~/qwen/models/ollama-cuda.conda \
  https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/linux-64/ollama-0.32.15-cuda_130h0c8c962_0.conda
```

`.conda` 是「zip 包着 tar.zst」，用 Python 解（先 `pip install zstandard`）：

```python
import zipfile, zstandard, tarfile, os
z = zipfile.ZipFile('ollama-cuda.conda')
dctx = zstandard.ZstdDecompressor()
pkg = [n for n in z.namelist() if n.startswith('pkg-') and n.endswith('.tar.zst')][0]
with z.open(pkg) as fh, dctx.stream_reader(fh) as r:
    with tarfile.open(fileobj=r, mode='r|') as tf:
        tf.extractall(os.path.expanduser('~/qwen/runtime/ollama'), filter='data')
```

得到 `bin/ollama` + `lib/ollama/cuda_v13/`。

### 3.3 下载权重：hf-mirror 多线程分块

单连接会被限速到 ~250 KB/s，**必须多连接分块**：

```bash
export HF_ENDPOINT=https://hf-mirror.com        # 让 transformers/huggingface_hub 也走镜像
python3 ~/qwen/tools/fetch.py \
  "https://hf-mirror.com/bartowski/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-Q4_K_M.gguf" \
  "$HOME/qwen/models/Qwen3.8-27B-Q4_K_M.gguf" 12
```

`~/qwen/tools/` 里的配套脚本：`fetch.py`（分块下载）、`finish.py`（把被限速的块再切 8 段补齐）、
`assemble.py`（离线拼装）、`gguf_check.py`（校验 GGUF 头与元数据）、`stop.sh`（安全停止并保留分块）。

### 3.4 导入 Ollama

```bash
bash ~/qwen/bin/serve.sh
ollama create qwen3.8-27b-local -f ~/qwen/models/Modelfile
```

### 3.5 `serve.sh` 的六个环境变量（逐个解释）

```bash
export OLLAMA_FLASH_ATTENTION=1        # 开 FlashAttention，省显存、提速
export OLLAMA_KV_CACHE_TYPE=q8_0       # KV cache 量化到 8 bit，32768 上下文只吃约 1 GB
export OLLAMA_NUM_PARALLEL=1           # 单并发：16 GB 卡放不下两份 KV
export OLLAMA_MAX_LOADED_MODELS=1      # 同时只常驻一个模型（生图时必须让位）
export OLLAMA_CONTEXT_LENGTH=32768     # 上下文长度；prefill 线性增长，别填满
export OLLAMA_KEEP_ALIVE=-1            # 永久常驻，避免每次空闲后重加载 28~38 秒
```

`serve.sh` 是幂等的：先探 `/api/version`，已在跑就直接返回；启动用
`setsid nohup … &`（**不是** `nohup … &`——后者挡不住进程组 SIGTERM，调用方一退后台就死）。

### 3.6 验证与性能基线

```bash
curl -s http://127.0.0.1:11434/api/version
curl -s http://127.0.0.1:11434/api/ps | python3 -c "
import sys,json;d=json.load(sys.stdin)
[print(f\"VRAM {m.get('size_vram',0)/2**30:.2f} GiB / 总 {m['size']/2**30:.2f} GiB\") for m in d.get('models',[])]"
```

| 指标 | 健康值（本机实测） |
|---|---|
| VRAM | **≈ 13–14 GiB**（掉到 5 GiB 以下 = 整机跑 CPU 了，见 3.7） |
| 生成速度 | 7.6 tok/s |
| Prefill | 97.7 tok/s |
| 单条回复 | 2.4–6.7 s（模型已常驻） |
| 冷加载 | 28–38 s |

### 3.7 本地模型排障

**① 回复突然变成 30–60 秒一条**

看 `/api/ps`：如果 `VRAM` 只有几百 MiB、总大小仍是 17.65 GiB，且日志出现
`load_tensors: offloaded 0/66 layers to GPU` —— 加载那一刻可用显存不够，Ollama 整机跑 CPU 了。
修复（等 GPU 空出来再执行）：

```bash
M=qwen3.8-27b-local:latest
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\":\"$M\",\"keep_alive\":0}"   # 卸载
sleep 4
curl -s http://127.0.0.1:11434/api/generate \
  -d "{\"model\":\"$M\",\"prompt\":\"hi\",\"stream\":false,\"think\":false,\"keep_alive\":-1,\"options\":{\"num_predict\":1}}" >/dev/null
grep -a "offloaded" ~/qwen/logs/ollama.log | tail -1    # 应显示 53/66 layers
```

**② 想再快一点**（按收益排序）

1. 关掉 Windows 侧占显存的东西（浏览器硬件加速、游戏、视频渲染）——加载时可用显存越多，上卡的层越多；
2. 换 `IQ4_XS`（15.48 GB，比 Q4_K_M 小约 2 GB），几乎能全量上卡；
3. `OLLAMA_CONTEXT_LENGTH=16384` 省约 0.5 GB KV；
4. 仍不够就换更小的模型。

**③ `pkill` 把自己杀了**（踩过三次）

`pkill -f 'ollama serve'` / `pkill -f 'bot.py'` **会匹配到正在执行这条命令的 shell 自己**。
解决：用方括号写法 `pkill -f '[o]llama serve'`，或用 `bash ~/qq-bot/svc.sh stop ollama`。

---

## 4. Qwen-Image-2.1 部署（**已在本机跑通**，完整记录见另文）

> ✅ **更正**：本节初稿写作「未在本机验证」，那是错的——该模型已于 **2026-09-21**
> 在本机部署完成并全部验收通过。**完整的安装步骤、7 个真实踩坑与性能数据在
> [`QWEN-IMAGE-DEPLOY.md`](QWEN-IMAGE-DEPLOY.md)**。本节只保留结论摘要与选型判断，
> 避免同一事实在两处各自漂移；4.1 之后的安装细节若有出入，**一律以另文为准**。

### 4.0 实测结论摘要

| 项目 | 实测值 |
|---|---|
| 采用路线 | **ComfyUI + 官方 int8 权重**（不是 Diffusers，也不是 vLLM） |
| 权重 | DiT int8 7.26 GB + 文本编码器 int8 9.35 GB + VAE 0.68 GB = **17.28 GB** |
| 1024² / 25 步 | 采样 29 s；含加载首图 65 s |
| **2048² / 25 步（原生 2K）** | 采样 **75 s**；含加载 85 s |
| 显存峰值 | 2K 约 **14.1 GiB** |
| RGBA 透明 | ✅ 实测 25.9% 全透明像素 |
| torch | **2.14.0+cu130**，`arch_list` 含 `sm_120`，bf16 矩阵乘法通过 |

**三个推翻直觉的结论**（论证见另文）：

1. **16 GB 卡能跑满原生 2K** —— 前提是把 Windows 侧显存占用压下来，而不是必须降级 offload。
2. **必须用 int8 权重**：文本编码器 bf16 一个组件就有 **17.5 GB**，**单独超过 16 GB 显存**。
   可行原因是 ComfyUI 在「文本编码 → 采样 → VAE 解码」三阶段之间自动换出权重，
   峰值只按单阶段最大者（≈9.4 GB）计算，而不是三者之和（17.3 GB）。
3. **ModelScope 比 hf-mirror 快约 25 倍**（50.9 MB/s vs 2.0 MB/s），权重源应优先 ModelScope。

### 4.1 是什么

| 项目 | 值 |
|---|---|
| 发布 | 2026-09-20，与 Diffusers / ComfyUI / vLLM-Omni / SGLang **同日**支持 |
| 参数 | 视觉生成组件 **7B**，32 层单流 DiT（MMDiT 架构） |
| 输出 | **原生 2K**（2048×2048 直出，非放大）；**原生 RGBA 透明通道** |
| 编辑 | 单次最多 **10 张参考图**；生成与编辑同一个 checkpoint |
| 默认步数 | 40 |
| 许可证 | **Qwen Research License Agreement** —— 商用前必须读 LICENSE |

原生 RGBA 是它最大的差异点：贴图、logo、图标、商品抠图**出图即带 alpha**，
不需要抠图模型和边缘修补。

### 4.2 路线选型

| 路线 | 适合 | 注意 |
|---|---|---|
| **Diffusers** | Python 开发者、单机实验 | 官方示例最短，但要自己管显存 |
| **ComfyUI** | 可视化拖拽、复用工作流 | 权重与节点版本要匹配同套模板 |
| vLLM-Omni / SGLang | API 服务、批量并发 | 参数多，需要压测 |

**本机建议先走 Diffusers 验证基线**，因为 Docker Hub 不可达（vLLM 容器路线不通），
而 ComfyUI 更适合要反复调参的场景。先确认能出图，再决定要不要上 ComfyUI。

### 4.3 Diffusers 部署

**依赖要求**（官方）：PyTorch ≥ 2.4.0、Transformers ≥ 5.17、最新 Diffusers、Accelerate、Pillow。

```bash
python3 -m venv ~/.venvs/qwen-image          # 独立 venv，别污染 qqbot 环境
source ~/.venvs/qwen-image/bin/activate
python -m pip install --upgrade pip
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple 'accelerate' 'pillow'
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple 'torch>=2.4.0'
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple 'transformers>=5.17'
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple 'git+https://github.com/huggingface/diffusers'
```

> ✅ **实测更正（2026-09-21）** —— 这一段初稿的结论是错的：
> 1. `download.pytorch.org` 确实不通，但**根本不需要绕**：清华 PyPI 的默认 wheel
>    就是 `torch 2.14.0+cu130`，**已经支持 Blackwell**，不必加任何 `--index-url`。
> 2. RTX 5080 是 sm_120，装完**仍然必须验证**（下面这段脚本实测通过），
>    否则可能到推理时才报
>    `no kernel image is available for execution on the device`：
>
>    ```bash
>    python -c "
>    import torch
>    print('torch', torch.__version__, '| cuda', torch.version.cuda)
>    print('可用:', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0))
>    a = torch.randn(512, 512, device='cuda'); print('矩阵乘法 ok:', (a @ a).sum().item() != 0)
>    "
>    ```
>
>    如果 `torch.cuda.is_available()` 是 `False` 或矩阵乘法报 kernel 错误，
>    说明装到的构建不支持 sm_120 —— 需要换更新的 torch，或另找 cu128 的 wheel 源。

**最小文生图脚本**（固定种子便于对比）：

```python
import torch
from diffusers import QwenImage21Pipeline

pipe = QwenImage21Pipeline.from_pretrained(
    "Qwen/Qwen-Image-2.1",
    torch_dtype=torch.bfloat16,
)
pipe.enable_model_cpu_offload()          # ★ 16 GB 卡必开，见 4.6

image = pipe(
    prompt='A neon shop sign that reads "QWEN IMAGE 2.1", rainy night, reflections on wet pavement',
    width=2048,
    height=2048,
    num_inference_steps=40,
    generator=torch.Generator("cuda").manual_seed(42),
).images[0]

image.save("qwen21-t2i.png")
```

**下载权重走镜像**（本机实测 hf-mirror 上该仓库存在，HTTP 200）：

```bash
export HF_ENDPOINT=https://hf-mirror.com
# 之后 from_pretrained("Qwen/Qwen-Image-2.1") 会自动走镜像
```

### 4.4 推荐分辨率（别自己乱填长宽）

| 比例 | 尺寸 |
|---|---|
| 1:1 | 2048 × 2048 |
| 4:3 | 2400 × 1792 |
| 3:4 | 1792 × 2400 |
| 3:2 | 2528 × 1696 |
| 2:3 | 1696 × 2528 |
| 16:9 | 2752 × 1536 |
| 9:16 | 1536 × 2752 |

### 4.5 图像编辑与透明图

```python
from PIL import Image

# 单图编辑（多图传列表，上限 10 张）
edited = pipe(prompt="Change the background to a sunset beach",
              image=Image.open("input.png"),
              num_inference_steps=40).images[0]
edited.save("edited.png")
```

透明图**必须在提示词里明确要求**，否则会出成普通背景：

```python
transparent = pipe(
    prompt=("This is an RGBA image with transparency. A cute cartoon dragon sticker. "
            "The image has alpha channel and the background is transparent."),
    width=2048, height=2048, num_inference_steps=40,
).images[0]
transparent.save("dragon-sticker.png")     # 必须存 PNG，JPEG 丢掉 alpha
```

### 4.6 16 GB 单卡的现实预期（**已实测，原估算被推翻**）

官方**没有公布最低显存**，因为它随精度、offload、量化方式大幅变化。
按 7B 参数推算：仅 bf16 权重就约 **14 GB**，再加上文本编码器和 VAE，
**全管线常驻 16 GB 卡基本不可能**。实际做法：

| 策略 | 代价 |
|---|---|
| `enable_sequential_cpu_offload()` | **Diffusers 路线必须用这个**；`enable_model_cpu_offload()` 会直接 OOM（它整组件搬上卡，而编码器 bf16 就 17.5 GB） |
| fp8 量化（vLLM-Omni 支持） | 省显存，质量需自行评估 |
| 先降分辨率和步数做冒烟测试 | 验证环境的最快方式 |

**建议的推进顺序**：先用 `1024×1024` + `num_inference_steps=10` 跑通链路，
再逐步升到官方 2048×2048 / 40 步，同时用 `nvidia-smi -l 1` 观察显存峰值。

### 4.7 可选：ComfyUI 部署

```bash
# GitHub 直连在这台机器不通：实测走 gh-proxy 可用（codeload 亦可），
# 且用单次 -c 绕过 MITM，不要改全局 git 配置
git -c http.sslVerify=false clone https://gh-proxy.com/https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI && pip install -r requirements.txt

# 权重放这里
#   models/diffusion_models/  ← Qwen-Image-2.1 主模型
#   models/text_encoders/     ← 文本编码器
#   models/vae/               ← VAE
```

权重用**官方兼容版** `Comfy-Org/Qwen-Image-2.1`（本机实测 hf-mirror 上存在）。
工作流：ComfyUI 的 **Templates 面板**里选 Qwen-Image-2.1 模板，或从
[workflow_templates](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_qwen_image_2_1_t2i.json) 下载。

> 先把 ComfyUI 更新到最新版——ComfyUI 对该模型是发布当天原生支持。

### 4.8 Qwen-Image 排障

| 现象 | 原因 / 处理 |
|---|---|
| `QwenImage21Pipeline` 导入失败 | Diffusers 版本旧了：从 GitHub 装最新版并重启 Python 环境；注意导入名大小写 |
| 显存不足 | 先 `enable_model_cpu_offload()`，再降分辨率和步数；**不要**删模型组件 |
| 透明图出现灰/白底 | 提示词要同时含 RGBA、alpha channel、transparent background；保存必须是 PNG |
| 想用第三方 GGUF/量化权重 | 可作实验分支，但要核对基座确实是 `Qwen/Qwen-Image-2.1`，并单独验证画质与许可证 |
| 商用 | 许可证是 **Qwen Research License Agreement**，读仓库 `LICENSE` 再决定 |

### 4.9 提示词重写器（可选）

官方另有两个重写权重：`Qwen/Qwen-Image-2.1-PE-T2I`（文生图）、`Qwen/Qwen-Image-2.1-PE-I2I`（编辑），
基于 Qwen3.5-VL 9B，把短描述扩成长提示词。**注意它本身也要占显存**，
16 GB 卡上不要和主管线同时常驻。

---

## 5. 日常启停速查

```bash
# DSH
dsh-web                      # 从源码启动 Web GUI（端口 3080）
dsh-dev --help               # 跑任意 dsh 子命令
dsh-dev web --port 3081      # 换端口
# 或：npx @deepseek-ai/dsh web --no-open

# 本地大模型
bash ~/qwen/bin/serve.sh                   # 启动（幂等）
bash ~/qq-bot/svc.sh status                # 看状态 + 显存
bash ~/qq-bot/svc.sh stop ollama           # 停服务并释放显存
tail -f ~/qwen/logs/ollama.log             # 日志

# 生图（未部署，部署后大致是）
source ~/.venvs/qwen-image/bin/activate
python ~/qwen-image/t2i.py
```

**显存切换口诀**：要生图 → 先 `keep_alive:0` 卸载大模型；要聊天 → 直接发请求，它会自己加载。

---

## 6. 参考来源

- DSH 本机源码：`~/project/deepseek-harness`（`README.zh.md`、`docs/user/guide/providers.zh.md`、`network-proxy.zh.md`）
- 本地模型部署记录：`~/qwen/README.md`（本机实测）与 `~/qwen/bin/serve.sh`
- QQ 机器人侧：`~/qq-bot/OPS.md`（运维排障、`svc.sh` 用法）
- [QwenLM/Qwen-Image-2.1（官方仓库）](https://github.com/QwenLM/Qwen-Image-2.1)
- [Qwen/Qwen-Image-2.1（Hugging Face 模型卡）](https://huggingface.co/Qwen/Qwen-Image-2.1)
- [Comfy-Org/Qwen-Image-2.1（ComfyUI 兼容权重）](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)
- [Qwen-Image-2.1 in ComfyUI（ComfyUI 官方博客，2026-09-20）](https://blog.comfy.org/p/qwen-image-21-in-comfyui-open-weight)
- [Qwen-Image-2.1 开源部署完整指南：Diffusers、ComfyUI 与推理服务](https://news.qiniu.com/archives/1789957408429)
- [Qwen-Image-2.1 on vLLM（部署配方）](https://recipes.vllm.ai/Qwen/Qwen-Image-2.1)
- [Qwen-Image 2.1 - SGLang Documentation](https://docs.sglang.io/cookbook/diffusion/Qwen-Image/Qwen-Image-2.1)
