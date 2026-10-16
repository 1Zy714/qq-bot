#!/usr/bin/env python3
"""Qwen-Image-2.1 出图：ComfyUI HTTP API 客户端 + 显存互斥。

为什么要显存互斥——本机 16 GB 显存装不下两个模型：

* 聊天模型常驻：Ollama 约 15.8 GB，或 Strata 约 13.1 GB
* Qwen-Image-2.1 出图峰值约 14.1 GB

两条后端路径不同，都封装在这里：

* **Ollama（旧后端）**：出图前调 ``/api/generate``（keep_alive=0）把模型卸下，
  下次聊天请求它自己按需加载（冷启动 28~38 秒），所以不需要"恢复"动作。
* **Strata（当前后端）**：没有卸载 API，只能停掉整个服务
  （``switch.sh image``，顺带释放约 33 GB 内存）；它不会自己回来，
  因此停掉时留一个标记文件，由 ``release_image_model()`` 在下一次聊天请求时
  从后台重新拉起（加载约 39 GB，首次回复会明显变慢）。

配置项见 config.example.json 里的 ``comfy_*`` / ``image_*`` / ``ollama_root``。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

LOG = logging.getLogger("qqbot.image")

# 聊天模型（Strata）与生图模型（ComfyUI）的显存互斥切换脚本
SWITCH_SH = Path.home() / "qwen-image" / "bin" / "switch.sh"
STRATA_URL = "http://127.0.0.1:8080"
# 只有"因出图而停掉 Strata"才留这个标记；主人主动停用聊天模型（没有标记）时，
# 机器人绝不擅自把它拉起来。
STRATA_STOPPED_FLAG = Path(__file__).resolve().parent / ".strata-stopped"


class ImageGenError(RuntimeError):
    """出图失败：服务没起、超时，或 ComfyUI 报错。"""


class ImageGen:
    """把一句提示词变成 PNG 字节。"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        # 出图后后台恢复聊天模型的任务句柄（持有引用，避免被 GC 掉）
        self._restore_task: asyncio.Task[bool] | None = None

    # ---------- 显存互斥 ----------

    async def _strata_alive(self, client: httpx.AsyncClient) -> bool:
        """Strata 服务是否在应答——在跑就说明显存被它占着。"""
        try:
            resp = await client.get(f"{STRATA_URL}/v1/status", timeout=3.0)
            return resp.status_code == 200
        except Exception:  # 没启动、连接被拒都算"不在"
            return False

    async def _run_switch(self, mode: str) -> bool:
        """调用 switch.sh 切换聊天/生图后端（阻塞到脚本返回）。"""
        if not SWITCH_SH.exists():
            LOG.warning("[image] 缺少切换脚本: %s", SWITCH_SH)
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(SWITCH_SH), mode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            text = (out or b"").decode("utf-8", "replace").strip()
            LOG.info("[image] switch.sh %s -> rc=%s | %s", mode, proc.returncode, text[-300:])
            return proc.returncode == 0
        except Exception as error:
            LOG.warning("[image] switch.sh %s 调用失败: %s", mode, error)
            return False

    async def release_chat_model(self, client: httpx.AsyncClient) -> bool:
        """出图前腾显存（与聊天模型互斥）。

        先看 Strata 是否在跑（当前后端）：在跑就停掉整个服务并留下标记，
        好让下一次聊天请求把它拉回来。否则回退到 Ollama 的卸载调用。

        两种情况下"没在跑/卸载失败"都不算错误——这只是优化动作，
        失败了照样尝试出图（ComfyUI 会自己决定能不能跑）。
        """
        if await self._strata_alive(client):
            ok = await self._run_switch("image")
            if ok:
                STRATA_STOPPED_FLAG.touch()
                LOG.info("[image] 已停掉 Strata 腾显存（下次聊天时自动恢复）")
            return ok

        root = str(self.cfg.ollama_root).rstrip("/")
        try:
            resp = await client.post(
                f"{root}/api/generate",
                json={"model": self.cfg.model, "prompt": "", "keep_alive": 0},
                timeout=30.0,
            )
            LOG.info("ollama unload -> %s %s", resp.status_code, resp.text[:100])
            return resp.status_code == 200
        except Exception as error:  # 连接被拒、超时都无所谓
            LOG.info("ollama 未运行/卸载失败，忽略: %s", error)
            return False

    async def release_image_model(self, client: httpx.AsyncClient) -> bool:
        """聊天前把生图模型让开，必要时把聊天模型放回来。

        为什么需要让开：ComfyUI 出完图**不会立刻**释放显存——
        实测出图后它仍占着约 14 GB（日志里的 RAM pressure cache 要闲置很久才回收）。
        如果这时让聊天模型加载，它只能拿到 62% 上卡、38% 跑 CPU（实测），
        聊天会明显变慢。所以聊天前必须显式放开。

        另外：若上一轮出图把 Strata 停掉了（标记文件在），这里在后台重新拉起它，
        不阻塞这条聊天消息——代价是第一次回复可能赶上它还在加载（约 1 分钟）。
        """
        if STRATA_STOPPED_FLAG.exists():
            STRATA_STOPPED_FLAG.unlink(missing_ok=True)
            self._restore_task = asyncio.create_task(self._run_switch("chat"))
            LOG.info("[image] 已发起 Strata 恢复（后台加载，首次回复可能稍慢）")

        base = str(self.cfg.comfy_base).rstrip("/")
        try:
            resp = await client.post(
                f"{base}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=30.0,
            )
            LOG.info("comfy /free -> %s %s", resp.status_code, resp.text[:80])
            return resp.status_code == 200
        except Exception as error:  # ComfyUI 没在跑也一样：不用释放
            LOG.info("comfy /free 失败（忽略）: %s", error)
            return False

    # ---------- workflow ----------

    def _graph(self, prompt: str, steps: int, size: int, use_pe: bool) -> dict[str, Any]:
        """按官方 t2i 模板搭图；use_pe 时先过一遍 PE-T2I 提示词重写。

        参数全部来自本机实测（见 ~/qwen-image/DEPLOY.md）：
        UNETLoader/CLIPLoader(type=qwen_image)/VAELoader + TextEncodeQwenImage21
        + KSampler(cfg=1, euler, simple)。
        """
        graph: dict[str, Any] = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": str(self.cfg.image_unet),
                    "weight_dtype": "default",
                },
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": str(self.cfg.image_clip),
                    "type": "qwen_image",
                    "device": "default",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": str(self.cfg.image_vae)},
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": size, "height": size, "batch_size": 1},
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["4", 1],
                    "latent_image": ["5", 0],
                    "seed": int(time.time()) % (2**31),
                    "steps": steps,
                    "cfg": 1,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1,
                },
            },
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
            "8": {
                "class_type": "SaveImage",
                "inputs": {"images": ["7", 0], "filename_prefix": "qqbot"},
            },
        }
        text_inputs: dict[str, Any] = {
            "clip": ["2", 0],
            "negative_prompt": str(self.cfg.image_negative),
            "resolution": 1024,
        }
        if use_pe:
            # PE-T2I 是 Qwen3.5-VL 9B 微调；官方模板 llm_qwen3_5_text_gen.json
            # 里 Qwen3.5 系 LLM 就是用 CLIPLoader(type=stable_diffusion) 加载的
            graph["10"] = {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": str(self.cfg.image_pe_clip),
                    "type": "stable_diffusion",
                    "device": "default",
                },
            }
            graph["11"] = {
                "class_type": "TextGenerate",
                "inputs": {
                    "clip": ["10", 0],
                    "prompt": prompt,
                    "max_length": 1024,
                    "sampling_mode": "off",
                    "thinking": False,
                    "use_default_template": True,
                },
            }
            text_inputs["prompt"] = ["11", 0]
        else:
            text_inputs["prompt"] = prompt

        graph["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": text_inputs}
        return graph

    # ---------- 出图 ----------

    def _edit_graph(
        self, image_name: str, instruction: str, steps: int, size: int, use_pe: bool
    ) -> dict[str, Any]:
        """按官方 image_edit 模板搭：参考图喂给 TextEncodeQwenImage21。

        两个关键点（见 docs/IMAGES.md 第 3 节，都是实测踩出来的）：

        * 参考图输入在 API 里的 id 是**点号拼接**的 ``images.image_1``，
          写成 ``images: {...}`` 会被静默忽略；
        * ``KSampler.latent_image`` 要接 TextEncodeQwenImage21 的 **latent 输出**
          （第 3 个输出），不能接 EmptyLatentImage，否则编辑会偏移。
        """
        graph: dict[str, Any] = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": str(self.cfg.image_unet), "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": str(self.cfg.image_clip),
                    "type": "qwen_image",
                    "device": "default",
                },
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": str(self.cfg.image_vae)}},
            "20": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["4", 1],
                    "latent_image": ["4", 2],  # 节点自带的 latent，尺寸跟参考图一致
                    "seed": int(time.time()) % (2**31),
                    "steps": steps,
                    "cfg": 1,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1,
                },
            },
            "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
            "8": {
                "class_type": "SaveImage",
                "inputs": {"images": ["7", 0], "filename_prefix": "qqbot_edit"},
            },
        }
        text_inputs: dict[str, Any] = {
            "clip": ["2", 0],
            "negative_prompt": str(self.cfg.image_negative),
            "resolution": int(self.cfg.image_size),
            "vae": ["3", 0],  # 接了 vae 才会生成 reference_latents
            "images.image_1": ["20", 0],
        }
        if use_pe:
            # 编辑任务开 PE-I2I 时要先单独跑一步重写（见 edit()/rewrite_edit_prompt）。
            # 这里进来时 prompt 已经是重写好的最终描述，所以不用再挂 TextGenerate 节点。
            pass
        text_inputs["prompt"] = instruction
        graph["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": text_inputs}
        return graph

    async def upload_image(
        self, client: httpx.AsyncClient, data: bytes, filename: str = "qqbot_ref.jpg"
    ) -> str:
        """把图上传到 ComfyUI 的 input/，返回它认的文件名（给 LoadImage 用）。

        缓存的图必须先进 ComfyUI 的输入目录，工作流才能引用——
        走 HTTP 上传而不是直接写文件，这样不依赖 WSL 里的路径。
        """
        base = str(self.cfg.comfy_base).rstrip("/")
        files = {"image": (filename, data, "image/jpeg")}
        form = {"overwrite": "true", "type": "input"}
        try:
            resp = await client.post(f"{base}/upload/image", files=files, data=form, timeout=120.0)
        except Exception as error:
            raise ImageGenError(f"上传参考图失败（{base}）：{error}") from error
        if resp.status_code != 200:
            raise ImageGenError(f"上传参考图 HTTP {resp.status_code}: {resp.text[:200]}")
        info = resp.json()
        name = info.get("name")
        if not name:
            raise ImageGenError(f"上传参考图没返回文件名: {str(info)[:200]}")
        subfolder = info.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    async def edit(
        self,
        client: httpx.AsyncClient,
        ref_image: bytes,
        instruction: str,
        *,
        steps: int | None = None,
        size: int | None = None,
        use_pe: bool | None = None,
    ) -> bytes:
        """按参考图改：上传参考图 → 编辑工作流 → 返回 PNG 字节。"""
        steps = int(self.cfg.image_steps if steps is None else steps)
        size = int(self.cfg.image_size if size is None else size)
        use_pe = bool(self.cfg.image_edit_use_pe if use_pe is None else use_pe)

        name = await self.upload_image(client, ref_image)
        prompt = instruction
        if use_pe:
            prompt = await self.rewrite_edit_prompt(client, ref_image, instruction)
        LOG.info("编辑参考图=%s 提示词=%r", name, prompt[:60])
        graph = self._edit_graph(name, prompt, steps, size, False)
        return await self._run(client, graph, label=f"编辑 pe={use_pe} steps={steps}")

    async def rewrite_edit_prompt(
        self, client: httpx.AsyncClient, ref_image: bytes, instruction: str
    ) -> str:
        """用 PE-I2I 把编辑指令改写成详细描述。

        单独跑一步（官方推荐的编辑做法）。它的输出形如
        ``</think>{"action":"modify","target":"dragon","details":"..."}``，
        这里把 details 取出来；解析不了就只剥掉 ``</think>`` 之类的标签。
        """
        name = await self.upload_image(client, ref_image, "qqbot_edit_ref.jpg")
        graph = {
            "1": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": str(self.cfg.image_edit_pe_clip),
                    "type": "stable_diffusion",
                    "device": "default",
                },
            },
            "2": {
                "class_type": "TextGenerate",
                "inputs": {
                    "clip": ["1", 0],
                    "prompt": instruction,
                    "max_length": 1024,
                    "sampling_mode": "off",
                    "thinking": False,
                    "use_default_template": True,
                    "image": ["3", 0],
                },
            },
            "3": {"class_type": "LoadImage", "inputs": {"image": name, "upload": "image"}},
            "4": {
                "class_type": "SaveText",
                "inputs": {"text": ["2", 0], "filename_prefix": "qqbot_pe_edit", "format": "txt"},
            },
        }
        text = await self._generate_text(client, graph)
        return _extract_edit_details(text)

    # ---------- 出图 ----------

    async def generate(
        self,
        client: httpx.AsyncClient,
        prompt: str,
        *,
        steps: int | None = None,
        size: int | None = None,
        use_pe: bool | None = None,
    ) -> bytes:
        """新画一张：提交 → 轮询 → 取回 PNG 字节。失败抛 ImageGenError。"""
        steps = int(self.cfg.image_steps if steps is None else steps)
        size = int(self.cfg.image_size if size is None else size)
        use_pe = bool(self.cfg.image_use_pe if use_pe is None else use_pe)
        graph = self._graph(prompt, steps, size, use_pe)
        return await self._run(client, graph, label=f"pe={use_pe} steps={steps} size={size}")

    async def _run(
        self, client: httpx.AsyncClient, graph: dict[str, Any], label: str = ""
    ) -> bytes:
        """提交工作流并轮询到出图。"""
        base = str(self.cfg.comfy_base).rstrip("/")
        deadline = time.monotonic() + float(self.cfg.image_timeout)
        started = time.monotonic()
        try:
            resp = await client.post(f"{base}/prompt", json={"prompt": graph}, timeout=60.0)
        except Exception as error:
            raise ImageGenError(f"连不上 ComfyUI（{base}）：{error}") from error

        if resp.status_code != 200:
            raise ImageGenError(f"提交失败 HTTP {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        if body.get("node_errors"):
            raise ImageGenError(f"节点报错: {str(body['node_errors'])[:300]}")
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ImageGenError(f"没有拿到 prompt_id: {str(body)[:200]}")
        LOG.info("comfy 已提交 prompt_id=%s %s", prompt_id, label)

        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            try:
                hist = (await client.get(f"{base}/history/{prompt_id}", timeout=30.0)).json()
            except Exception:
                continue
            entry = hist.get(prompt_id)
            if not entry:
                continue
            status = (entry.get("status") or {}).get("status_str")
            if status == "error":
                raise ImageGenError(f"ComfyUI 执行报错: {str(entry.get('status'))[:300]}")
            images = [
                img
                for out in (entry.get("outputs") or {}).values()
                for img in (out.get("images") or [])
            ]
            if not images:
                continue
            image = images[-1]
            png = await self._fetch(client, base, image)
            LOG.info(
                "出图完成 %.1fs %s (%d 字节)",
                time.monotonic() - started,
                image.get("filename"),
                len(png),
            )
            return png

        raise ImageGenError(f"出图超时（>{self.cfg.image_timeout}s）")

    async def _generate_text(self, client: httpx.AsyncClient, graph: dict[str, Any]) -> str:
        """跑一个纯文本工作流（PE 重写用），返回 SaveText 落盘的内容。"""
        base = str(self.cfg.comfy_base).rstrip("/")
        deadline = time.monotonic() + float(self.cfg.image_timeout)
        resp = await client.post(f"{base}/prompt", json={"prompt": graph}, timeout=60.0)
        if resp.status_code != 200:
            raise ImageGenError(f"PE 提交失败 HTTP {resp.status_code}: {resp.text[:200]}")
        prompt_id = (resp.json() or {}).get("prompt_id")
        if not prompt_id:
            raise ImageGenError("PE 没有拿到 prompt_id")
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            try:
                hist = (await client.get(f"{base}/history/{prompt_id}", timeout=30.0)).json()
            except Exception:
                continue
            entry = hist.get(prompt_id)
            if not entry:
                continue
            if (entry.get("status") or {}).get("status_str") == "error":
                raise ImageGenError("PE 执行报错")
            for out in (entry.get("outputs") or {}).values():
                texts = out.get("text") or []
                if texts:
                    return texts[0] if isinstance(texts[0], str) else str(texts[0])
        raise ImageGenError("PE 重写超时")

    async def _fetch(self, client: httpx.AsyncClient, base: str, image: dict[str, Any]) -> bytes:
        """经 ComfyUI 的 /view 取图，避免耦合 WSL 文件路径。"""
        query = urllib.parse.urlencode(
            {
                "filename": image.get("filename", ""),
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        resp = await client.get(f"{base}/view?{query}", timeout=120.0)
        if resp.status_code != 200:
            raise ImageGenError(f"取图失败 HTTP {resp.status_code}")
        return resp.content


def encode_jpeg(png: bytes, quality: int = 85) -> bytes:
    """PNG → JPEG，顺带把透明通道合成到白底。

    两条路线都需要：NapCat 路线是为了绕开 WebSocket 的 1 MiB 上限；
    官方平台路线是因为富媒体上传的 base64 越小越稳。
    Pillow 缺失或图片异常时原样返回。
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                canvas = Image.new("RGB", image.size, (255, 255, 255))
                canvas.paste(image, mask=image.split()[-1])
                image = canvas
            else:
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            return buffer.getvalue()
    except Exception:  # Pillow 没装或图片异常：原样发
        return png


def match_image_command(prefixes: Any, text: str) -> str | None:
    """命中出图前缀就返回提示词（可能为空串），否则 None。两条路线共用。"""
    stripped = text.strip()
    for prefix in prefixes:
        if stripped.lower().startswith(str(prefix).lower()):
            return stripped[len(str(prefix)):].strip()
    return None


# 从提取出的提示词两端要剥掉的标点
_TRIM_CHARS = "，,。.：:！!？?、；;·— \t\n"

# 句首残留的请求式填充词（按长度降序，长的先匹配）
_REQUEST_FILLERS = (
    "麻烦你", "能不能", "可不可以", "帮我", "给我", "帮忙", "请帮我",
    "我想", "我要", "想要", "想看", "请", "麻烦", "可以", "能否", "来",
)

# "用第几张"的指代：数字越小越新
_REF_NUMBER_PATTERNS = (
    re.compile(r"第\s*([0-9]{1,2})\s*[张幅个]"),
    re.compile(r"图\s*([0-9]{1,2})"),
    re.compile(r"#\s*([0-9]{1,2})"),
    re.compile(r"([0-9]{1,2})\s*号"),
)
# "用哪张"的口语指代，一律当成最新那张
_REF_LATEST_WORDS = (
    "刚才那张", "刚才那个", "刚才的图", "刚刚那张", "上一张", "上一个", "上张",
    "前一张", "这张", "这个图", "最新那张", "最新的图", "刚画的", "刚那张",
)
# 编辑意图词
_EDIT_WORDS = (
    "改成", "改为", "改一下", "修改成", "修改为", "修改一下", "换成", "换为", "换一下",
    "变成", "变为", "调整", "调成", "调一下", "加上", "加点", "去掉", "删掉", "删除",
    "增加", "添加", "重画", "重新画", "再画", "让它", "给它",
)


@dataclass
class ImageAction:
    """这条消息到底要干什么。

    * ``kind="generate"``：新画一张
    * ``kind="edit"``：拿缓存里的某张图改
    """

    kind: str
    prompt: str
    ref_index: int | None = None  # 1 = 最新那张，2 = 倒数第二张…


class ImageCache:
    """每个会话最近 N 张生成图，供"按之前那张改"使用。

    **只放内存**：进程重启就没了。刻意不落盘——群友的图留在磁盘上不合适，
    而且重跑一遍也就几十秒。

    索引约定：``1`` 是最新的一张，``2`` 是倒数第二张，以此类推。
    """

    def __init__(self, size: int = 10) -> None:
        self.size = max(1, int(size))
        self._sessions: dict[str, deque[dict[str, Any]]] = {}

    def add(self, session: str, image: bytes, prompt: str) -> None:
        buf = self._sessions.setdefault(session, deque(maxlen=self.size))
        buf.appendleft({"image": image, "prompt": prompt, "at": time.time()})

    def get(self, session: str, index: int = 1) -> dict[str, Any] | None:
        buf = self._sessions.get(session)
        if not buf or index < 1 or index > len(buf):
            return None
        return buf[index - 1]

    def count(self, session: str) -> int:
        return len(self._sessions.get(session, ()))

    def describe(self, session: str) -> str:
        """给日志/提示语用的一句话摘要。"""
        buf = self._sessions.get(session)
        if not buf:
            return "(无缓存)"
        return " / ".join(f"{i + 1}.{item['prompt'][:16]}" for i, item in enumerate(buf))


def parse_image_reference(text: str) -> int | None:
    """从话里找出"第几张"。返回 1 起的序号（1=最新），没提到则 None。"""
    for pattern in _REF_NUMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    for word in _REF_LATEST_WORDS:
        if word in text:
            return 1
    return None


def has_edit_intent(text: str) -> bool:
    """话里有没有"改一改"的意思。"""
    return any(word in text for word in _EDIT_WORDS)


def _extract_edit_details(text: str) -> str:
    """从 PE-I2I 的输出里取出真正的编辑描述。

    实测 PE-I2I 的输出形如：

        </think>{"action": "modify", "target": "dragon",
                 "details": "Recolor the cartoon dragon's body from yellow-orange to blue, ..."}

    优先取 JSON 里的 details；取不到就剥掉 ``</think>`` 这类标签后原样返回。
    """
    cleaned = re.sub(r"</?think>", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(cleaned[start:end + 1])
        except Exception:
            data = None
        if isinstance(data, dict):
            for key in ("details", "instruction", "prompt", "description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return cleaned or text


def detect_image_action(cfg: Any, text: str, cache_count: int = 0) -> ImageAction | None:
    """综合判断这条消息要出图、还是要改图；都不是则 None。

    优先级：

    1. 显式命令前缀（``/画``、``/img``…）——永远是"新画一张"；
    2. 提到缓存里的某张图 + 有编辑意图 ——> **改图**；
    3. 有编辑意图 + 缓存里有图（没指明第几张就用最新那张）——> **改图**；
    4. 命中生成关键词 ——> 新画一张。

    第 3 条是"用户改个提示词让它按上一张改"的主要入口，例如
    「把背景换成夜晚」「给它加条围巾」。
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 1) 显式命令前缀：当成新画
    for prefix in cfg.image_prefixes:
        if stripped.lower().startswith(str(prefix).lower()):
            return ImageAction("generate", stripped[len(str(prefix)):].strip())

    # 2)/3) 改图
    if has_edit_intent(stripped):
        ref = parse_image_reference(stripped)
        if ref is not None:
            if 1 <= ref <= cache_count:
                return ImageAction("edit", stripped, ref)
            # 指了第 N 张但缓存里没有——交给下面按新画/聊天处理
        elif cache_count > 0:
            return ImageAction("edit", stripped, 1)

    # 4) 新画
    prompt = detect_image_intent(cfg, stripped)
    if prompt is not None:
        return ImageAction("generate", prompt)
    return None


def detect_image_intent(cfg: Any, text: str) -> str | None:
    """判断这条消息是不是在要图，返回应该拿去画的那句话；不是则 None。

    两种识别方式，都不要求用户打命令：

    1. **显式前缀**（``/画``、``#画``、``/img``、``/draw``）——老习惯仍然可用；
    2. **关键词扫描**（``画一张``、``帮我画``、``生成图片``…）——出现在句子中间也能认出来。

    为什么关键词里**故意不放"画"这个单字**：中文里"动画""漫画""画质""插画""笔画"
    太常见，单字匹配会大量误触发，把正常聊天变成出图。想要单字就写 ``"画 "``
    （带空格），那样只匹配"画 一只猫"这种写法。
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 1) 显式命令前缀
    for prefix in cfg.image_prefixes:
        if stripped.lower().startswith(str(prefix).lower()):
            return stripped[len(str(prefix)):].strip()

    # 2) 关键词扫描：取**最靠左**的匹配，同样靠左时取**更长**的。
    #    这样"帮我画一只猫"会按"帮我画"切得到"一只猫"，而不是按"画一只"切成"帮我 猫"。
    best: tuple[int, int, str] | None = None
    low = stripped.lower()
    for keyword in cfg.image_keywords:
        word = str(keyword).lower()
        if not word:
            continue
        index = low.find(word)
        if index < 0:
            continue
        candidate = (index, -len(word), word)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None

    index, _, word = best
    # 只在句首附近才剥掉请求式措辞；出现在句子中间的保留整句，避免切掉主语/场景。
    if index > 4:
        return stripped

    rest = (stripped[:index] + " " + stripped[index + len(word):]).strip()
    rest = rest.strip(_TRIM_CHARS)
    # 再剥掉句首残留的"我想""能不能""麻烦"这类请求式填充
    for _ in range(3):
        for filler in _REQUEST_FILLERS:
            if rest.startswith(filler):
                rest = rest[len(filler):].strip(_TRIM_CHARS)
                break
        else:
            break
    # 注意：rest 可能是空串。那表示"认出是在要图，但没说画什么"（例如只发了"画图"），
    # 调用方看到空串会回一句用法提示，而不是拿"画图"两个字去当提示词。
    return rest


class ModelPriority:
    """管理"当前哪个模型占 GPU"，也就是主次切换。

    背景：16 GB 单卡装不下两个模型（聊天 27B ≈ 15.8 GB，出图 ≈ 14 GB），
    必须分个先后。语义：

    * ``primary == "chat"``（默认）：聊天为主。出图前卸载聊天模型；聊天前释放
      ComfyUI。**用 PE 重写提示词**，画质优先。
    * ``primary == "image"``：生图为主。**跳过 PE 重写**——这就是"简化生图逻辑"：
      省掉 9.47 GB 的 PE 模型加载和约 20 秒耗时，直接用原话出图；
      出图后不主动回收 ComfyUI，连着画不用重新加载。

    出图后闲置超过 ``image_primary_idle_seconds`` 秒，自动切回聊天为主，
    并把 ComfyUI 占的显存放开（见 :meth:`reaper`）。
    """

    def __init__(self, cfg: Any, image: "ImageGen") -> None:
        self.cfg = cfg
        self.image = image
        self.primary = "image" if str(cfg.primary_model).lower() == "image" else "chat"
        self.last_image_at = 0.0

    # ---------- 查询 ----------

    @property
    def image_primary(self) -> bool:
        return self.primary == "image"

    def use_pe(self) -> bool:
        """当前是否该用 PE 重写。生图为主时跳过——这就是简化。"""
        return bool(self.cfg.image_use_pe) and not self.image_primary

    def idle_expired(self) -> bool:
        """生图为主的模式下，是否已经闲置够久、该让位给聊天了。"""
        if not self.image_primary:
            return False
        window = float(self.cfg.image_primary_idle_seconds)
        if window <= 0 or self.last_image_at <= 0:
            return False
        return (time.monotonic() - self.last_image_at) >= window

    # ---------- 状态迁移 ----------

    def note_image(self) -> bool:
        """记一次出图，并把生图提为主。返回是否发生了切换。"""
        self.last_image_at = time.monotonic()
        if self.image_primary:
            return False
        self.primary = "image"
        LOG.info("生图模型提为主：跳过 PE 重写，出图后不再主动回收显存")
        return True

    def revert_to_chat(self) -> bool:
        """把聊天切回主。返回是否发生了切换。"""
        if not self.image_primary:
            return False
        self.primary = "chat"
        LOG.info("聊天模型切回主：恢复 PE 重写，聊天前释放 ComfyUI 显存")
        return True

    # ---------- 后台回收 ----------

    async def reaper(self, client: httpx.AsyncClient, interval: float = 15.0) -> None:
        """定时检查：生图为主且闲置够久 -> 切回聊天为主并释放 ComfyUI。

        两条路线都在启动时把这个协程挂成后台任务。
        """
        window = float(self.cfg.image_primary_idle_seconds)
        if window <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            if not self.idle_expired():
                continue
            idle = time.monotonic() - self.last_image_at
            if self.revert_to_chat():
                LOG.info("已有 %.0f 秒没出图（阈值 %.0fs），把聊天模型切回主", idle, window)
                await self.image.release_image_model(client)


def to_base64_segment(png: bytes, quality: int = 85) -> dict[str, Any]:
    """把 PNG 压成 JPEG 再包成 OneBot v11 图片段（NapCat 路线用）。

    为什么要压缩：1024² RGBA PNG 约 1.17 MB，base64 后 1.56 MB，实测会撞上
    WebSocket 的 ``message too big``（默认上限 1 MiB）。转 JPEG q85 后
    约 130 KB，base64 不到 180 KB，稳定安全，对 QQ 也更友好。
    """
    payload = encode_jpeg(png, quality)
    return {"type": "image", "data": {"file": "base64://" + base64.b64encode(payload).decode()}}
