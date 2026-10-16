# Strata 本地大模型安装进度（2026-10-15 深夜暂停）

> **2026-10-16 23:15 更新：已全部完成 ✅**
> Strata（Qwen3.8-Flash-Next 125B MoE）已装好并运行，实测 **70+ tok/s**（旧方案 Ollama 仅 4.15 tok/s），
> QQ 机器人已切换到这个后端并通过自检。下方保留当初暂停时的原始记录与踩坑清单，供日后维护参考。

---

## 零、最终结果（2026-10-16）

| 指标 | 旧方案 Ollama | **新方案 Strata** |
|---|---|---|
| 模型 | qwen3.8-27b（dense 27B） | Qwen3.8-Flash-Next（**125B MoE**） |
| 生成速度 | 4.15 tok/s | **70.2 ~ 72.9 tok/s** |
| 预填充 | 28 tok/s | **~320 tok/s** |
| 显存占用 | 15.8 GB（打满超售） | 13.2 GB（专家缓存 2756 个，命中率 80.6%） |
| 机器人端到端 | 16.0 s/句 | **2.4 s/句** |

**服务信息**

```
OpenAI 兼容：   http://127.0.0.1:8080/v1
Anthropic 兼容：http://127.0.0.1:8080/v1/messages
模型名：qwen3.8-flash-next-iq2_xs    上下文：65536    KV：int8    引擎：0.1.44.1
```

**已完成的关键动作**

1. 分片 2 续传完成 + SHA-256 校验；专家打包 `packs/iq2_xs`；MTP 层量化（5.2 GB → 0.889 GB）
2. `run-iq2_xs.bat` 生成；模型加载 33.02 GiB 专家进内存（52 s），GPU 专家缓存 3.71 GiB
3. **QQ 机器人切换**：`~/qq-bot/config.json` 的 `ollama_base` → `http://127.0.0.1:8080/v1`，
   `model` → `qwen3.8-flash-next-iq2_xs`（改前已备份 `config.json.bak-20261016-2313`）
4. **`svc.sh` 适配**：新增 `STRATA_URL`，`status`/`vram_gib` 优先读 Strata 的 `/v1/status`
   （取 `machine.gpu.used_mib`），Ollama 逻辑保留为回退
5. 自检通过：`bash ~/qq-bot/run.sh --selftest` → 打到 8080，200 OK，2.4 s

**遗留事项（主人需知道）**

- **出图前的显存互斥失效**：`image_gen.py` 的 `release_chat_model()` 走 Ollama 的 `/api/generate` 来卸载聊天模型；
  Strata 没有该 API，所以这一步现在只会快速失败并被忽略。若 ComfyUI 出图报显存不足，
  要么手动关掉模型窗口，要么重跑 setup 加 `--vram-reserve-mib` 多留显存。
- **模型依附于一个终端窗口**：关掉那个窗口就停模型。重启用 `Y:\Strata\run-iq2_xs.bat` 或 `START-HERE.bat`。
- 内存很紧：模型加载后 RAM 占用约 **46.7 / 47.1 GiB**，setup 已自动关掉 KV streaming 保平稳。

---

---

## 一、为什么要换 Strata

原方案是 Ollama 跑 `qwen3.8-27b-local`（Q4_K_M，17.65 GiB），但显卡只有 16.3 GB 显存，装不下：

| 实测项 | 数值 | 说明 |
|---|---|---|
| 生成速度 | 1.98 → 4.85 tok/s | 取决于 offload 层数，最好也就 4.85 |
| 预填充 | 16 ~ 32 tok/s | 异常低 |
| GPU 功耗 / 利用率 | 73 W / 21% | GPU 在划水，瓶颈在跨设备同步 |
| 图分割（graph splits） | 420 次 | CPU/GPU 之间反复同步的元凶 |

Strata 的解法正对这种病：**MoE 专家分摊**（GPU 只放最热专家，全部专家常驻内存，CPU 与 GPU 并行计算），
不要求整个模型塞进显存。官方在 12 GB 的 RTX 5070 上实测 53~94 tok/s。

参考：[Strata README](https://github.com/Niko1221/Strata/blob/main/README.zh-CN.md) · [实现原理](https://github.com/Niko1221/Strata/blob/main/docs/HOW_IT_WORKS.md)

---

## 二、硬件判定（setup --check 原话）

```
[ok] GPU: NVIDIA GeForce RTX 5080, 15.9 GB VRAM, compute capability 12.0, driver 617.42
[ok] RAM: 47 GB
[ok] CPU: AMD Ryzen 7 9700X 8-Core Processor (AVX-512)
[ok] PCIe: 4.0 x16
```

| 规格 | 判定 |
|---|---|
| Q2_0 | 需 ~48 GB 内存：tight |
| **IQ2_XS（已选）** | 需 ~48 GB 内存：tight ← 可跑，但内存偏紧 |
| IQ3_XXS / IQ3_S | low-RAM 模式可行（GPU 放 22~25% 专家） |
| IQ1_M | fits，但只有一半专家，**中文弱**（不适合 QQ 场景） |

> 内存偏紧是已知风险：物理 47.1 GB，而 IQ2_XS 标称需 48 GB。
> 若启动或运行中出现内存压力，回退选项见第六节。

---

## 三、已完成的步骤（重跑会自动跳过，勿重做）

| # | 内容 | 位置 / 状态 |
|---|---|---|
| 1 | Strata 源码 v0.1.44.1 | `Y:\Strata\` |
| 2 | 引擎包（本地 prebuilt） | `Y:\Strata-prebuilt\` |
| 2a | `strata-windows-x64.zip`（148 MB，**CUDA 13**，archs 含 sm_120） | ← setup 实际使用 |
| 2b | `strata-windows-x64-cuda12.zip`（215 MB，备用） | 未使用 |
| 3 | llama.cpp 源码 `3cf0325` | `Y:\Strata\third_party\llama.cpp-3cf0325.zip` + 同名 `.done` |
| 4 | Python 环境 | `Y:\Strata\.venv\`（含 nvidia-cublas 13.0.2.14、nvidia-cuda-runtime 13.0.96） |
| 5 | 引擎安装 | `Y:\Strata\engine\strata.exe`（`[ok] ready-made engine 0.1.44.1 for sm_75, sm_86, sm_89, sm_120 (CUDA 13.0)`） |
| 6 | 模型分片 1（36.53 GB） | `Y:\Strata-data\models\IQ2_XS\...-00001-of-00002.gguf` ✅ 已 SHA-256 校验 |
| 7 | 模型分片 2（26.82 GB） | `...-00002-of-00002.gguf.part` ⏸ 约 19 GB，待续传 |

合计 63.35 GB，**当前约 85%**。暂停时 Y 盘剩余 170.6 GB。

### 安装参数（已写入 setup 的配置，勿选错）

- model = **IQ2_XS**
- context = **65536**
- KV cache = **8-bit**
- images = **off**
- experimental speed projection = **off**

---

## 四、下次继续：一条命令

在 Windows 侧（cmd）执行：

```bat
cd /d Y:\Strata
set "STRATA_PREBUILT_URL=Y:\Strata-prebuilt"
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
START-HERE.bat --model IQ2_XS --data-dir Y:\Strata-data --source modelscope --prebuilt Y:\Strata-prebuilt --yes
```

从 WSL 侧用 interop 调用（本次就是这么跑的）：

```bash
/mnt/c/Windows/System32/cmd.exe /c 'cd /d Y:\Strata & set "STRATA_PREBUILT_URL=Y:\Strata-prebuilt" & set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" & START-HERE.bat --model IQ2_XS --data-dir Y:\Strata-data --source modelscope --prebuilt Y:\Strata-prebuilt --yes'
```

**它会依次**：跳过已完成步骤 → 续传分片 2（约 8 GB，实测 ~40 MB/s，约 3-5 分钟）→ 校验 SHA-256 → 打包专家（pack）
→ 写配置 → **启动模型**。

> 启动时会往内存加载 35-55 GB，**电脑会卡 1-3 分钟**，属正常，别关窗口。

---

## 五、启动后要做的两件事

**1. 验证**

- 浏览器打开 `http://127.0.0.1:8080`
- 或命令行：`curl http://127.0.0.1:8080/v1/status`

**2. 把 QQ 机器人切到 Strata**

编辑 `~/qq-bot/config.json`（改两行）：

```json
"ollama_base": "http://127.0.0.1:8080/v1"    // 原 http://127.0.0.1:11434/v1
"model":       "qwen3.8-flash-next"          // 名字随便填，Strata 不校验
```

- **机器人代码不用改**：`official_bot.py` 走的就是 OpenAI 兼容协议。
- 但 `ollama_root`（`http://127.0.0.1:11434`，`svc.sh` 用它查显存 `/api/ps`）Strata 没有对应 API，
  改用 `GET http://127.0.0.1:8080/v1/status` 或 `/health`。
- 切换后 Ollama 可以停掉腾显存：`bash ~/qq-bot/svc.sh stop ollama`。
- WSL 与 Windows 已配 `networkingMode=mirrored` + `hostAddressLoopback`，WSL 里直接访问 `127.0.0.1:8080` 即可。

---

## 六、回退选项（内存不够时）

- 换 `IQ3_XXS`（需 ~60 GB，走 low-RAM 模式，GPU 放约 25% 专家，其余留内存）：
  重跑命令把 `--model IQ3_XS` 换成 `--model IQ3_XXS` 即可（会额外下载）。
- 省显存给别的程序：`--vram-reserve-mib 2048`（默认只留 700 MiB）。
- 完全卸载模型/释放内存：关掉 Strata 窗口即可；显存与内存会被释放。

---

## 七、踩过的坑（重要，别重踩）

1. **GitHub 直连此机极慢**：Windows 侧 ~25 KB/s，WSL 侧 ~40 KB/s。
2. **可用镜像**：`ghfast.top`、`gh-proxy.com` —— 持续 ~320 KB/s，且支持 Range 续传（HTTP 206）。
   - 注意 `ghfast.top` 前 12 秒是突发 2.7 MB/s，之后掉到 ~9 KB/s；**要按"持续速度"选源**。
   - 失效的：`gh.llkk.cc`、`github.moeyy.xyz`、`ghproxy.cc`、`hub.gitmirror.com`。
3. **Windows 侧到镜像比 WSL 侧慢约 100 倍** → 大文件一律**先在 WSL 下载，再 copy 到 Y 盘**
   （`copy \\wsl.localhost\Ubuntu-24.04\home\lzy959\xxx.zip Y:\...`，215 MB 只要 0.56 秒）。
4. **setup.py 即使用预编译引擎，也会先去 GitHub 拉 llama.cpp 源码**（`setup.py:5478` 无条件调用）。
   → 预先放好 `third_party\llama.cpp-3cf0325.zip` **并创建同名 `.done` 文件**即可完全跳过下载。
5. **`--prebuilt` 参数在 CUDA 引擎分支会被忽略**（`setup.py:5376` 直接读环境变量）
   → 必须同时设置 `STRATA_PREBUILT_URL`。
6. **引擎包名要对**：CUDA 13 用 `strata-windows-x64.zip`（archs 含 **120**，原生支持 RTX 5080）；
   `strata-windows-x64-cuda12.zip` 的 archs 是 `[60,61,70,75,80,86,89]`，只能靠 PTX 兜底。
7. **ModelScope 实测 33.8 MB/s**（远超官方文档写的 11-14 MB/s），且支持 Range。
8. **NTFS/9p 元数据延迟**：下载中 `dir` 会显示 `.part` 为 0 字节，别被骗；
   用 `(Get-PSDrive Y).Free` 的剩余空间变化判断更可靠。
9. WSL 侧**无法写 `/mnt/y`**（沙箱限制），但**读可以**；写操作走 Windows 侧命令。

---

## 八、相关环境事实（备查）

- 物理内存 47.1 GB；WSL 分配 23 GiB（默认 50%，未改 `.wslconfig`）
- WSL 发行版 Ubuntu-24.04，虚拟盘位于 `Y:\wsl\`（Y 盘总 754 GB）
- C 盘仅剩约 25 GB，**不要往 C 盘装大文件**
- Windows 侧已有 Python 3.12.10、git 2.54.0、bsdtar 3.8.8
- 无可用代理（`127.0.0.1:9000` 未监听，系统代理关闭）
- 上一件事的收尾：DSH 源码模式的 `dw` 已修好（重建了过期的 web shell 并加了产物自检），
  `~/qq-bot/svc.sh` 管理的 Ollama 与「千鲸🐋」官方机器人目前仍在运行
