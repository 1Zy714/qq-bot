# QQ 机器人出图（Qwen-Image-2.1）

给机器人加了一条 `/画` 指令，直接调本地 ComfyUI 出图发进 QQ。
**两条路线都支持**：官方开放平台（`official_bot.py`，你实际在用的）和
NapCat/OneBot v11（`bot.py`）。**全部只走 `127.0.0.1`，不需要公网 IP、不开任何端口。**

---

## 1. 怎么用

```
/画 一只戴着红围巾的小龙，在雨夜里举着灯笼
/img a corgi playing guitar in the rain
画图 赛博朋克风格的上海外滩
```

触发词在 `image_prefixes` 里配（**必须以此开头**才算指令）：

```
/画  #画  /img  /draw
画图  画画  画一张  画一幅  画个  帮我画  生成图片  来张图
```

> ⚠️ **最容易踩的坑：说法不匹配就走了聊天模型。**
> 一开始只配了 `/画`、`#画`、`/img`、`画图`，结果「画一张猫」「画个猫」「帮我画一只猫」
> 全都落到聊天模型，而人设只会回「本小皮可没有图像生成的小魔法呀」——
> 看起来就像出图功能坏了，实际上出图链路一次都没被调用。
> 现在已经把这些自然说法都加进触发词了。
> 反例（「这幅画很好看」「你好」）仍然正确走聊天，没有误触发。

两条路线的触发方式不同：

| 路线 | 怎么触发 |
|---|---|
| **官方平台**（`official_bot.py`） | 群里 **必须 @机器人** 再说 `/画 …`；单聊直接发 `/画 …` |
| NapCat（`bot.py`） | 群里直接发 `/画 …`（出图前缀本身就是触发词，不必 @ 或 `/ai`） |

> 官方平台只推送 `on_group_at_message_create`——**群里不 @ 机器人，消息根本不会到达本进程**，
> 自然也不会出图。这是平台机制，不是 bug。

只有前缀、没写提示词时会回用法说明，不会浪费一次出图。

一次出图的完整时序：

```
用户: @机器人 /画 一只柯基
机器人: 收到～开始出图，大概 40 秒，画好我直接发出来。   ← 立刻回，不让用户干等
        （40~70 秒后）
机器人: [图片]
```

| 情况 | 耗时 |
|---|---|
| 文字聊天回复 | 不变（7.6 tok/s 那套） |
| 出图（ComfyUI 已热） | 约 40 秒 |
| 出图（ComfyUI 冷启动） | 约 70 秒 |
| 出图后第一次聊天 | +28~38 秒（Ollama 冷启动，见下） |

---

## 2. 显存互斥（方案 A）——这是最需要注意的地方

**16 GB 显存装不下两个模型：**

| 模型 | 显存 |
|---|---|
| Ollama 聊天模型 Qwen3.8-27B Q4_K_M | 约 **15.8 GB**（`OLLAMA_KEEP_ALIVE=-1` 常驻不卸） |
| Qwen-Image-2.1 出图峰值 | 约 **14.1 GB** |

所以出图前必须先让聊天模型让位。实现是单向的，只做一件事：

```python
# image_gen.py
await client.post(f"{ollama_root}/api/generate",
                  json={"model": model, "prompt": "", "keep_alive": 0})
```

反向不需要操作：**ComfyUI 空闲时会自己把模型换到内存**（日志里的
`RAM pressure cache`），实测出完图后显存从 13.7 GB 自己回落到约 3 GB。

代价：出图后第一次聊天，Ollama 要重新加载模型（28~38 秒，你 README 里的实测值）。

Ollama 没在跑时卸载会失败，代码里当**正常情况**处理（只记一条日志，继续出图）：

```
ollama 未运行/卸载失败，忽略: All connection attempts failed
```

> 换策略：如果以后觉得冷启动太烦，可以改成让 ComfyUI 用 `--lowvram`
> 并给 Ollama 减少上卡层数，两边共存。`image_use_pe` 之外的开关没有做成配置，
> 因为方案 A 是当前唯一不需要牺牲聊天质量的做法。

---

## 3. 四个踩过的坑

### 3.1 官方平台发图要走「富媒体」，而且 SDK 不给你传本地图

QQ 官方平台发图分两步：先 `POST /v2/groups/{group_openid}/files` 上传拿到
`file_info`，再用 `msg_type=7` + `media={"file_info": …}` 发出去。

问题在于 **botpy 的 `post_group_file()` 只接受 `url`**：

```python
post_group_file(self, group_openid, file_type, url, srv_send_msg=False)
```

本机没有公网 IP，腾讯服务器拉不到我们的图 → 这条路直接死。

但官方接口本身是支持 `file_data`（base64）的，只是 SDK 没暴露出来。
看源码发现它底层就是 `self._http.request(route, json=payload)`，所以绕过封装、
复用 botpy 已鉴权的 http 客户端直接调：

```python
route = Route("POST", "/v2/groups/{group_openid}/files", group_openid=...)
await self.api._http.request(route, json={
    "file_type": 1,                      # 1 = 图片
    "file_data": base64.b64encode(jpeg).decode(),
    "srv_send_msg": False,               # 只上传，不立即发
})
```

> 顺带确认：`botpy.types.message.Media` 是 **TypedDict**，运行时就是普通 dict，
> 所以 `media={"file_info": …}` 能正常被 json 序列化。
> 另外 `post_group_message` 的注释里写着 `msg_type: 7 media 富媒体`。

### 3.2 NapCat 路线的 base64 图片会撞 WebSocket 上限（1 MiB）

第一版直接把 PNG 塞进 base64：1024² RGBA PNG 约 1.17 MB → base64 **1.56 MB**，
NapCat 那边直接 `sent 1009 (message too big)`，图发不出去。

改成**先压 JPEG 再发**（`image_jpeg_quality: 85`，透明通道合成到白底）：

| | 大小 | base64 |
|---|---|---|
| PNG 直发 | 1.17 MB | 1.56 MB ❌ |
| JPEG q85 | 129 KB | **172 KB** ✅ |

小了一个数量级，对 QQ 客户端和移动端也更友好。

### 3.3 必须用 base64，不能用文件路径（NapCat 路线）

**NapCat 跑在 Windows，bot.py 跑在 WSL**——给 NapCat 一个
`/home/lzy959/qwen-image/ComfyUI/output/xxx.png` 它读不到。
所以用 `{"type":"image","data":{"file":"base64://..."}}`。

顺带一提，取图走的是 ComfyUI 的 `/view` HTTP 接口而不是直接读文件，
这样不依赖 WSL 里的输出目录路径。

### 3.4 出图指令要显式绕过群聊触发门槛（NapCat 路线）

`_trigger` 原本只认 `/ai`、`#ai` 或 @，`/画` 会被直接过滤掉、连提示语都收不到。
已在 `_trigger` 里加了一条：命中 `image_prefixes` 也算触发，并原样返回整句
交给 `_image_command` 解析。

---

## 4. 改了哪些文件

| 文件 | 改动 |
|---|---|
| `image_gen.py` | **新增**。ComfyUI 客户端 + 双向显存互斥 + JPEG 压缩 + 共用的 `match_image_command` |
| `official_bot.py` | **官方路线**：`Responder` 加出图分支与 `comfy_dirty`、`_answer_image`；`QQBotClient` 加 `_upload_image` 富媒体上传 + 两个入口的 `image_sender` |
| `bot.py` | **NapCat 路线**：加出图分支（`_image_command`、`_trigger` 放行、`send_image`、`handle_image`、`comfy_dirty`） |
| `config.json` / `config.example.json` | 新增 `image_*` / `comfy_base` / `ollama_root` 配置块 |
| `test_official_image.py` | **新增**。官方路线离线自测（校验富媒体上传参数 + 出图流程，不连腾讯） |
| `test_image_e2e.py` | **新增**。假 NapCat 端到端自测 |
| `test_mutex_e2e.py` | **新增**。双向显存互斥验证 |

出图任务在两条路线里都**复用各自的单并发队列**（NapCat 是 `worker`，官方是 `Responder`），
没有另起并发——ComfyUI 自己也排队，而且出图期间本来就不该再塞聊天请求进 GPU。

出图**不写聊天历史**，所以群友画图不会污染人设对话的上下文。

---

## 5. 配置项

```jsonc
"ollama_root": "http://127.0.0.1:11434",   // 卸载模型用（注意不是 /v1 那个）
"comfy_base": "http://127.0.0.1:8188",
"image_prefixes": ["/画", "#画", "/img", "画图"],

// 权重名必须和 ComfyUI/models/ 下看到的完全一致
"image_unet": "qwen_image_2.1_int8_convrot.safetensors",
"image_clip": "qwen3vl_8b_int8_convrot.safetensors",
"image_vae": "qwen_image_2.1_vae_bf16.safetensors",
"image_pe_clip": "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",

"image_use_pe": true,      // PE 把短句扩写成详细描述，画质明显更好，+20 秒
"image_steps": 25,
"image_size": 1024,        // 改 2048 就是原生 2K，但耗时约 3.4 倍
"image_negative": "",
"image_timeout": 300.0,
"image_jpeg_quality": 85,

"image_ack_text": "收到～开始出图，大概 40 秒，画好我直接发出来。",
"image_usage_text": "用法：/画 一只在雨里弹吉他的柯基",
"image_error_text": "出图失败了，可能是显存不够或者 ComfyUI 没起来。"
```

---

## 6. 前置条件与自测

出图前 ComfyUI 必须在跑：

```bash
bash ~/qwen-image/bin/serve.sh        # 起 ComfyUI（幂等）
bash ~/qq-bot/svc.sh status           # 看三条服务的状态
bash ~/qq-bot/svc.sh start official   # 官方平台路线（你实际用的）
bash ~/qq-bot/svc.sh start napcat     # NapCat 路线（需要 Windows 侧跑 NapCat）
```

两个自测（**都不需要真实 QQ**；官方那个也不连腾讯。出图那步会真实跑 ComfyUI）：

```bash
cd ~/qq-bot
~/.venvs/qqbot/bin/python test_official_image.py   # 官方路线：上传参数 + 出图流程
~/.venvs/qqbot/bin/python test_image_e2e.py        # NapCat 路线：假 NapCat 端到端
~/.venvs/qqbot/bin/python test_mutex_e2e.py        # 双向互斥（会真实加载 27B，约 6 分钟）
```

官方路线自测的期望输出：

```
上传 URL : https://api.sgroup.qq.com/v2/groups/G1/files
file_type: 1  srv_send_msg: False
  ✓ 上传参数正确
[a] 空提示词只回用法，不出图
[b] 人格指令优先于出图
[c] 正常出图
    text seq=1 收到～开始出图，大概 40 秒，画好我直接发出来。
    image seq=2 120,246 字节
    ✓ 先回提示语、再发图，comfy_dirty 已置位
```

依赖：`~/.venvs/qqbot` 里新增了 **Pillow**（用于压缩）。没装也能跑，会退回发 PNG，
但可能撞上 3.2 的大小限制。

---

## 7. 排错：说「不能生图」时先看这三处

### 7.1 先确认它到底有没有走成聊天模型

```bash
grep -c "出图失败" ~/qq-bot/official.log    # 0 = 出图链路从没失败过
grep "出图完成" ~/qq-bot/official.log       # 有记录 = 图确实生成并发出去了
```

如果 `出图失败` 是 0、也没有 `出图完成`，那说明**指令根本没进到出图分支**——
八成是说法不匹配（见第 1 节的坑）或群里没 @ 机器人。
这时日志里会看到人设的普通回复，比如「我可没有图像生成的小魔法呀」。

### 7.2 再确认显存互斥有没有生效

```bash
# 跑一次，看四个时间点的显存与 ollama 加载状态
cd ~/qq-bot && ~/.venvs/qqbot/bin/python - <<'EOF'
import asyncio, subprocess, httpx
from pathlib import Path
import bot as qqbot
def state(t):
    v = subprocess.run(["nvidia-smi","--query-gpu=memory.used,memory.free","--format=csv,noheader"],
                       capture_output=True,text=True,timeout=30).stdout.strip()
    p = subprocess.run([str(Path.home()/"qwen/runtime/ollama/bin/ollama"),"ps"],
                       capture_output=True,text=True,timeout=60).stdout.strip().splitlines()
    print(f"  {t:10s} 显存={v}  ollama={p[1].split()[0] if len(p)>1 else '(未加载)'}")
async def main():
    cfg = qqbot.Config.load(Path('config.json')); ig = qqbot.ImageGen(cfg)
    state("开始")
    async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.request_timeout)) as c:
        print("  unload ->", await ig.release_chat_model(c)); await asyncio.sleep(3)
        state("卸载后")
        png = await ig.generate(c, "一只坐在窗台晒太阳的橘猫")
        print(f"  ✓ 出图 {len(png):,} 字节"); state("出图后")
asyncio.run(main())
EOF
```

本机实测（聊天模型正占着 15.7 GB、只剩 287 MB 的情况下）：

```
开始        显存=15691 MiB, 287 MiB   ollama=qwen3.8-27b-local:latest
unload -> True
卸载后      显存=1843 MiB, 14135 MiB  ollama=(未加载)      ← 释放 13.8 GB
✓ 出图成功 1,650,283 字节, 42.8s
出图后      显存=13808 MiB, 2170 MiB  ollama=(未加载)
```

**结论：显存互斥是有效的，聊天模型占着显存也不影响出图**——出图前会自动把它卸掉。

### 7.3 已知的真实风险：官方路线没有排队

`official_bot.py` 是每个事件直接调 `responder.answer()`，**没有 `bot.py` 那样的单并发队列**。
所以出图那 40~100 秒里如果又来一条聊天消息，Ollama 会重新加载并抢显存，
可能把出图挤爆、或者让聊天请求自己超时。

实测日志里就出现过一次：出图成功（104.4 秒），但并发的聊天请求
`httpx.ReadTimeout`（`self.model.ask()` 抛的，不是出图路径）。

要更稳的话，给 `Responder` 加一把 `asyncio.Lock` 串行化即可（还没做）。

---

## 8. 没做的事

- **多图/图生图**。ComfyUI 那边的编辑链路已经验证可用
  （见 `~/qwen-image/DEPLOY.md` 第 10 节）。要接的话：
  官方平台拿 `message.attachments` 里的图片 URL 下载，
  NapCat 用 `get_image`/`get_file` 拉回本地，再走编辑工作流。还没做。
- **出图任务没有独立的冷却**，和聊天共用 `group_cooldown_seconds`
  （官方路线没有冷却，靠被动回复的 5 次上限天然限制）。
- **被动回复窗口是 5 分钟**。出图 40~70 秒没问题，但如果 ComfyUI 冷启动 +
  队列排队超过 5 分钟，`msg_id` 就失效了，图会发不出去。目前 `image_timeout`
  是 300 秒，正好卡在这个边界上，极端情况下可能踩到。
