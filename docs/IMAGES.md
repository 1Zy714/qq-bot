# QQ 机器人出图（Qwen-Image-2.1）

机器人会在聊天里认出「要画图」的意图，直接调本地 ComfyUI 出图发进 QQ。
**两条路线都支持**：官方开放平台（`official_bot.py`，你实际在用的）和
NapCat/OneBot v11（`bot.py`）。**全部只走 `127.0.0.1`，不需要公网 IP、不开任何端口。**

---

## 1. 怎么用：不用打命令，说到点子就出图

直接像平常说话一样就行：

```
帮我画一只戴着红围巾的小龙，在雨夜里举着灯笼
我想画一张赛博朋克风格的上海外滩
生成图片：一只在雨里弹吉他的柯基
来张图 太空猫
draw a cat playing piano
```

老习惯的命令写法仍然可用：`/画`、`#画`、`/img`、`/draw`。

### 识别规则

两条并列，命中任意一条即出图：

| 方式 | 说明 |
|---|---|
| **显式前缀** | 以 `/画`、`#画`、`/img`、`/draw` 开头 |
| **关键词扫描** | 句子里**任何位置**出现 `画一张`、`帮我画`、`生成图片`、`来张图`…（见 `image_keywords`） |

识别到之后会把请求式措辞剥掉，只把描述送去画：

| 用户说 | 实际拿去画的提示词 |
|---|---|
| `画一只猫` | `猫` |
| `帮我画一只猫` | `一只猫` |
| `我想画一张在雨里弹吉他的柯基` | `在雨里弹吉他的柯基` |
| `生成图片：一只柯基` | `一只柯基` |
| `能不能给我画一幅山水画` | `一幅山水画` |

只说了触发词、没说画什么（例如只发「画图」）→ 回一句用法提示，不会拿「画图」两个字去当提示词。

> ⚠️ **关键词里故意不放单字「画」。**
> 中文里「动画」「漫画」「画质」「插画」「笔画」「刻画」太常见，
> 单字匹配会把正常聊天全变成出图。实测的 13 条反例（「这个动画很好看」「画质怎么样」
> 「我在规划路线」「帮我查一下天气」…）**误触发 0 条**。
> 确实想用单字，就写 `"画 "`（带空格），那样只匹配「画 一只猫」这种写法。

### 两条路线的触发差异

| 路线 | 群里怎么触发 |
|---|---|
| **官方平台**（`official_bot.py`） | **必须 @机器人**；单聊直接说就行 |
| NapCat（`bot.py`） | 直接说就行（配合 `group_reply_all` 时连 @ 都不用） |

> 官方平台默认只推送 `on_group_at_message_create`——**群里不 @ 机器人，消息根本不会到达本进程**。
> 想让群里免 @ 也能出图，要先看 `GROUP-REPLY.md` 开启全量群消息。

一次出图的完整时序：

```
用户: @机器人 帮我画一只柯基
机器人: 收到～开始出图，大概 40 秒，画好我直接发出来。   ← 立刻回，不让用户干等
        （30~70 秒后）
机器人: [图片]
```

| 情况 | 耗时 |
|---|---|
| 文字聊天回复 | 不变（7.6 tok/s 那套） |
| 出图（生图为主 / 跳过 PE） | 约 **30 秒**（实测） |
| 出图（带 PE 重写） | 约 **44 秒**（实测） |
| 出图（ComfyUI 冷启动） | 约 70 秒 |
| 出图后第一次聊天 | +28~38 秒（Ollama 冷启动，见下） |

---

## 1.5 主次切换：谁占着 GPU

16 GB 单卡装不下两个模型，所以必须分个先后。配置项 `primary_model` 决定**初始**谁是主：

```jsonc
"primary_model": "chat",              // chat=聊天为主（默认） / image=生图为主
"image_primary_idle_seconds": 180.0,  // 生图为主时，闲置这么久没再出图就切回聊天为主
```

### 语义

| | `chat`（聊天为主，默认） | `image`（生图为主） |
|---|---|---|
| PE 提示词重写 | ✅ 用（画质优先） | ❌ **跳过**（这就是"简化生图逻辑"） |
| 单图耗时 | 约 44 秒 | 约 **30 秒** |
| 出图后 | 下次聊天前释放 ComfyUI 显存 | 不主动回收，连着画不用重新加载 |
| 适用 | 平时聊天，偶尔画图 | 集中画图、画图机器人 |

### 运行时自动切换

`primary_model` 只是**启动时**的初始值，运行中会自己变：

```
普通聊天                → 主 = chat
有人说"画一张…"          → 主 = image（跳过 PE），记录时间
（继续画图，一直是 image）
180 秒没再出图           → 自动切回 主 = chat，并释放 ComfyUI 占的显存
```

回落由后台协程 `ModelPriority.reaper()` 每 15 秒检查一次，两条路线都在启动时挂上了。

日志里能看到切换：

```
生图模型提为主：跳过 PE 重写，出图后不再主动回收显存
已有 195 秒没出图（阈值 180s），把聊天模型切回主
聊天模型切回主：恢复 PE 重写，聊天前释放 ComfyUI 显存
```

> 想让机器人**一上来就是画图模式**，把 `primary_model` 设成 `"image"` 即可。
> 想让它永远不用 PE，把 `image_use_pe` 设成 `false`（那就是无条件简化）。

---

## 1.8 按之前的图改（图片缓存）

机器人**为每个会话记住最近 10 张**生成图，用户可以据此让模型改图。

### 怎么用

先让它画一张，然后像这样说话就行：

```
画一只戴着红围巾的小龙                     ← 新画，进缓存为「第1张」
把背景改成夜晚，加一点月光                  ← 默认改最新那张
把第2张改成水彩画风格                      ← 指定改哪张
给它加一条蓝围巾                          ← 口语指代，等同"最新那张"
```

识别规则：**有"改"的意图 + 缓存里有图** → 走编辑链路；否则才当新画。

| 提到的指代 | 指向 |
|---|---|
| `第2张` `图2` `#2` `2号` | 对应序号（**1 = 最新**，2 = 倒数第二…） |
| `刚才那张` `上一张` `这张` `刚画的` | 最新那张 |
| 没提第几张 | 最新那张 |

编辑意图词：`改成` `改为` `换成` `变成` `调整` `加上` `去掉` `删除` `添加` `重画` `给它` …
（完整列表见 `image_gen.py` 的 `_EDIT_WORDS`）

缓存为空时说要改图，会回「我这儿还没有可改的图」并转入正常聊天，不会报错。
指名了第 9 张但缓存里只有 3 张也一样处理。

### 实测效果

```
[1] 画一只戴着红围巾的小龙        → 出图，缓存 1 张（24.2s，用 PE）
[2] 把背景改成夜晚，加一点月光     → 改图，缓存 2 张（16.1s）
[3] 把第2张改成水彩画风格         → 改图，缓存 3 张（14.1s）
```

第 2 步的结果是**同一只龙**（姿势、脸、围巾完全一致），只有背景换成了星空月夜
——说明参考图确实被用上了，不是重新画了一只。

### 实现要点

编辑走 ComfyUI 的编辑链路。两个坑（详见第 3 节）：

* 缓存的图要先 `POST /upload/image` 送进 ComfyUI 的 `input/`，`LoadImage` 才引得到；
  走 HTTP 而不是直接写文件，不依赖 WSL 路径。
* 参考图输入在 API 里的 id 是**点号拼接**的 `images.image_1`；
  `KSampler.latent_image` 要接 `TextEncodeQwenImage21` 的 **latent 输出**（第 3 个输出），
  不能接 `EmptyLatentImage`，否则编辑会偏移。

### 配置

```jsonc
"image_cache_size": 10,       // 每个会话记最近多少张
"image_edit_use_pe": false,   // 是否先跑 PE-I2I 重写编辑指令（更稳，多约 10 秒）
"image_edit_pe_clip": "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors",
"image_edit_ack_text": "收到～按你说的改，大概 40 秒。",
"image_edit_no_ref_text": "我这儿还没有可改的图，先让我画一张吧～",
```

> ⚠️ **缓存只在内存里**，进程重启就清空。刻意不落盘——群友的图留在磁盘上不合适，
> 而且重画一张也就几十秒。

### 自测

```bash
~/.venvs/qqbot/bin/python ~/qq-bot/test_edit_cache_e2e.py
```

会真实跑两次 ComfyUI（约 1 分钟），验证「出图 → 进缓存 → 按缓存改图 → 再进缓存」
以及缓存上限；出图存到 `/tmp/e2e_step{1,2,3}.png`（其实是 JPEG）。

---

## 2. 显存互斥——这是最需要注意的地方

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
| `image_gen.py` | **新增**。ComfyUI 客户端 + 双向显存互斥 + JPEG 压缩 + `detect_image_intent()` 关键字识别 + `ModelPriority` 主次状态机 |
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

// 触发方式一：显式命令前缀
"image_prefixes": ["/画", "#画", "/img", "/draw"],
// 触发方式二：关键词扫描（句子中间出现即可）。故意不放单字「画」
"image_keywords": ["画一张", "画一幅", "画一个", "画一只", "画个", "画只", "画张",
                   "画一下", "帮我画", "给我画", "帮忙画", "画出来", "生成图片",
                   "生成一张", "来张图", "画图", "画画", "draw a ", "paint a ", ...],

// 主次切换
"primary_model": "chat",              // chat=聊天为主（默认） / image=生图为主
"image_primary_idle_seconds": 180.0,  // 生图为主时闲置这么久就切回聊天为主

// 权重名必须和 ComfyUI/models/ 下看到的完全一致
"image_unet": "qwen_image_2.1_int8_convrot.safetensors",
"image_clip": "qwen3vl_8b_int8_convrot.safetensors",
"image_vae": "qwen_image_2.1_vae_bf16.safetensors",
"image_pe_clip": "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",

"image_use_pe": true,      // PE 把短句扩写成详细描述，画质更好但 +14 秒；生图为主时自动跳过
"image_steps": 25,
"image_size": 1024,        // 改 2048 就是原生 2K，但耗时约 3.4 倍
"image_negative": "",
"image_timeout": 300.0,
"image_jpeg_quality": 85,

"image_ack_text": "收到～开始出图，大概 40 秒，画好我直接发出来。",
"image_usage_text": "用法：直接说「画一张在雨里弹吉他的柯基」就行",
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
