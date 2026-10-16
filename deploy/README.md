# 部署脚本集

本目录收录这套机器人所依赖的**部署与运维脚本**（WSL2 + Windows 双栈），便于换机重建。

> ⚠️ 脚本里是**本机路径**（`/home/lzy959`、`Y:\`），换机请按自己的路径修改。
> ⚠️ 密钥（`qq.env`、`config.json`）**不在本仓库**，需另行准备。

---

## 一、显存互斥切换（最重要）

`switch.sh` —— 聊天模型与画图模型共用一块 16 GB 显卡，必须二选一：

```bash
bash switch.sh status    # 看两边状态与显存占用
bash switch.sh image     # 要画图：停聊天模型 → 起 ComfyUI（约 13 秒）
bash switch.sh chat      # 要聊天：停 ComfyUI → 起聊天模型（约 42 秒，39 GB 内存加载）
bash switch.sh stop-all  # 两个都停
```

**为什么必须互斥**：聊天模型常驻约 13.1 GB / 16.3 GB 显存，出图峰值还要 10~13 GB，
一块卡放不下两个。切换脚本靠 `taskkill /F /T` 杀掉 8080 端口的持有进程
（只杀 `strata.exe` 不够——前端 `auto_load` 还活着会继续占着端口）。

---

## 二、启动脚本

| 脚本 | 平台 | 作用 |
|---|---|---|
| `run-strata.bat` | **Windows** | 启动 Strata 本地大模型（`127.0.0.1:8080`），加载 Qwen3.8-Flash-Next IQ2_XS，约 40~60 秒 |
| `serve-image.sh` | WSL | 启动 ComfyUI（`127.0.0.1:8188`） |
| `serve-ollama.sh` | WSL | 启动 Ollama（`127.0.0.1:11434`）——**旧方案，已停用**，留档 |
| `ask-ollama.sh` | WSL | 命令行向 Ollama 提问——旧方案，留档 |

---

## 三、配套部署文档

| 文档 | 讲什么 |
|---|---|
| [`../docs/STRATA-DEPLOY.md`](../docs/STRATA-DEPLOY.md) | ⭐ **本地 125B 模型部署实录**（含 10 条踩坑） |
| [`../docs/QWEN-IMAGE-DEPLOY.md`](../docs/QWEN-IMAGE-DEPLOY.md) | 生图接入机器人的方案 |
| [`qwen-image-DEPLOY.md`](qwen-image-DEPLOY.md) | 生图部署详细过程 + 显存实测数据 |
| [`../docs/MULTI-MODEL-GPU.md`](../docs/MULTI-MODEL-GPU.md) | 16 GB 显存如何让聊天与画图共存 |
| [`../docs/OPS.md`](../docs/OPS.md) | 运维与排障手册 |

---

## 四、换机重建顺序

**前置**：Windows 11 + WSL2（Ubuntu 24.04）+ RTX 5080 16 GB + 47 GB 内存

**① 模型侧（Windows）**

```
1. 取 Strata 引擎      → Y:\Strata\（Windows 原生，不要放 WSL 里）
2. 下模型权重          → Y:\Strata-data\models\IQ2_XS\（两个分片，共 68 GB）
3. 跑 run-strata.bat   → 等它打印 ready，服务在 127.0.0.1:8080
```

**② 机器人侧（WSL）**

```bash
pip install -r ../requirements.txt      # 或按 docs/DEPLOY.md 装
# 准备 config.json（AppID / Secret）与 qq.env —— 这两样不入库
bash ../svc.sh start official
```

**③ 画图侧（WSL，可选）**

```bash
bash serve-image.sh        # 起 ComfyUI（8188）
bash switch.sh image       # 或用它自动完成"停聊天 + 起画图"
```

---

## 五、目录对照

| 脚本部署位置 | 仓库内副本 |
|---|---|
| `~/qwen-image/bin/switch.sh` | `deploy/switch.sh` |
| `~/qwen-image/bin/serve.sh` | `deploy/serve-image.sh` |
| `~/qwen/bin/serve.sh` | `deploy/serve-ollama.sh` |
| `~/qwen/bin/ask.sh` | `deploy/ask-ollama.sh` |
| `Y:\Strata\run-iq2_xs.bat` | `deploy/run-strata.bat` |
| `~/qwen-image/DEPLOY.md` | `deploy/qwen-image-DEPLOY.md` |

> 仓库内是**副本**，实际运行用的是左边那份；改动请两边同步（或直接反过来：改仓库再复制过去）。
