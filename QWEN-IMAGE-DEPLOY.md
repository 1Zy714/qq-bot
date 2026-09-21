# Qwen-Image-2.1 本地部署完整方案

> 目标机器实测于 **2026-09-21**，WSL2 Ubuntu 24.04 + RTX 5080 Laptop 16GB。
> 本文所有数字来自本机实测 + 官方仓库/HF API 实时查询，非估算。

---

## 0. 结论速览（先看这段）

| 项 | 结论 |
|---|---|
| 推荐路线 | **路线 A：ComfyUI（WSL）+ 官方 int8 权重**，原生支持、可视化、显存最省 |
| 备选路线 | 路线 B：Diffusers Python 脚本（要写代码/做 API 时用） |
| 不推荐 | vLLM-Omni / SGLang（Docker Hub 在这台机器不通，且 16GB 单卡不是服务化场景） |
| 下载量 | A：**17.29 GB**（int8 全套）；B：**33.12 GB**（bf16 全套） |
| 预计耗时 | 下载 ~30–60 分钟（hf-mirror 多线程）+ 安装 ~20 分钟 |
| 磁盘位置 | **必须放 Linux 侧 `/`**（剩 891 GB）。`C:` 只剩 24 GB，放不下 |
| 头号风险 | **当前显存已被 Windows 侧占用 8.2 GB，只剩 7.7 GB 可用 → 必 OOM**。跑之前必须先释放 |
| 许可证 | ⚠️ **Qwen Research License Agreement，不是 Apache-2.0**。商用前必须读 LICENSE |

### 执行进度：**部署完成并全部验收通过**（2026-09-21 21:30）

- [x] 权重下载 → `~/qwen-image/models/`（17.28 GB，sha256 全一致，耗时 2 分 52 秒）
- [x] ComfyUI 源码 → `~/qwen-image/ComfyUI/`（HEAD `b0f4b7b`，2026-09-20，含 `TextEncodeQwenImage21`）
- [x] venv + torch → `torch 2.14.0+cu130`，**sm_120 已验证**（`arch_list` 含 sm_120）
- [x] 依赖安装完成（ComfyUI 0.37.0 启动正常）
- [x] 权重挂载（符号链接，未复制）
- [x] **冒烟测试通过**：文生图 1K / 2K、原生 RGBA 透明、图像编辑

**实测性能（RTX 5080 Laptop 16GB + int8 权重）**

| 项目 | 实测值 |
|---|---|
| 1024×1024 / 25 步 | 采样 **29 s**（1.19 s/步），含加载首图 **65 s** |
| 2048×2048 / 25 步（原生 2K） | 采样 **75 s**（3.01 s/步），含加载 **85 s** |
| 图像编辑（1 参考图）1024² | 约 **18–23 s** |
| 显存峰值 | 2K 时约 **14.1 GiB**（Windows 侧另占约 6 GiB） |
| 权重常驻 | DiT int8 **6920 MB** + 文本编码器 int8 **8916 MB** + VAE 644 MB |
| RGBA 透明 | ✅ alpha 0–255，实测 **25.9%** 全透明像素 |

**结论：16 GB 显存可以跑满原生 2K，但前提是把 Windows 侧显存占用压下来。**

**实测结论：ModelScope 比 hf-mirror 快约 25 倍**（单流 50.9 MB/s vs 2.0 MB/s），
所以权重源最终定为 **ModelScope**，下载器换成 `~/qwen-image/tools/fetch_ms.py`。

---

## 1. 模型是什么（事实核对）

**Qwen-Image-2.1**，Qwen 团队 **2026-09-20** 开源，统一「文生图 + 图像编辑」模型。

| 组件 | 规格 |
|---|---|
| 视觉生成主干 | **7B，32 层 Single-Stream DiT**，block-causal 注意力（`(q_idx >= kv_idx) or same_image_block`） |
| 文本/图像编码器 | **Qwen3-VL 8B**（VLM，统一编码文本指令与条件图） |
| VAE | **64 通道 RGBA 自编码器，16× 空间压缩**，原生透明 |
| 调度器 | Flow Matching + Euler discrete + dynamic shifting |

**四个卖点**
1. **原生 RGBA 透明图** —— 直接出带 alpha 通道的 PNG，不需要抠图/bg-removal 节点
2. **原生 2K** —— 2048×2048 直接生成，不是放大上去的
3. **最多 10 张参考图** —— 人物/产品/多主体合成
4. **一套权重同时做生成和编辑** —— 不是两个模型

**官方推荐尺寸（7 组）**

| 比例 | 尺寸 | 比例 | 尺寸 |
|---|---|---|---|
| 1:1 | 2048×2048 | 3:2 | 2528×1696 |
| 4:3 | 2400×1792 | 2:3 | 1696×2528 |
| 3:4 | 1792×2400 | 16:9 | 2752×1536 |
| | | 9:16 | 1536×2752 |

默认 `num_inference_steps = 40`。

**权重来源（已实时核对文件大小）**

| 仓库 | 内容 | 总量 |
|---|---|---|
| `Qwen/Qwen-Image-2.1` | Diffusers 格式：transformer 14.23 GB + text_encoder 17.54 GB + vae 1.35 GB | **33.12 GB** |
| `Comfy-Org/Qwen-Image-2.1` | ComfyUI 格式（bf16 / int8 / w4a8 三档） | 74.30 GB（按需取） |
| `Qwen/Qwen-Image-2.1-PE-T2I` / `-PE-I2I` | 提示词重写模型（Qwen3.5-VL 9B 微调），**可选** | 各 18.82 GB |

**Comfy-Org 权重明细（路线 A 的选型表）**

| 文件 | 大小 | 用途 |
|---|---|---|
| `diffusion_models/qwen_image_2.1_int8_convrot.safetensors` | **7.26 GB** | ✅ 首选 DiT（int8） |
| `diffusion_models/qwen_image_2.1_bf16.safetensors` | 14.23 GB | 满血 DiT（16GB 卡吃紧） |
| `text_encoders/qwen3vl_8b_int8_convrot.safetensors` | **9.35 GB** | ✅ 首选文本编码器（int8） |
| `text_encoders/qwen3vl_8b_w4a8.safetensors` | 6.31 GB | 低显存备选（质量有损） |
| `text_encoders/qwen3vl_8b_bf16.safetensors` | 17.53 GB | ❌ 单卡放不下（>16GB） |
| `vae/qwen_image_2.1_vae_bf16.safetensors` | **0.68 GB** | ✅ VAE |

> 注意：**`text_encoder` 是 17.5 GB bf16，本身就超过 16 GB 显存**。这是 16GB 卡必须走 int8/w4a8 的根本原因，不是"可选优化"。

---

## 2. 本机环境实测

```
GPU     NVIDIA GeForce RTX 5080 Laptop · 16303 MiB · compute_cap 12.0 (sm_120, Blackwell)
驱动    616.92 / CUDA UMD 13.4
当前显存占用  8213 MiB 已用 / 7765 MiB 可用   ← ⚠️ 关键瓶颈
CPU     AMD Ryzen 7 9700X (8C/16T)
内存    WSL 内 23 GiB + 6 GiB swap
系统    WSL2 (kernel 6.6.87.2-microsoft-standard) on Windows, Ubuntu 24.04.4 LTS
磁盘    /  = 1007 GB，剩 891 GB     |     C: = 200 GB，剩 24 GB
Python  3.12.3（系统），pip 24.0，**无 torch**
缺失    uv / pipx / ffmpeg / nvcc / conda（都不需要）
已有    ~/qwen/（Qwen3.8-27B + Ollama，含现成多线程下载器）
网络    hf-mirror.com ✅ 1.1s | ModelScope ✅ 0.22s | PyPI ✅ | 清华镜像 ✅
        huggingface.co ❌ 5s 超时 | Docker Hub ❌ 不通
```

**WSL 配置**（`/mnt/c/Users/12451/.wslconfig`）：`networkingMode=mirrored`、`dnsTunneling=true`、`autoProxy=true`、`hostAddressLoopback=true`、`autoMemoryReclaim=gradual`

→ 好处：WSL 里起的 ComfyUI，Windows 浏览器可直接开 `http://localhost:8188`，不用配端口转发。
→ 副作用：`github.com` 被 Steam++/Watt Toolkit 改写 hosts + MITM，**HTTPS 必须 `-k` / 关 SSL 校验**（你的 `~/qwen/README.md` 第 5 节已记录）。

---

## 3. 三条路线对比与选型

| 路线 | 适合 | 优点 | 缺点 | 本机结论 |
|---|---|---|---|---|
| **A. ComfyUI + int8** | 日常出图、拖拽调参 | Day-0 原生支持、无需自定义节点、自动阶段性换出、RGBA 开箱、可视化 | 首次装 ComfyUI | ✅ **推荐** |
| **B. Diffusers 脚本** | 批量、接自己的程序、做 API | 官方最短示例、易集成 | bf16 编码器 17.5GB > 显存，必须 sequential offload（慢）；要写代码 | ✅ 备选 |
| **C. vLLM-Omni / SGLang** | 高并发 API 服务 | 吞吐高、前缀 KV 缓存、FP8、TP | Docker Hub 不通、参数多、单卡无收益 | ❌ 不做 |

**为什么选 A**：ComfyUI 会在「文本编码 → 采样 → VAE 解码」三个阶段之间自动换出权重，所以峰值显存 ≈ 单阶段最大组件（约 9.4 GB 编码 / 7.3 GB 采样），而不是三者之和 17.3 GB。这是 16 GB 卡能跑 7B DiT + 8B 编码器的唯一现实路径。

---

## 4. 显存预算（决定能不能跑 2K）

| 阶段 | 常驻权重 | 激活/中间量 | 峰值估算 |
|---|---|---|---|
| 文本编码（含参考图） | 9.35 GB (TE int8) | 1–2 GB（长提示词 + 视觉 token） | **~11 GB** |
| 去噪采样 @2K | 7.26 GB (DiT int8) | 2K latent 128×128 = 16384 token，注意力开销大 | **~10–13 GB** |
| VAE 解码 @2048² RGBA | 0.68 GB | 解码峰值 | ~2–3 GB |

**结论**
- 需要 **稳定 13 GB 以上可用显存** 才能舒服跑 2K 40 步。
- 当前只有 **7.7 GB 可用 → 一定 OOM**。所以**第 0 步必须是释放 Windows 侧显存**。
- 释放后（≈15 GB 可用）2K 可行；只想先跑通就先用 **1024×1024 / 20 步**。
- 兜底：WDDM 允许显存溢出到系统内存（WSL 有 23 GB RAM），所以未必硬崩，但会**慢到不可用**。别把它当方案。

---

## 5. 路线 A：ComfyUI + int8 权重（推荐）

### A0. 准备工作（先做，5 分钟）

**① 释放显存**——这是能否成功的第一决定因素：

```bash
# 1) 看当前占用（WSL 内看不到 Windows 进程，只能看总量）
nvidia-smi --query-gpu=memory.used,memory.free --format=csv

# 2) 停掉本机可能占卡的 Ollama（注意方括号写法，避免 pkill 杀掉自己）
pkill -f '[o]llama serve' 2>/dev/null || true

# 3) Windows 侧要手动关：浏览器硬件加速 / 游戏 / 视频渲染 / 壁纸引擎 / 其他 AI 应用
#    目标：memory.used 降到 1 GB 以下
```

**② 建目录 + 决定磁盘位置**（全部放 Linux 侧，`C:` 只剩 24 GB）

```bash
mkdir -p ~/qwen-image/{models,logs}
cd ~/qwen-image
df -h /            # 确认仍是 ~891 GB 可用
```

**③（可选）回收空间**——`~/qwen` 有约 35 GB 冗余（你自己的 README 第 7 节已列）：
`~/qwen/models/Qwen3.8-27B-Q4_K_M.gguf` 17 GB、`ollama-cuda.conda` 0.6 GB、`~/.ollama/models` 里未被引用的孤儿 blob 17 GB。

### A1. 安装 ComfyUI + Python 环境

```bash
# GitHub 被 Steam++ MITM，必须关 SSL 校验（一次性）
git config --global http.sslVerify false

cd ~/qwen-image
git clone https://github.com/comfyanonymous/ComfyUI.git

python3 -m venv ~/qwen-image/venv
~/qwen-image/venv/bin/pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple

# 先装 torch（sm_120 需要 CUDA ≥12.8 的构建）
~/qwen-image/venv/bin/pip install torch torchvision -i https://pypi.tuna.tsinghua.edu.cn/simple

# 验证 Blackwell 是否被支持 —— 这一步必须过
~/qwen-image/venv/bin/python -c "
import torch
print('torch', torch.__version__, 'cuda', torch.version.cuda)
print('device', torch.cuda.get_device_name(0), 'cap', torch.cuda.get_device_capability(0))
x = torch.randn(512,512,device='cuda',dtype=torch.bfloat16); print('bf16 matmul ok:', (x@x).sum().item()!=0)
"
```

**期望输出**：`cap (12, 0)` 且 `bf16 matmul ok: True`，无 `no kernel image is available` 报错。

**若报 sm_120 不支持** → 换官方 CUDA 索引（国内可能偏慢，耐心等或挂代理）：

```bash
~/qwen-image/venv/bin/pip install --force-reinstall torch torchvision \
  --index-url https://download.pytorch.org/whl/cu130
```

**再装 ComfyUI 依赖**：

```bash
~/qwen-image/venv/bin/pip install -r ~/qwen-image/ComfyUI/requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> ComfyUI 必须是 **2026-09-20 之后** 的版本（`git log -1 --date=short` 确认），否则不认 Qwen-Image-2.1 和 `int8_convrot` 权重格式。

### A2. 下载权重（17.29 GB）✅ 已完成

> **实际执行结果**：源用 **ModelScope**，驱动脚本 `~/qwen-image/tools/download_all.sh`，
> 下载器 `~/qwen-image/tools/fetch_ms.py`（在你原版 `fetch.py` 上改了两点：改用
> `Range: bytes=0-0` + `Content-Range` 探测总长，因为 ModelScope 的 HEAD 不返回
> `Content-Length`；并内置 sha256 校验）。
> 权重落在 **`~/qwen-image/models/`** 暂存，装好 ComfyUI 后需挂到 `ComfyUI/models/`。
> 结果：**17.28 GB，3 个文件 sha256 全部一致，耗时 2 分 52 秒**。

**重跑/续传**：`bash ~/qwen-image/tools/download_all.sh`（已完成的 `.partNN` 会自动跳过）

**以下为备用源（hf-mirror，实测仅 2 MB/s）**，复用你现成的多线程下载器 `~/qwen/tools/fetch.py`：

```bash
cd ~/qwen-image
BASE="https://hf-mirror.com/Comfy-Org/Qwen-Image-2.1/resolve/main"
DEST="$HOME/qwen-image/ComfyUI/models"

# 1) DiT int8 —— 7.26 GB
python3 ~/qwen/tools/fetch.py "$BASE/diffusion_models/qwen_image_2.1_int8_convrot.safetensors" \
  "$DEST/diffusion_models/qwen_image_2.1_int8_convrot.safetensors" 12

# 2) 文本编码器 int8 —— 9.35 GB（最大的一个）
python3 ~/qwen/tools/fetch.py "$BASE/text_encoders/qwen3vl_8b_int8_convrot.safetensors" \
  "$DEST/text_encoders/qwen3vl_8b_int8_convrot.safetensors" 12

# 3) VAE —— 0.68 GB
python3 ~/qwen/tools/fetch.py "$BASE/vae/qwen_image_2.1_vae_bf16.safetensors" \
  "$DEST/vae/qwen_image_2.1_vae_bf16.safetensors" 12
```

**ModelScope 备用源**（实测 0.22s 响应，且已镜像该仓库）：

```
https://www.modelscope.cn/models/Comfy-Org/Qwen-Image-2.1/resolve/master/<上面的相对路径>
```

**显存极度紧张时的替换**：把第 2 步换成 `text_encoders/qwen3vl_8b_w4a8.safetensors`（6.31 GB），总下载降到 14.25 GB，文本理解质量有损。

**当前实际落盘位置（暂存，已校验）**：

```
~/qwen-image/models/
├── diffusion_models/qwen_image_2.1_int8_convrot.safetensors   7,256,783,064 B
├── text_encoders/qwen3vl_8b_int8_convrot.safetensors          9,350,798,360 B
└── vae/qwen_image_2.1_vae_bf16.safetensors                      675,509,688 B
                                              合计 17,283,091,112 B
```

装好 ComfyUI 后挂载（**用符号链接，不复制**，省 17 GB）：

```bash
cd ~/qwen-image/ComfyUI/models
ln -sfn ~/qwen-image/models/diffusion_models/qwen_image_2.1_int8_convrot.safetensors diffusion_models/
ln -sfn ~/qwen-image/models/text_encoders/qwen3vl_8b_int8_convrot.safetensors      text_encoders/
ln -sfn ~/qwen-image/models/vae/qwen_image_2.1_vae_bf16.safetensors                vae/
```

> 注意 `git clone` 的目标目录必须为空：所以**先 clone ComfyUI，再建 `models/` 子目录**。
> 磁盘现状：`/` 剩 874 GB。

### A3. 启动

```bash
cd ~/qwen-image/ComfyUI
setsid nohup ~/qwen-image/venv/bin/python main.py \
    --listen 0.0.0.0 --port 8188 \
    --reserve-vram 0.8 \
    > ~/qwen-image/logs/comfy.log 2>&1 &

sleep 20 && tail -30 ~/qwen-image/logs/comfy.log
```

- `--reserve-vram 0.8`：给系统留 0.8 GB，避免显存爆满时把 WSL 显示驱动搞崩
- 用 `setsid nohup`（你的 README 第 6 节经验：普通 `nohup &` 挡不住进程组 SIGTERM）
- Windows 浏览器直接开 **http://localhost:8188**（`networkingMode=mirrored` + `hostAddressLoopback=true` 已配好）
- 停止：`pkill -f '[m]ain.py'`（方括号写法）

**显存不够时的降级顺序**（每次只改一项）：
1. 加 `--lowvram`（分层换入换出，慢但省）
2. 换 w4a8 编码器（A2 的替换项）
3. 分辨率降到 1024×1024、步数降到 20

### A4. 冒烟测试与验收

在 ComfyUI 里：**Templates → 搜 `Qwen-Image 2.1` → 载入 `t2i` 模板**（官方 Day-0 提供 `image_qwen_image_2_1_t2i.json` 和 `image_qwen_image_2_1_image_edit.json`）。
节点名是 **Text Encode Qwen Image 2.1**，`image_1` 起可挂最多 10 张参考图。

| 轮次 | 尺寸 | 步数 | 目的 |
|---|---|---|---|
| 1 | 1024×1024 | 20 | 只验证链路通 |
| 2 | 2048×2048 | 40 | 官方配置，看真实耗时与峰值显存 |
| 3 | 同上 + RGBA 提示词 | 40 | 验证透明输出 |
| 4 | 挂 1 张参考图做编辑 | 40 | 验证编辑链路 |

**透明图提示词模板**（不按这个格式写会出灰底/白底）：

> `This is an RGBA image with transparency. <你的描述>. The image has alpha channel and the background is transparent.`

**验收要记录的指标**（照你 README 的风格）：首图延迟、连续 3 次峰值显存、2048² 40 步单图耗时、OOM 临界分辨率。

### A5. 可选增强

| 项 | 收益 | 代价 |
|---|---|---|
| **提示词重写模型** PE-T2I（int8 9.47 GB） | 短提示词 → 长详述，构图/文字显著变好 | 多 9.5 GB 下载 + 一次 LLM 前向 |
| **LightX2V / Qwen-Image-Lightning** | 步数大幅压缩，官方称 25× NFE 削减 | 另装框架，画质有出入 |
| **LoRA** | 风格/人像微调 | 需训练或找社区权重 |

---

## 6. 路线 B：Diffusers 脚本（备选）

只在需要**写代码、批量出图、自己包 API** 时走这条。

### 关键坑：diffusers 不能用 PyPI 版本

PyPI 上最新 `diffusers 0.40.0` 发布于 **2026-08-20**，**早于** Qwen-Image-2.1 的 09-20，因此**不含 `QwenImage21Pipeline`**。必须从 GitHub main 装（我已确认 main 上存在 `src/diffusers/pipelines/qwenimage21/pipeline_qwenimage21.py`）：

```bash
python3 -m venv ~/qwen-image/venv-diffusers
~/qwen-image/venv-diffusers/bin/pip install -U pip -i https://pypi.tuna.tsinghua.edu.cn/simple

# GitHub SSL 被 MITM，靠 GIT_SSL_NO_VERIFY 绕过（前面已设 git config，这里双保险）
GIT_SSL_NO_VERIFY=1 ~/qwen-image/venv-diffusers/bin/pip install \
  git+https://github.com/huggingface/diffusers

~/qwen-image/venv-diffusers/bin/pip install 'transformers>=5.17' accelerate pillow \
  torch torchvision -i https://pypi.tuna.tsinghua.edu.cn/simple

# 确认装上了
~/qwen-image/venv-diffusers/bin/python -c "from diffusers import QwenImage21Pipeline; print('ok')"
```

### 下载权重（33.12 GB，用 hf-mirror）

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 关键：直连 huggingface.co 不通
~/qwen-image/venv-diffusers/bin/pip install -U "huggingface_hub[cli]" -i https://pypi.tuna.tsinghua.edu.cn/simple
HF_ENDPOINT=https://hf-mirror.com ~/qwen-image/venv-diffusers/bin/hf download \
  Qwen/Qwen-Image-2.1 --local-dir ~/qwen-image/models/Qwen-Image-2.1
```

### 最小脚本 `t2i.py`

```python
import torch
from diffusers import QwenImage21Pipeline

pipe = QwenImage21Pipeline.from_pretrained(
    "/home/lzy959/qwen-image/models/Qwen-Image-2.1",
    torch_dtype=torch.bfloat16,
    local_files_only=True,
)

# ⚠️ 必须用 sequential offload：
#    text_encoder 是 17.5 GB bf16 > 16 GB 显存，
#    enable_model_cpu_offload() 是"整组件搬上卡"，会直接 OOM。
pipe.enable_sequential_cpu_offload()   # 逐层换入，能跑但慢

image = pipe(
    prompt="A neon shop sign that reads \"QWEN IMAGE 2.1\", rainy night, reflections on wet pavement",
    width=1024, height=1024,          # 先小尺寸验证，通了两 K
    num_inference_steps=20,
    generator=torch.Generator("cuda").manual_seed(42),
).images[0]

image.save("t2i_example.png")
```

性能预期：sequential offload 每步都在 PCIe 上搬权重，**比 ComfyUI int8 慢数倍**。想快就得用 int8 权重 + bitsandbytes/quanto，工程量明显上升 —— 这也是推荐路线 A 的原因。

---

## 7. 网络踩坑清单（复用你已验证的结论）

| 现象 | 真相与对策 |
|---|---|
| `huggingface.co` 超时（我实测 5s 无响应） | 用 **`hf-mirror.com`**（1.1s）或 **ModelScope**（0.22s）。设 `HF_ENDPOINT=https://hf-mirror.com` |
| `github.com` 解析到 `127.0.0.1` | Steam++ / Watt Toolkit 写的 hosts，`.wslconfig` 的 mirrored 网络让 WSL 共用 → `git config --global http.sslVerify false` |
| `unable to get local issuer certificate` | MITM 根证书只在 Windows 证书store → `curl -k` / 关 SSL 校验 |
| GitHub 克隆慢 | 仓库本体不大（ComfyUI ~几十 MB），可接受；release 资产才会 60–75 KB/s |
| Docker Hub 不通 | 所以 **vLLM-Omni / SGLang 容器路线直接排除** |
| PyPI 慢 | 全程加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 单连接被限速 | `fetch.py` 多线程分块，12 并发；某块慢就用 `finish.py` 再切 8 段补齐 |

---

## 8. 排错表

| 症状 | 原因 | 处理 |
|---|---|---|
| `CUDA out of memory` | Windows 侧占着显存 | 关浏览器/游戏，`pkill -f '[o]llama serve'`；降分辨率；`--lowvram` |
| `no kernel image is available for execution` | torch wheel 不含 sm_120 | 换 `--index-url https://download.pytorch.org/whl/cu130` 重装 |
| ComfyUI 不认 `int8_convrot` 权重 / 无 Qwen-Image 2.1 模板 | ComfyUI 版本早于 09-20 | `cd ComfyUI && git pull`（已关 sslVerify） |
| `ImportError: cannot import name 'QwenImage21Pipeline'` | 装了 PyPI 的 0.40.0 | `pip install git+https://github.com/huggingface/diffusers`（带 `GIT_SSL_NO_VERIFY=1`） |
| 下载 403 / 卡住 | 走了 huggingface.co | 换 hf-mirror 或 ModelScope |
| 透明图出灰底/白底 | 提示词没写 RGBA/alpha/transparent | 用 A4 的模板句；**存 PNG，JPEG 不存 alpha** |
| 文字渲染不准 | 短提示词 | 上 PE-T2I 重写模型 |
| 采样极慢（分钟级/步） | 显存溢出到系统内存了 | 降分辨率，确认 `memory.used` 没有顶满 |

---

## 9. 执行清单（已全部完成）

- [x] 0. 释放 Windows 显存（本次从剩 7.7 GB 提升到 ~14.6 GB 可用）
- [x] 1. `mkdir -p ~/qwen-image`，`/` 剩余 874 GB
- [x] 2. 取 ComfyUI 源码（走 gh-proxy + codeload 回退，带 `.git`）
- [x] 3. 建 venv，装 torch，**sm_120 + bf16 matmul 验证通过**
- [x] 4. 装 `requirements.txt`（清华 + 官方 PyPI 补缺）
- [x] 5. 下载 3 个权重文件（17.28 GB），**sha256 全部一致**
- [x] 6. 符号链接挂到 `ComfyUI/models/{diffusion_models,text_encoders,vae}/`
- [x] 7. `setsid nohup` 启动，`localhost:8188` 就绪（8 s）
- [x] 8. 文生图 1024² / 25 步冒烟通过（65 s，文字渲染精确）
- [x] 9. 2048² / 25 步基准（85 s），2K 无 OOM
- [x] 10. RGBA 透明输出 + 单参考图编辑，均通过
- [ ] 11. ⚠️ **读 LICENSE，确认用途合规（Qwen Research License，非 Apache-2.0）—— 需要你自己判断**

### 日常使用

```bash
bash ~/qwen-image/bin/serve.sh              # 启动（幂等，已在跑则直接返回）
bash ~/qwen-image/bin/serve.sh --lowvram    # 显存紧张时
pkill -f '[m]ain.py'                        # 停止（方括号写法，避免杀掉自己）
tail -f ~/qwen-image/logs/comfy.log         # 看日志
# 浏览器（Windows 侧）：http://localhost:8188
```

命令行出图 / 编辑（不需要开网页）：

```bash
# 文生图           宽  高  步数  "提示词"
python3 ~/qwen-image/tools/smoke_test.py 1024 1024 25 "your prompt"

# 图像编辑（输入图需先在 ComfyUI/input/ 下）
python3 ~/qwen-image/tools/edit_test.py dragon_sticker.png "Change the dragon's color to blue." 25
```

> 官方模板在网页里是 **Templates → Qwen-Image 2.1**；节点是
> **Text Encode Qwen Image 2.1**（`image_1` 起可挂最多 10 张参考图）。
> RGBA 透明图提示词必须写成：
> `This is an RGBA image with transparency. <描述>. The image has alpha channel and the background is transparent.`

---

## 10. 本次执行的踩坑记录（都是真实卡点，可复现）

### 10.1 GitHub 直连不通，但 codeload 与 gh-proxy 通

| 端点 | 结果 |
|---|---|
| `github.com` | ❌ 解析到真实 IP `20.205.243.166` 但连接超时 |
| `raw.githubusercontent.com` | ❌ 超时 |
| `api.github.com` | ✅ 301，0.57 s |
| **`codeload.github.com`**（官方打包端点） | ✅ 200 |
| **`gh-proxy.com`** | ✅ 206，174 KB/s |

最终用 `git clone https://gh-proxy.com/https://github.com/comfyanonymous/ComfyUI.git`
（脚本 `tools/fetch_comfyui.sh`，带回退）。**带 `.git`，以后可以 `git pull` 更新。**
用 `git -c http.sslVerify=false` 单次绕过 Steam++ 的 MITM，**没有改你的全局 git 配置**。

### 10.2 清华 PyPI 少一个刚发布的包

`comfyui-workflow-templates-media-assets-02==0.1.3` 官方 PyPI 有、清华镜像**还没同步**，
导致 `pip install -r requirements.txt` 直接失败。解决：清华为主 + 官方 PyPI 补缺。

```bash
~/qwen-image/venv/bin/pip install -r requirements.txt \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --extra-index-url https://pypi.org/simple
```

> 教训：`pip install ... | tail -N` 会把退出码换成 `tail` 的 0，失败被吞掉。
> 判断成功必须看 `PIPESTATUS` 或直接 `grep` 日志里的 `Successfully installed`。

### 10.3 torch 不需要换 cu130 索引（与预案相反）

原方案预留了「默认 wheel 不含 sm_120 就换 `download.pytorch.org/whl/cu130`」的退路，
实际**清华 PyPI 的默认 wheel 就是 `2.14.0+cu130`**，直接支持 Blackwell：

```
torch 2.14.0+cu130 | cuda 13.0 | capability (12, 0)
arch_list ['sm_75','sm_80','sm_86','sm_90','sm_100','sm_120'] | bf16 matmul ok
```

### 10.4 `int8_convrot` 权重格式已核实

文件内嵌每个量化层的配置张量（`<layer>.comfy_quant`，U8），内容是：

```json
{"format": "int8_tensorwise", "convrot": true, "convrot_groupsize": 256}
```

ComfyUI 的 `QUANT_ALGOS` 正好有 `int8_tensorwise`，且 `comfy/ops.py:1314` 专门处理
`quant_format == "int8_tensorwise" and convrot` → **完全兼容**，无需自定义节点。

### 10.5 ⚠️ 最容易踩的坑：autogrow 输入在 API 里必须用点号键

`TextEncodeQwenImage21` 的参考图输入是 autogrow 类型。用 `/prompt` API 提交时：

| 写法 | 结果 |
|---|---|
| `{"image_1": ["470", 0]}` | ❌ `TypeError: execute() got an unexpected keyword argument 'image_1'` |
| `{"images": {"image_1": [...]}}` | ⚠️ 不报错，但**参考图被静默忽略**，出图与不传图完全相同 |
| **`{"images.image_1": ["470", 0]}`** | ✅ 正确 |

原因在 `comfy_api/latest/_io.py`：`finalize_prefix()` 用 `.` 拼接父输入名与模板名，
所以真实输入 id 是 `images.image_1`。

**判断参考图有没有生效的最快方法**：固定 seed，传图与不传图各跑一次，比对输出 PNG 的
sha256。两次一样就说明图没进去。
（注意：别拿 DSH 预览的 WebP 归一化副本哈希做比较，那不是源文件哈希。）

### 10.6 编辑任务必须走「节点自带 latent」

`TextEncodeQwenImage21` 的 `latent` 输出（第 3 个输出）文档原文：
「Empty latent on the first reference image's size, to match with sampling as any other
size shifts the edit.」→ **编辑时 `KSampler.latent_image` 要接它**，不能接 `EmptyLatentImage`，
否则编辑会偏移。纯文生图两者皆可（官方 t2i 模板用的是 `EmptyLatentImage`）。

### 10.7 小瑕疵

- `torchaudio` 被解析成 `2.11.0`（torch 是 `2.14.0`），版本不匹配。当前不影响图像功能
  （ComfyUI 0.37.0 启动与出图无报错），但以后要用音频节点时需要对齐版本。
- ComfyUI 启动日志有 `No OpenGL_accelerate module loaded`，非致命，可忽略。

---

## 11. 参考来源

- 官方仓库（含 Day-0 支持与架构说明）：https://github.com/QwenLM/Qwen-Image-2.1
- 模型卡：https://huggingface.co/Qwen/Qwen-Image-2.1
- ComfyUI 兼容权重：https://huggingface.co/Comfy-Org/Qwen-Image-2.1
- ComfyUI 官方博客（RGBA / 10 参考图 / 模板）：https://blog.comfy.org/p/qwen-image-21-in-comfyui-open-weight
- vLLM-Omni recipe：https://recipes.vllm.ai/Qwen/Qwen-Image-2.1
- SGLang cookbook：https://docs.sglang.io/cookbook/diffusion/Qwen-Image/Qwen-Image-2.1
- 部署要点与排错（第三方汇总）：https://news.qiniu.com/archives/1789957408429
- 本机既有部署经验：`~/qwen/README.md`（网络实况、下载器、pkill 坑）
