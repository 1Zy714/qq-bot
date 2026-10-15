#!/usr/bin/env python3
"""OneBot v11 反向 WebSocket → Ollama(OpenAI 兼容) 的 QQ 群聊机器人适配器。

设计约束来自本机实测，改动前先读 ~/qq-bot/docs/PLAN.md 第 1、6 节：

* 本机模型只有 ``reasoning_effort: "none"`` 能关闭思考，必须显式发送；
* Ollama 侧 ``OLLAMA_NUM_PARALLEL=1``，因此这里只跑一个 worker 串行处理；
* 解码 7.6 tok/s、prefill 97.7 tok/s，回复必须短、历史必须小。

用法::

    python bot.py              # 常驻，等 NapCat 反向连进来
    python bot.py --selftest   # 只测 Ollama 这一半，不需要 NapCat
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

try:  # websockets >= 14 的新实现
    from websockets.asyncio.server import serve as ws_serve
except ImportError:  # websockets < 14
    from websockets import serve as ws_serve  # type: ignore[no-redef]

from web_tools import chat_with_tools  # noqa: E402  与本文件同目录
from image_gen import (  # noqa: E402  与本文件同目录
    ImageCache,
    ImageGen,
    ImageGenError,
    ModelPriority,
    detect_image_action,
    detect_image_intent,
    to_base64_segment,
)
from user_memory import (  # noqa: E402  按 uid 记住每个人（与官方路线共用同一份档案）
    Speaker,
    UserMemory,
    parse_memory_marker,
)

HERE = Path(__file__).resolve().parent
LOG = logging.getLogger("qqbot")

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 6199,
    "token": "",
    "ollama_base": "http://127.0.0.1:11434/v1",
    "model": "qwen3.8-27b-local:latest",
    "system_prompt": (
        "你是 QQ 群里的本地小助手，名字叫小千。"
        "用中文口语回答，最多两三句话，不要 Markdown、不要编号列表。"
        "不知道就直说不知道，不要编。"
    ),
    "max_tokens": 160,
    "temperature": 0.7,
    "history_turns": 4,
    "request_timeout": 90.0,
    "queue_limit": 3,
    "max_reply_chars": 400,
    "max_prompt_chars": 500,
    "trigger_prefixes": ["/ai", "#ai"],
    "respond_to_private": True,
    "group_cooldown_seconds": 5.0,
    # 群里不 @ 也回（全量群消息）。官方路线还要在开放平台后台开「接收所有消息」；
    # NapCat 路线还要在 NapCat 里打开群消息推送。开着很吃 GPU，配合冷却用。
    "group_reply_all": False,
    "reply_with_at": True,
    "busy_text": "我这边排队有点长，稍等一下再叫我～",
    "error_text": "本地模型好像卡住了，等会儿再试试。",
    "reset_words": ["重置", "reset", "清空"],
    # 表情包：自动从群聊图片里收集，回复时由模型用 [表情:标签] 标记挑一张发出去。
    # sticker_dir 留空则用 qq-bot/stickers。
    "sticker_enabled": True,
    "sticker_dir": "",
    # 长期记忆：按 uid 记住每个人的昵称、发言次数和模型留下的印象。
    "user_memory_enabled": True,
    "user_memory_path": "",
    "personas": {},
    "persona": "",
    "persona_words": ["人格", "人设", "persona"],
    "persona_alias_words": [
        "切换成", "切换到", "切换为", "切换",
        "换成", "换为", "扮演", "变成", "变为",
    ],
    "persona_aliases": {},
    "tools_enabled": True,

    # ---- 出图（Qwen-Image-2.1 via ComfyUI）----
    # 显存互斥：出图前叫 Ollama 卸载模型腾显存，出完图 Ollama 按需冷启动。
    # 权重名要跟 ComfyUI/models/ 下看到的完全一致。详见 ~/qq-bot/docs/IMAGES.md。
    "ollama_root": "http://127.0.0.1:11434",
    "comfy_base": "http://127.0.0.1:8188",
    # 显式命令前缀（老习惯，仍然可用）
    "image_prefixes": ["/画", "#画", "/img", "/draw"],
    # 关键词扫描：出现在句子中间也能识别，不需要打命令。
    # 故意不放"画"这个单字——"动画/漫画/画质/插画"会大量误触发。
    "image_keywords": [
        "画一张", "画一幅", "画一个", "画一只", "画个", "画只", "画张", "画一下",
        "帮我画", "给我画", "帮忙画", "画出来", "画一幅画",
        "生成图片", "生成一张", "生成一幅", "生成个图", "生成图像",
        "来张图", "来一张图", "来幅画", "出个图", "画图", "画画",
        "draw a ", "draw an ", "paint a ", "generate an image", "make an image",
    ],
    # 主次切换：谁占着 GPU。chat=聊天为主（默认），image=生图为主。
    "primary_model": "chat",
    # 生图为主时，闲置这么久没再出图就把聊天切回主
    "image_primary_idle_seconds": 180.0,
    "image_unet": "qwen_image_2.1_int8_convrot.safetensors",
    "image_clip": "qwen3vl_8b_int8_convrot.safetensors",
    "image_vae": "qwen_image_2.1_vae_bf16.safetensors",
    "image_pe_clip": "qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors",
    # 改图（按缓存的上一张改）
    "image_cache_size": 10,   # 每个会话记最近多少张
    "image_edit_pe_clip": "qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors",
    "image_edit_use_pe": False,  # PE-I2I 重写编辑指令，更稳但多跑一步（约 10 秒）
    "image_edit_ack_text": "收到～按你说的改，大概 40 秒。",
    "image_edit_no_ref_text": "我这儿还没有可改的图，先让我画一张吧～",
    "image_use_pe": True,
    "image_steps": 25,
    "image_size": 1024,
    "image_negative": "",
    "image_timeout": 300.0,
    "image_jpeg_quality": 85,
    "image_ack_text": "收到～开始出图，大概 40 秒，画好我直接发出来。",
    "image_usage_text": "用法：直接说「画一张在雨里弹吉他的柯基」就行",
    "image_error_text": "出图失败了，可能是显存不够或者 ComfyUI 没起来。",
}


@dataclass
class Config:
    """运行配置，字段含义见 config.example.json。"""

    values: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))

    def __getattr__(self, item: str) -> Any:  # 让 cfg.model 这种写法可用
        try:
            return self.values[item]
        except KeyError as exc:  # pragma: no cover - 配置拼写错误要立刻暴露
            raise AttributeError(item) from exc

    @classmethod
    def load(cls, path: Path) -> "Config":
        """读 config.json；不存在时用内置默认值。"""
        merged = dict(DEFAULTS)
        if path.exists():
            merged.update(json.loads(path.read_text("utf-8")))
        return cls(merged)


def log_setup() -> None:
    """行缓冲日志，便于 run.sh 重定向到文件后 tail。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def clean_reply(text: str) -> str:
    """剥掉可能漏出来的推理块与包裹符号，群里只发正文。"""
    out = text.strip()
    for open_tag, close_tag in (("<think>", "</think>"), ("<thinking>", "</thinking>")):
        while open_tag in out and close_tag in out:
            head, _, rest = out.partition(open_tag)
            _, _, tail = rest.partition(close_tag)
            out = (head + tail).strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'“”「」":
        out = out[1:-1].strip()
    return out


def segments_to_text(segments: list[dict[str, Any]]) -> str:
    """把 OneBot array 消息里的文本拼起来，@ 段转成可读占位。"""
    parts: list[str] = []
    for seg in segments:
        kind = seg.get("type")
        data = seg.get("data") or {}
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind == "at":
            parts.append(f"@{data.get('qq', '')} ")
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "face":
            parts.append("[表情]")
    return "".join(parts)


class PersonaStore:
    """多套人设：定义写在 config.json 的 ``personas``，当前生效的记在 state 文件里。

    ``personas`` 为空时退回单套 ``system_prompt``，老配置照常可用。
    """

    def __init__(self, cfg: Config, state_path: Path) -> None:
        self.cfg = cfg
        self.state_path = state_path
        self.personas: dict[str, str] = {
            str(k): str(v) for k, v in (cfg.personas or {}).items()
        }
        self.words: list[str] = [str(w) for w in (cfg.persona_words or [])]
        self.aliases: list[str] = [str(w) for w in (cfg.persona_alias_words or [])]
        # 别称 → 正式键名，例如「雪之下雪乃」→「雪乃」，省得记全名
        self.alias_names: dict[str, str] = {
            str(k): str(v) for k, v in (cfg.persona_aliases or {}).items()
        }
        self.current: str = self._initial_name()

    def _resolve(self, name: str) -> str:
        """把别称解析成正式的人设键名。"""
        return self.alias_names.get(name, name)

    def _initial_name(self) -> str:
        """优先级：state 文件 > config 的 persona > 第一个。"""
        if not self.personas:
            return ""
        saved = ""
        if self.state_path.exists():
            saved = self.state_path.read_text("utf-8").strip()
        if saved in self.personas:
            return saved
        wanted = self._resolve(str(self.cfg.persona or ""))
        if wanted in self.personas:
            return wanted
        return next(iter(self.personas))

    def prompt(self) -> str:
        """当前人设文本。"""
        if self.personas and self.current in self.personas:
            return self.personas[self.current]
        return str(self.cfg.system_prompt)

    def label(self) -> str:
        """当前人设名。"""
        return self.current or "默认"

    def switch(self, name: str) -> tuple[bool, str]:
        """切换人设并落盘，重启后仍然生效。"""
        key = self._resolve(name)
        if key not in self.personas:
            available = "、".join(self.personas) or "（无）"
            return False, f"没有「{name}」这号人设哦。现有：{available}"
        self.current = key
        try:
            self.state_path.write_text(key + "\n", "utf-8")
        except OSError:
            pass  # 落盘失败不影响本次切换
        return True, f"人设已切换：{key}"

    def handle_command(self, text: str) -> tuple[bool, str]:
        """识别切换人设的指令，返回 (是否已处理, 给用户的回复)。

        两类关键词：

        * **严格**（``persona_words``，如「人格 小皮」）——没点名到的人设会明确报错，
          便于用户发现自己打错了。
        * **宽松**（``persona_alias_words``，如「换成小皮」「扮演暴脾气」）——只在
          后面确实跟着一个已知人设时才拦下；否则当成普通聊天放过，避免把
          「换成什么好呢」这种日常话误判成指令。
        """
        stripped = text.strip()
        candidates = [(w, True) for w in self.words] + [(w, False) for w in self.aliases]
        for word, strict in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
            if not word or not stripped.lower().startswith(word.lower()):
                continue
            if not self.personas:
                return True, "现在只有一套人设，没得切。"
            rest = stripped[len(word):].strip(" 　：:，,。.、")
            # 「人格切换成小皮」这类叠词：再剥一层切换动词
            rest = re.sub(r"^(?:切换|换成|换为|变为|变成|为|成|到)+", "", rest)
            rest = rest.strip(" 　：:，,。.、")
            if not rest:
                if not strict:
                    continue  # 光说「切换」没有目标，当普通聊天
                available = "、".join(self.personas)
                return True, f"当前人设：{self.label()}。可选：{available}"
            if rest in ("随机", "random", "任意"):
                return self.switch(random.choice(list(self.personas)))
            switched, message = self.switch(rest)
            if switched or strict:
                return True, message
            return False, ""  # 宽松关键词 + 未知人设 → 不打断正常对话
        return False, ""


class Bot:
    """反向 WS 服务端 + 单并发推理队列 + 每会话历史。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.personas = PersonaStore(cfg, HERE / "persona.state")
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.history: dict[str, deque[dict[str, str]]] = {}
        self.last_reply_at: dict[str, float] = {}
        self.worker_task: asyncio.Task[None] | None = None
        self.image = ImageGen(cfg)
        # 每个会话最近若干张生成图，供"按上一张改"使用（只在内存里）
        self.cache = ImageCache(int(cfg.image_cache_size))
        # 主次切换：谁占着 GPU（chat=聊天为主 / image=生图为主，闲置后自动回落）
        self.priority = ModelPriority(cfg, self.image)
        # 出过图之后 ComfyUI 会占着约 14 GB 不放，下次聊天前要先放开
        self.comfy_dirty = False
        self.reaper_task: asyncio.Task[None] | None = None
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(cfg.request_timeout))
        # 按 uid 记住每个人。与官方路线指向同一份档案，两边认的是同一批人。
        self.memory: UserMemory | None = None
        if bool(cfg.values.get("user_memory_enabled", True)):
            mem_path = cfg.values.get("user_memory_path") or (HERE / "memory" / "users.json")
            self.memory = UserMemory(Path(mem_path))

    # ---------- 记忆 ----------

    def _speaker(self, event: dict[str, Any]) -> Speaker:
        """从 OneBot 事件里抽出发言人。OneBot 直接给了昵称，不用像官方路线那样打补丁。"""
        sender = event.get("sender") or {}
        scene = "group" if event.get("message_type") == "group" else "c2c"
        # 群名片优先（群里显示的那个），退回 QQ 昵称
        return Speaker(
            uid=str(event.get("user_id") or ""),
            name=str(sender.get("card") or sender.get("nickname") or ""),
            scene=scene,
        )

    def _prompt_prefix(self, speaker: Speaker) -> str:
        """【群友 昵称】/【私聊 昵称】——让模型一眼看出在跟谁说话。"""
        if self.memory is None:
            return ""
        profile = self.memory.touch(speaker.uid, speaker.name)
        self.memory.save()
        scene = "群友" if speaker.scene == "group" else "私聊"
        return f"[{scene} {profile.display}] "

    def memory_command(self, text: str) -> tuple[bool, str]:
        """「记忆」/「忘掉 xxx」，与官方路线同一套语义（严格前缀，不误伤日常话）。"""
        if self.memory is None:
            return False, ""
        stripped = text.strip()
        if stripped in ("记忆", "记忆列表"):
            return True, self.memory.describe()[: int(self.cfg.max_reply_chars)]
        for prefix in ("忘掉", "忘记", "删除记忆"):
            if not stripped.startswith(prefix):
                continue
            target = stripped[len(prefix):].strip(" 　：:，,。.、")
            if not target:
                return True, "用法：忘掉 昵称（或「忘掉全部」）"
            if target in ("全部", "所有", "所有人", "全部记忆"):
                return True, f"好，{self.memory.clear()} 个人的记忆都删了。"
            removed = self.memory.forget(target)
            self.memory.save()
            return True, (f"已经把「{target}」从记忆里删掉了。" if removed
                          else f"我没记住「{target}」，不用删。")
        return False, ""

    # ---------- Ollama ----------

    async def ollama_chat(self, messages: list[dict[str, Any]]) -> str:
        """调本机模型；问到最新信息时它会自己发起联网工具调用。"""
        content = await chat_with_tools(
            self.client, self.cfg, messages, max_rounds=2 if self.cfg.tools_enabled else 0
        )
        content = clean_reply(content)
        if not content:
            raise RuntimeError("模型返回空正文（思考没关掉？检查 reasoning_effort）")
        return content

    # ---------- 事件入口 ----------

    def _authorized(self, ws: Any) -> bool:
        token = str(self.cfg.token or "")
        if not token:
            return True
        request = getattr(ws, "request", None)
        headers = getattr(request, "headers", None) or getattr(ws, "request_headers", None) or {}
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        if auth.split()[-1:] == [token]:
            return True
        path = getattr(request, "path", None) or getattr(ws, "path", "") or ""
        query = urllib.parse.parse_qs(urllib.parse.urlparse(str(path)).query)
        return query.get("access_token", [""])[0] == token

    def _trigger(self, event: dict[str, Any], text: str) -> tuple[bool, str]:
        """判断是否该回，并给出剥掉触发词后的提问。"""
        if event.get("message_type") == "private":
            if not self.cfg.respond_to_private:
                return False, ""
            return True, text.strip()

        self_id = str(event.get("self_id", ""))
        mentioned = any(
            seg.get("type") == "at" and str((seg.get("data") or {}).get("qq", "")) == self_id
            for seg in event.get("message", [])
        )
        stripped = text.strip()
        for prefix in self.cfg.trigger_prefixes:
            if stripped.lower().startswith(str(prefix).lower()):
                return True, stripped[len(str(prefix)):].strip()
        # 出图/改图本身就是触发词：群里直接说就行，不必先 @ 或打 /ai
        if detect_image_action(self.cfg, stripped, self.cache.count(self._session_key(event))):
            return True, stripped
        if mentioned:
            for seg in event.get("message", []):
                if seg.get("type") == "at":
                    stripped = stripped.replace(f"@{self_id}", "", 1)
            return True, stripped.strip()
        # 全量群消息模式：群里不 @ 也回（放在所有规则之后，不影响原有触发方式）
        if self.cfg.group_reply_all:
            return True, stripped
        return False, ""

    def _session_key(self, event: dict[str, Any]) -> str:
        if event.get("message_type") == "group":
            return f"group:{event.get('group_id')}"
        return f"private:{event.get('user_id')}"

    def _image_command(self, text: str) -> str | None:
        """判断是否要出图（前缀或关键词），返回提示词；不是则 None。"""
        return detect_image_intent(self.cfg, text)

    async def on_event(self, ws: Any, event: dict[str, Any]) -> None:
        """处理一条 OneBot 事件。"""
        if event.get("post_type") != "message":
            return
        text = segments_to_text(event.get("message") or [])
        should, prompt = self._trigger(event, text)
        session = self._session_key(event)

        if should and prompt.strip() in [str(w) for w in self.cfg.reset_words]:
            self.history.pop(session, None)
            await self.send(ws, event, "上下文已清空。")
            return
        if not should or not prompt.strip():
            return

        # 人格指令优先级最高：不受冷却和排队影响，随时可切
        handled, command_reply = self.personas.handle_command(prompt)
        if handled:
            LOG.info("persona command session=%s -> %s", session, command_reply)
            await self.send(ws, event, command_reply)
            return

        # 记忆指令同理：查询/删除都不经模型
        handled, command_reply = self.memory_command(prompt)
        if handled:
            LOG.info("memory command session=%s -> %s", session, command_reply[:40])
            await self.send(ws, event, command_reply)
            return

        cooldown = float(self.cfg.group_cooldown_seconds)
        if event.get("message_type") == "group" and cooldown > 0:
            if time.monotonic() - self.last_reply_at.get(session, 0.0) < cooldown:
                LOG.info("cooldown skip session=%s", session)
                return

        if self.queue.qsize() >= int(self.cfg.queue_limit):
            LOG.warning("queue full (%s), rejecting session=%s", self.queue.qsize(), session)
            await self.send(ws, event, str(self.cfg.busy_text))
            return

        # 出图/改图：复用同一个串行队列，但走独立分支，
        # 这样不会写进聊天历史、也不会被模型当成聊天内容回答。
        action = detect_image_action(self.cfg, prompt, self.cache.count(session))
        if action is not None:
            if not action.prompt:
                await self.send(ws, event, str(self.cfg.image_usage_text))
                return
            if action.kind == "edit" and self.cache.get(session, action.ref_index or 1) is None:
                await self.send(ws, event, str(self.cfg.image_edit_no_ref_text))
                return
            text = action.prompt[: int(self.cfg.max_prompt_chars)]
            LOG.info(
                "enqueue %s session=%s ref=%s prompt=%r",
                action.kind, session, action.ref_index, text[:60],
            )
            await self.send(
                ws,
                event,
                str(self.cfg.image_edit_ack_text if action.kind == "edit" else self.cfg.image_ack_text),
            )
            await self.queue.put(
                {
                    "kind": "image",
                    "edit": action.kind == "edit",
                    "ref_index": action.ref_index or 1,
                    "ws": ws,
                    "event": event,
                    "session": session,
                    "prompt": text,
                }
            )
            return

        prompt = prompt[: int(self.cfg.max_prompt_chars)]
        LOG.info("enqueue session=%s prompt=%r qsize=%s", session, prompt[:60], self.queue.qsize())
        await self.queue.put({
            "ws": ws,
            "event": event,
            "session": session,
            "prompt": prompt,
            # 发言人在这里就抽好：worker 线程不必再解析 OneBot 事件结构
            "speaker": self._speaker(event),
        })

    # ---------- 发送 ----------

    async def send(self, ws: Any, event: dict[str, Any], text: str) -> None:
        """按来源会话回一句话。"""
        if event.get("message_type") == "group":
            action, params = "send_group_msg", {"group_id": event.get("group_id")}
        else:
            action, params = "send_private_msg", {"user_id": event.get("user_id")}

        if event.get("message_type") == "group" and self.cfg.reply_with_at:
            message: list[dict[str, Any]] = [
                {"type": "at", "data": {"qq": str(event.get("user_id"))}},
                {"type": "text", "data": {"text": " " + text}},
            ]
        else:
            message = [{"type": "text", "data": {"text": text}}]

        payload = {"action": action, "params": params | {"message": message}, "echo": str(time.time())}
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            LOG.exception("send failed")

    async def send_image(self, ws: Any, event: dict[str, Any], png: bytes) -> None:
        """把 PNG 作为图片段发回来源会话。

        用 base64 而不是文件路径：NapCat 在 Windows、bot.py 在 WSL，
        WSL 路径它读不到。
        """
        if event.get("message_type") == "group":
            action, params = "send_group_msg", {"group_id": event.get("group_id")}
        else:
            action, params = "send_private_msg", {"user_id": event.get("user_id")}

        message: list[dict[str, Any]] = []
        if event.get("message_type") == "group" and self.cfg.reply_with_at:
            message.append({"type": "at", "data": {"qq": str(event.get("user_id"))}})
        message.append(to_base64_segment(png, quality=int(self.cfg.image_jpeg_quality)))

        payload = {"action": action, "params": params | {"message": message}, "echo": str(time.time())}
        try:
            await ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            LOG.exception("send image failed")

    # ---------- 队列与生成 ----------

    async def worker(self) -> None:
        """唯一的推理消费者：保证同一时刻只有一个请求打在 Ollama 上。"""
        while True:
            job = await self.queue.get()
            try:
                await self.handle(job)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("handle failed")
                await self.send(job["ws"], job["event"], str(self.cfg.error_text))
            finally:
                self.queue.task_done()

    async def handle(self, job: dict[str, Any]) -> None:
        """取历史 → 推理 → 回消息 → 记历史。"""
        if job.get("kind") == "image":
            await self.handle_image(job)
            return
        session = job["session"]
        # 双向显存互斥的另一半：出过图就让 ComfyUI 把显存放开，
        # 否则 Ollama 加载时只能拿到部分上卡（实测 62% GPU / 38% CPU），聊天会变慢
        if self.comfy_dirty:
            await self.image.release_image_model(self.client)
            self.comfy_dirty = False
        turns = max(1, int(self.cfg.history_turns)) * 2
        buf = self.history.setdefault(session, deque(maxlen=turns))
        system = self.personas.prompt()
        if self.memory is not None:
            system += self.memory.digest()
        messages = [{"role": "system", "content": system}]
        messages.extend(dict(item) for item in buf)

        # 提问打上发言人标签：标签放 user 消息而不是 system，
        # system 保持逐字稳定，前缀缓存才不会被每条消息打掉。
        speaker = job.get("speaker")
        display = ""
        if self.memory is not None and isinstance(speaker, Speaker):
            prefix = self._prompt_prefix(speaker)
            display = prefix
            prompt = f"{prefix}{job['prompt']}"
        else:
            prompt = job["prompt"]
        messages.append({"role": "user", "content": prompt})

        started = time.monotonic()
        reply = await self.ollama_chat(messages)
        elapsed = time.monotonic() - started
        # 记忆标记要在截断之前摘掉，否则会被切断成 "[记住:xxx" 发进群里
        reply, notes = parse_memory_marker(reply)
        if notes and self.memory is not None and isinstance(speaker, Speaker):
            added = self.memory.add_notes(speaker.uid, notes)
            if added:
                self.memory.save()
                LOG.info("记住 %s：%s", display.strip("[] ") or speaker.uid, "；".join(notes))
        reply = reply[: int(self.cfg.max_reply_chars)]
        LOG.info("reply session=%s in %.1fs: %r", session, elapsed, reply[:60])

        buf.append({"role": "user", "content": prompt})
        buf.append({"role": "assistant", "content": reply})
        self.last_reply_at[session] = time.monotonic()
        await self.send(job["ws"], job["event"], reply)

    async def handle_image(self, job: dict[str, Any]) -> None:
        """出图或改图：先腾显存（与 Ollama 互斥），再交给 ComfyUI，最后发图。"""
        session = job["session"]
        is_edit = bool(job.get("edit"))
        # 先取"当前是否该用 PE"，**再**提为主。顺序反了的话，第一次出图就会被
        # 判成"生图已为主"而跳过 PE——可那时它还没占据主导，白白损失画质。
        use_pe = self.priority.use_pe()
        promoted = self.priority.note_image()
        try:
            # 16 GB 显存装不下 27B 聊天模型 + 出图模型，出图前让位
            await self.image.release_chat_model(self.client)
            if is_edit:
                reference = self.cache.get(session, int(job.get("ref_index") or 1))
                if reference is None:
                    raise ImageGenError("参考图已不在缓存里（可能被新图挤掉了）")
                png = await self.image.edit(self.client, reference["image"], job["prompt"])
            else:
                # 生图为主时 use_pe 为 False —— 跳过 PE 重写，省约 14 秒
                png = await self.image.generate(self.client, job["prompt"], use_pe=use_pe)
        except Exception:
            LOG.exception("image failed session=%s edit=%s", session, is_edit)
            await self.send(job["ws"], job["event"], str(self.cfg.image_error_text))
            return
        finally:
            self.last_reply_at[session] = time.monotonic()
        # 存进缓存，下次可以"按这张改"
        self.cache.add(session, png, job["prompt"])
        LOG.info("缓存 %s 现有 %d 张: %s", session, self.cache.count(session), self.cache.describe(session))
        # 出完图 ComfyUI 会占着约 14 GB 不放，标记一下，等下次聊天前释放
        self.comfy_dirty = True
        if promoted:
            LOG.info("本次出图把生图提为主，之后的出图将跳过 PE")
        await self.send_image(job["ws"], job["event"], png)

    # ---------- 服务 ----------

    async def handler(self, ws: Any) -> None:
        """一个 NapCat 反向 WS 连接的生命周期。"""
        if not self._authorized(ws):
            LOG.warning("rejected connection: bad token")
            await ws.close(code=1008, reason="bad token")
            return
        LOG.info("napcat connected")
        try:
            async for raw in ws:
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    LOG.warning("bad json: %r", str(raw)[:120])
                    continue
                await self.on_event(ws, event)
        except Exception:
            LOG.exception("connection error")
        finally:
            LOG.info("napcat disconnected")

    async def run(self) -> None:
        """起 worker、主次回收协程与 WS 服务，直到被取消。"""
        self.worker_task = asyncio.create_task(self.worker())
        # 生图为主时，闲置够久自动把聊天切回主并释放 ComfyUI
        self.reaper_task = asyncio.create_task(self.priority.reaper(self.client))
        async with ws_serve(self.handler, self.cfg.host, int(self.cfg.port), ping_interval=30):
            LOG.info(
                "listening ws://%s:%s  model=%s  主=%s",
                self.cfg.host, self.cfg.port, self.cfg.model, self.priority.primary,
            )
            await asyncio.Future()

    async def aclose(self) -> None:
        """释放 HTTP 连接。"""
        await self.client.aclose()


async def selftest(cfg: Config) -> int:
    """只验证 Ollama 那一半：一次真实请求 + 计时。"""
    bot = Bot(cfg)
    try:
        messages = [
            {"role": "system", "content": str(cfg.system_prompt)},
            {"role": "user", "content": "用一句话说说你在群里能帮上什么忙。"},
        ]
        started = time.monotonic()
        reply = await bot.ollama_chat(messages)
        elapsed = time.monotonic() - started
        print(f"[selftest] {elapsed:.1f}s  {reply}")
        return 0
    except Exception as error:  # 自检失败要打印原因而不是堆栈
        print(f"[selftest] FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        await bot.aclose()


def main() -> int:
    """解析参数并运行。"""
    parser = argparse.ArgumentParser(description="OneBot v11 → Ollama QQ 机器人适配器")
    parser.add_argument("--config", default=str(HERE / "config.json"), help="配置文件路径")
    parser.add_argument("--selftest", action="store_true", help="只测 Ollama 端点，不启动 WS 服务")
    args = parser.parse_args()

    log_setup()
    cfg = Config.load(Path(args.config))

    if args.selftest:
        return asyncio.run(selftest(cfg))

    bot = Bot(cfg)
    if bot.memory is not None:
        LOG.info("用户记忆已启用：已记住 %d 人（%s）", len(bot.memory.users), bot.memory.path)
    else:
        LOG.info("用户记忆未启用（user_memory_enabled=false）")
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print()
        LOG.info("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
