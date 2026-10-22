#!/usr/bin/env python3
"""QQ 开放平台官方机器人 → 本地 Ollama 的桥接（WebSocket 模式）。

平台**不会也无法**直接访问你机器上的模型。事件流向是：

    腾讯平台 --(本进程主动连出的 WSS，无需公网 IP)--> 本进程 --(127.0.0.1:11434)--> Ollama

所以"调取本地模型"这件事发生在**本进程内部**：收到群 @ 事件后，用事件里的
``msg_id`` 在 5 分钟内做被动回复。Ollama 始终只监听回环，不需要任何端口映射。

用法::

    export QQ_APPID=你的AppID QQ_SECRET=你的AppSecret
    python official_bot.py                # 常驻
    python official_bot.py --selftest     # 只测本地模型那一半

配置与 NapCat 路线共用同目录的 ``config.json``（模型、system prompt、历史轮数等），
另可加 ``qq_appid`` / ``qq_secret`` 两个键，环境变量优先。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import random
import re
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bot import Config, PersonaStore, clean_reply  # noqa: E402  复用非官方路线的配置与清洗逻辑
from web_tools import chat_with_tools  # noqa: E402  联网工具（两条路线共用）
from image_gen import (  # noqa: E402  出图（两条路线共用）
    ImageAction,
    ImageCache,
    ImageGen,
    ImageGenError,
    ModelPriority,
    detect_image_action,
    encode_jpeg,
)
from sticker_lib import (  # noqa: E402  表情包库（收集 + 发送）
    MAX_STICKER_BYTES,
    StickerLibrary,
    build_sticker_hint,
    infer_tags_from_text,
    parse_sticker_marker,
)
from user_memory import (  # noqa: E402  按 openid 记住每个人（与协议无关，两条路线共用）
    Speaker,
    UserMemory,
    parse_memory_marker,
)
from msg_guard import MessageDedup, SendGuard  # noqa: E402  消息去重 + 发送熔断

import botpy  # noqa: E402
from botpy import logging as botpy_logging  # noqa: E402
from botpy.http import Route  # noqa: E402  富媒体上传要直接走底层路由
from botpy.message import C2CMessage, GroupMessage  # noqa: E402

LOG = botpy_logging.get_logger()

# 平台错误码 40054010：消息里不允许出现 URL；40054007：消息长度超限。
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# 兜底分片长度，避免撞上长度上限。
CHUNK_CHARS = 450

# 事件最外层 id 与 msg_id 的对应关系见官方文档"发送群聊消息"。
PASSIVE_WINDOW_SECONDS = 300.0
MAX_PASSIVE_REPLIES = 5

# 官方消息里的 QQ 内置表情是 ``<faceType=6,faceId=0>`` 这种标记。直接丢给模型，
# 它会当成一串乱码；日志里甚至出现过它反问「这表情什么意思」。这里把常用 faceId
# 映射成中文名，让模型至少知道对方发的是一个「微笑 / 流泪 / 得意」的表情。
QQ_FACE_NAMES: dict[int, str] = {
    0: "微笑", 1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪", 6: "害羞",
    7: "闭嘴", 8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒", 12: "调皮", 13: "呲牙",
    14: "惊讶", 15: "难过", 16: "酷", 17: "冷汗", 18: "抓狂", 19: "吐", 20: "偷笑",
    21: "可爱", 22: "白眼", 23: "傲慢", 24: "饥饿", 25: "困", 26: "惊恐", 27: "流汗",
    28: "憨笑", 29: "大兵", 30: "奋斗", 31: "咒骂", 32: "疑问", 33: "嘘", 34: "晕",
    35: "折磨", 36: "衰", 37: "骷髅", 38: "敲打", 39: "再见", 40: "擦汗", 41: "抠鼻",
    42: "鼓掌", 43: "糗大了", 44: "坏笑", 45: "左哼哼", 46: "右哼哼", 47: "哈欠",
    48: "鄙视", 49: "委屈", 50: "快哭了", 51: "阴险", 52: "亲亲", 53: "吓",
    54: "可怜", 55: "菜刀", 56: "西瓜", 57: "啤酒", 58: "篮球", 59: "乒乓",
    60: "咖啡", 61: "饭", 62: "猪头", 63: "玫瑰", 64: "凋谢", 65: "示爱",
    66: "爱心", 67: "心碎", 68: "蛋糕", 69: "闪电", 70: "炸弹", 71: "刀",
    72: "足球", 73: "瓢虫", 74: "便便", 75: "月亮", 76: "太阳", 77: "礼物",
    78: "拥抱", 79: "强", 80: "弱", 81: "握手", 82: "胜利", 83: "抱拳",
    84: "勾引", 85: "拳头", 86: "差劲", 87: "爱你", 88: "NO", 89: "OK",
    90: "爱情", 91: "飞吻", 92: "跳跳", 93: "发抖", 94: "怄火", 95: "转圈",
    96: "磕头", 97: "回头", 98: "跳绳", 99: "挥手", 100: "激动", 101: "街舞",
    102: "献吻", 103: "左太极", 104: "右太极",
}

# 兼容 ``<faceType=6,faceId=0>`` / 带空格 / 属性换序
# 实测格式：<faceType=6,faceId="0",ext="eyJ0ZXh0Ijoi...">（faceId 带引号、后面还有 ext）
FACE_TAG_RE = re.compile(
    r"<faceType\s*=\s*(\d+)\s*,\s*faceId\s*=\s*\"?(\d+)\"?[^>]*>",
    re.IGNORECASE,
)
# 富媒体附件在正文里的占位（官方事件里有时会带 ``<attachment ...>``）
ATTACHMENT_TAG_RE = re.compile(r"<attachment[^>]*>", re.IGNORECASE)


# 表情占位的统一前缀：pure_media 判断和 role 提示都引用它，避免到处写死字符串
FACE_PREFIX = "（对方发了个"
IMAGE_PLACEHOLDER = "[发了一张表情包/图片]"


def face_text(text: str) -> str:
    """把 QQ 表情标记换成自然语言，保留位置与语气。

    早期版本输出 ``[QQ表情:微笑]``，结果模型把它当成了系统标记或乱码，
    回出「这表情什么意思」。换成「（对方发了个「微笑」表情）」后，模型
    能直接把它当成一句人话读。
    """
    def repl(match: re.Match[str]) -> str:
        face_id = int(match.group(2))
        name = QQ_FACE_NAMES.get(face_id)
        return f"{FACE_PREFIX}「{name}」表情）" if name else f"{FACE_PREFIX}表情）"

    return FACE_TAG_RE.sub(repl, text or "")


def message_text(message: Any, tags: list[str] | None = None) -> tuple[str, bool]:
    """把一条官方消息整理成「模型看得懂的文本」，返回 ``(文本, 是否带图)``。

    三件事：
    1. ``<faceType=...>`` → ``[QQ表情:名字]``（表情不再是一串乱码）；
    2. ``<attachment ...>`` → 去掉（真正的附件信息在 ``message.attachments`` 里）；
    3. 只有图/只有表情、没有文字时，补一句占位，让模型知道对方发的是表情包，
       否则 ``min_chars`` 会直接把这句拒掉，表现就是「机器人对表情包完全没反应」。

    ``tags`` 是这张图在表情库里的已知标签（可选）。不能用 ``setattr(message, ...)``
    挂上去——botpy 的 ``GroupMessage`` 有 ``__slots__``，动态属性会直接抛异常。

    ``has_image`` 也覆盖「整条消息只有 QQ 表情」的情况：对纯表情消息按图片信号
    计分，机器人就会偶尔接一下，而不是永远沉默。
    """
    raw = getattr(message, "content", "") or ""
    has_image = False
    for att in getattr(message, "attachments", None) or []:
        content_type = (getattr(att, "content_type", "") or "").lower()
        if content_type.startswith("image/"):
            has_image = True
            break
    text = face_text(raw)
    text = ATTACHMENT_TAG_RE.sub("", text).strip()
    # 原文里去掉所有表情后什么都不剩 → 这是一条纯表情消息
    if not has_image and FACE_TAG_RE.search(raw) and not FACE_TAG_RE.sub("", raw).strip():
        has_image = True
    # 这张图如果已经在库里有标签，就告诉模型它大概是什么情绪。
    # 这是「同图第二次出现就能被认出来」的关键：本机没有视觉模型，
    # 但同一个表情包被反复转发时，第一次攒下的标签可以复用。
    known = list(tags or [])
    hint = f"（表情包含义：{'、'.join(known)}）" if known else ""
    if not text and has_image:
        return f"{IMAGE_PLACEHOLDER}{hint}", True
    return f"{text}{hint}", has_image


# 固定追加在 system 末尾，明确告诉模型「消息里的表情长什么样」。
# 这段必须稳定（不随消息变化），否则会打掉前缀缓存。
FACE_HINT = (
    "\n\n【消息里的表情】"
    "「（对方发了个「微笑」表情）」表示群友发的是 QQ 表情；"
    "「[发了一张表情包/图片]」表示群友发了一张图，后面可能带「表情包含义：笑」。"
    "这些都是群友真实发出来的内容，直接按那个情绪接话；"
    "不要说「看不懂」「这表情什么意思」，也别把标记念出来。"
    "想回一张图就写 [表情:标签]。"
)


# 只含这些占位符的回复等于「没说话」
_PLACEHOLDER_ONLY = {"[图片]", "[表情]", "[表情包]", "（图片）"}
# 去掉括号动作后，只剩这些标点也算空
_EMPTY_CHARS = " 　？?！!。，,、~～…（）()"


def message_has_content(text: str) -> bool:
    """正文有没有实际内容。

    只写「（甩尾）」「？」「[图片]」都不算——模型最近学会了用这些应付，
    群里看起来就是「机器人不会说话」。返回 False 时调用方会重试一次。
    """
    body = re.sub(r"[（(][^）)]*[）)]", "", text or "").strip()
    if not body or body in _PLACEHOLDER_ONLY:
        return False
    return bool(body.strip(_EMPTY_CHARS))


def sanitize(text: str, limit: int) -> str:
    """清洗模型输出：去推理块、去 URL、去 Markdown 噪声、截断。

    平台会因消息含 URL 直接拒发（40054010），所以这里必须处理。
    """
    out = clean_reply(text)
    out = URL_PATTERN.sub("(链接已省略)", out)
    out = out.replace("**", "").replace("##", "").replace("`", "")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out[:limit]


def split_message(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """按段落优先切成若干片；同一个 msg_id 的多次回复必须用不同的 msg_seq。"""
    if len(text) <= size:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n"):
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(block) > size:
            parts.append(block[:size])
            block = block[size:]
        current = block
    if current:
        parts.append(current)
    return parts[:MAX_PASSIVE_REPLIES]


class LocalModel:
    """本地模型客户端：一个 lock 串行化请求，队列过长直接拒绝。"""

    def __init__(self, cfg: Config, personas: PersonaStore | None = None) -> None:
        self.cfg = cfg
        self.personas = personas
        self.history: dict[str, deque[dict[str, str]]] = {}
        self._lock = asyncio.Lock()
        self._waiting = 0
        self._client: httpx.AsyncClient | None = None
        # 由 Responder 注入：search_sticker 工具要用它把网上的图存下来
        self.sticker_lib: Any = None

    @property
    def client(self) -> httpx.AsyncClient:
        """懒创建：连接池必须绑定在真正跑它的那个事件循环上。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(float(self.cfg.request_timeout)))
        return self._client

    @property
    def overloaded(self) -> bool:
        """等待数达到上限时，让调用方回一句"忙"而不是排队到超时。"""
        return self._waiting >= int(self.cfg.queue_limit)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """打一次本地模型的 OpenAI 兼容端点，需要时会走联网工具。"""
        content = await chat_with_tools(
            self.client, self.cfg, messages,
            max_rounds=3 if self.cfg.tools_enabled else 0,
            sticker=self.sticker_lib,
        )
        content = clean_reply(content)
        if not content:
            raise RuntimeError("模型返回空正文（思考没关掉？检查 reasoning_effort）")
        return content

    async def ask(self, session: str, prompt: str, system_suffix: str = "") -> str:
        """带历史的串行推理。

        ``system_suffix`` 用于临时追加一段系统提示（例如当前可用的表情包标签）。
        它**不进聊天历史**，只影响这一次请求，避免历史被反复污染。
        """
        self._waiting += 1
        try:
            async with self._lock:
                buf = self.history.setdefault(
                    session, deque(maxlen=max(1, int(self.cfg.history_turns)) * 2)
                )
                system = self.personas.prompt() if self.personas else str(self.cfg.system_prompt)
                if system_suffix:
                    system = f"{system}{system_suffix}"
                messages = [{"role": "system", "content": system}]
                messages.extend(dict(item) for item in buf)
                messages.append({"role": "user", "content": prompt})
                reply = await self.chat(messages)
                buf.append({"role": "user", "content": prompt})
                buf.append({"role": "assistant", "content": reply})
                return reply
        finally:
            self._waiting -= 1

    async def aclose(self) -> None:
        """释放连接（未使用过则无事可做）。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


Sender = Callable[[str, int], Awaitable[Any]]
ImageSender = Callable[[bytes, int], Awaitable[Any]]


def install_group_message_create_support() -> bool:
    """让 botpy 支持「群消息（全量模式）」。

    botpy 1.2.1 只实现了 ``GROUP_AT_MESSAGE_CREATE``（仅 @机器人时推送），
    而全量模式的事件名是 ``GROUP_MESSAGE_CREATE``。两者 intent 相同
    （都是 1<<25），事件体字段也完全一致，区别只是前者只在被@时推送。

    ``ConnectionState.__init__`` 用 ``inspect.getmembers`` 扫描自身所有
    ``parse_*`` 方法建分发表，查不到就丢事件并打 ``_parser unknown event``。
    所以这里给它补一个同名方法即可，不用改 SDK 源码。

    返回是否真的打了补丁（已存在则返回 False）。
    """
    from botpy.connection import ConnectionState

    if hasattr(ConnectionState, "parse_group_message_create"):
        return False

    def parse_group_message_create(self: Any, payload: dict[str, Any]) -> None:
        message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
        self._dispatch("group_message_create", message)

    ConnectionState.parse_group_message_create = parse_group_message_create  # type: ignore[attr-defined]
    return True


def install_sender_username_support() -> list[str]:
    """让 botpy 把发送者昵称/角色一并映射出来。

    ``GroupMessage._User`` 只取了 ``member_openid``，``C2CMessage._User`` 只取了
    ``user_openid``——而官方事件体里其实是带着 ``author.username`` /
    ``member_role`` / ``bot`` 的（见「群消息（全量模式）」的事件示例）。
    SDK 把它们丢掉了，机器人眼里所有群友都只是一串 openid，
    「记住每个人的性格和脾气」就无从谈起。

    不能改 SDK 源码（升级即丢），所以在消息类外面包一层 ``__init__``：
    先跑原逻辑，再把原始 payload 里的字段补到 ``author`` 上。
    返回实际被补过字段的类名。
    """
    from botpy.message import C2CMessage, GroupMessage

    patched: list[str] = []
    for cls in (GroupMessage, C2CMessage):
        if getattr(cls, "_maid_author_fields_patched", False):
            continue
        original = cls.__init__

        def wrapper(self: Any, api: Any, event_id: Any, data: Any, _original: Any = original) -> None:
            _original(self, api, event_id, data)
            author = (data or {}).get("author") or {}
            target = getattr(self, "author", None)
            if target is None:
                return
            for key in ("username", "member_role", "bot", "union_openid"):
                if key in author and key not in target.__dict__:
                    setattr(target, key, author[key])

        cls.__init__ = wrapper  # type: ignore[method-assign]
        cls._maid_author_fields_patched = True  # type: ignore[attr-defined]
        patched.append(cls.__name__)
    return patched


def speaker_of(message: Any, scene: str) -> Speaker | None:
    """从 botpy 消息里抽出发言人；拿不到 openid 就返回 None（不记不注入）。"""
    author = getattr(message, "author", None)
    if author is None:
        return None
    if scene == "c2c":
        uid = str(getattr(author, "user_openid", "") or "")
    else:
        uid = str(getattr(author, "member_openid", "") or "")
    if not uid:
        return None
    return Speaker(uid=uid, name=str(getattr(author, "username", "") or ""), scene=scene)


# ---------------------------------------------------------------------------
# 自动聊天（形态① 群内接话 / 形态② 定时主动发言），方案见 docs/AUTOCHAT.md
#
# 刻意拆成三块，边界清楚：
#   AutoChatPolicy     纯决策，只依赖注入的 rng/clock → 可以离线单测，不连腾讯网关
#   AutoChatState      冷却 / 当日额度 / 主动消息白名单的账本（内存态，重启归零）
#   AutoChatScheduler  只负责"到点发一条主动消息"，不参与接话判定
#
# 默认全部关闭：``auto_chat.enabled=false`` 时 on_group_message_create 走旧的
# ``group_reply_all`` + ``group_cooldown_seconds`` 逻辑，行为与本文件改动前完全一致。
# ---------------------------------------------------------------------------

AUTO_CHAT_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "dry_run": True,
    "reply_mode": "passive",
    "allowed_groups": [],
    "keywords": ["机器人", "小助手"],
    "reply_when_mentioned": True,
    "reply_when_question": False,
    "probability": 0.1,
    "min_chars": 2,
    "max_chars": 200,
    "cooldown_seconds": 90.0,
    # 表情包/纯表情消息的独立冷却（秒）：不跟文字消息共用 cooldown，否则斗图时
    # 刚回完一句就要再等 30 秒，看起来就是「机器人不认识表情」。
    "image_reply_cooldown": 20.0,
    "daily_limit_per_group": 30,
    "quiet_hours": ["23:00", "08:00"],
    "schedule": [],
    "proactive_prompt": (
        "现在群里很安静。你想主动说一句轻松的话。只回这一句，"
        "不要重复群里的内容，不要提太多问题。"
    ),
    "proactive_max_chars": 80,
    "tick_seconds": 30.0,
    # 同一个人连发几条短消息时，先等一小会儿合并成一句再判定。
    # 0 = 关闭（旧行为：来一条判一条，容易对着半句话作答）。
    "debounce_seconds": 0.0,
    # 发言时机闸门：在「必答层」之后、「概率层」之前插一层价值评分。
    # 参考 AngelHeart 的离场/在场与 dsh-qqbot 的回复闸门，但**不额外调模型**。
    "gate": {
        "enabled": False,
        "min_score": 0.55,
        "hard_cooldown_seconds": 8.0,
        "max_per_hour": 12,
        "question_bonus": 0.45,
        "image_bonus": 0.50,
        "length_bonus": 0.10,
        "crowd_penalty": 0.35,
        "repeat_penalty": 0.25,
        "ignored_penalty": 0.30,
        "recent_window": 20.0,
        "repeat_window": 60.0,
        "crowd_min_speakers": 2,
    },
}

PASSIVE_REPLY_MODES = ("passive", "both")
PROACTIVE_REPLY_MODES = ("proactive", "both")


def minutes_of_day(raw: Any) -> int | None:
    """把 ``"23:00"`` 解析成 1380；解析不了返回 None。"""
    if not isinstance(raw, str) or ":" not in raw:
        return None
    hour_text, _, minute_text = raw.partition(":")
    try:
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


@dataclass(frozen=True)
class GateResult:
    """发言时机闸门的判定结果。``score`` 只用于调参与日志，不对外。"""

    allow: bool
    score: float
    reason: str
    band: str  # must / likely / chance / silent


class SpeechGate:
    """轻量「该不该开口」评分器。

    为什么要有它：原来的概率层是**纯掷骰子**，群里每条消息都有 80% 机会被接，
    结果就是日志里那种「两个人各自吐槽一句，机器人硬插中间自说自话」。
    这里把「像不像在跟机器人说话」「群里是不是正在多人互聊」「刚说过话没有」
    变成可解释的分数，**不增加一次模型调用**（本地 7 tok/s，多调一次就是几秒）。

    信号与权重全部可配；``enabled=false`` 时完全不参与判定，旧行为逐字不变。
    """

    DEFAULTS: dict[str, Any] = {
        "enabled": False,
        "min_score": 0.55,
        "hard_cooldown_seconds": 8.0,
        "max_per_hour": 12,
        "question_bonus": 0.45,
        "image_bonus": 0.50,
        "length_bonus": 0.10,
        "crowd_penalty": 0.35,
        "repeat_penalty": 0.25,
        "ignored_penalty": 0.30,
        "recent_window": 20.0,
        "repeat_window": 60.0,
        "crowd_min_speakers": 2,
    }

    def __init__(self, raw: Any = None) -> None:
        self.raw: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}

    def _float(self, key: str) -> float:
        raw = self.raw.get(key, self.DEFAULTS[key])
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(self.DEFAULTS[key])

    def _int(self, key: str) -> int:
        raw = self.raw.get(key, self.DEFAULTS[key])
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(self.DEFAULTS[key])

    def float_setting(self, key: str) -> float:
        return self._float(key)

    def int_setting(self, key: str) -> int:
        return self._int(key)

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", self.DEFAULTS["enabled"]))

    def evaluate(
        self,
        *,
        text: str,
        mentioned: bool = False,
        keyword_hit: bool = False,
        question: bool = False,
        has_image: bool = False,
        seconds_since_last: float = float("inf"),
        ignored_last: bool = False,
        recent: list[tuple[float, str]] | None = None,
        hourly_count: int = 0,
    ) -> GateResult:
        """返回 allow/score/reason。

        ``recent`` 是「最近若干条群消息」的 ``(几秒前, 发送者 openid)``，
        由调用方在内存里维护；这里不碰消息对象，纯函数好测。
        """
        cooldown = self._float("hard_cooldown_seconds")
        if cooldown > 0 and seconds_since_last < cooldown:
            return GateResult(False, 0.0, "gate:hard_cooldown", "silent")

        max_per_hour = self._int("max_per_hour")
        if max_per_hour > 0 and hourly_count >= max_per_hour:
            return GateResult(False, 0.0, "gate:hourly_limit", "silent")

        score = 0.0
        if mentioned:
            score += 1.0
        elif keyword_hit:
            score += 0.8
        if question:
            score += self._float("question_bonus")
        if has_image:
            score += self._float("image_bonus")
        if len((text or "").strip()) >= 6:
            score += self._float("length_bonus")

        window = self._float("recent_window")
        repeat_window = self._float("repeat_window")
        recent = recent or []
        in_window = [(age, uid) for age, uid in recent if age <= window]
        speakers = {uid for _, uid in in_window if uid}
        if len(speakers) >= self._int("crowd_min_speakers") and len(in_window) >= 3:
            score -= self._float("crowd_penalty")  # 多人在聊，别硬插
        repeat_speakers: dict[str, int] = {}
        for age, uid in recent:
            if age <= repeat_window and uid:
                repeat_speakers[uid] = repeat_speakers.get(uid, 0) + 1
        if any(n >= 2 for n in repeat_speakers.values()):
            score -= self._float("repeat_penalty")  # 同一个人还在连说
        if ignored_last:
            score -= self._float("ignored_penalty")  # 上次开口没人接

        score = max(0.0, min(1.0, round(score, 3)))
        if score >= self._float("min_score"):
            return GateResult(True, score, f"gate:score={score:.2f}", "likely")
        return GateResult(False, score, f"gate:low_score={score:.2f}", "silent")


@dataclass(frozen=True)
class AutoChatDecision:
    """一次判定结果。``layer`` 指出命中的是哪一层，看日志调参就靠它。"""

    allow: bool
    reason: str
    layer: str  # gate=闸门 / must=必答 / chance=概率 / none=无触发


class AutoChatPolicy:
    """决定「群里的这条消息要不要接」。

    设计成纯函数：除了构造时注入的 ``rng`` / ``clock``，不读全局状态、不碰网络。
    所以 ``test_autochat_policy.py`` 能在不连腾讯网关、不启动模型的情况下把它测穿。
    """

    def __init__(
        self,
        block: Any = None,
        *,
        rng: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.time,
    ) -> None:
        merged = dict(AUTO_CHAT_DEFAULTS)
        if isinstance(block, dict):
            # 下划线开头的是给人看的注释键，别混进配置
            merged.update(
                {k: v for k, v in block.items() if not str(k).startswith("_")}
            )
        self.raw = merged
        self._rng = rng
        self._clock = clock
        self.gate = SpeechGate(merged.get("gate"))

    @property
    def gate_enabled(self) -> bool:
        return self.gate.enabled

    # --- 配置读取：全部带类型兜底，手写 JSON 很容易写成字符串或漏键 ---
    def int_setting(self, key: str, default: int) -> int:
        try:
            return int(self.raw.get(key, default))
        except (TypeError, ValueError):
            return default

    def float_setting(self, key: str, default: float) -> float:
        try:
            return float(self.raw.get(key, default))
        except (TypeError, ValueError):
            return default

    def _string_list(self, key: str) -> list[str]:
        """读字符串列表。顺手挡掉 ``null`` / 空串 / 嵌套结构——
        否则 ``str(None)`` 会变成字符串 ``"None"`` 混进关键词表，永远不会命中。"""
        value = self.raw.get(key) or []
        if not isinstance(value, (list, tuple)):
            return []
        out: list[str] = []
        for item in value:
            if item is None or isinstance(item, (dict, list, tuple)):
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled"))

    @property
    def dry_run(self) -> bool:
        return bool(self.raw.get("dry_run"))

    @property
    def reply_mode(self) -> str:
        return str(self.raw.get("reply_mode") or "passive").strip().lower()

    @property
    def passive_enabled(self) -> bool:
        return self.reply_mode in PASSIVE_REPLY_MODES

    @property
    def proactive_enabled(self) -> bool:
        return self.reply_mode in PROACTIVE_REPLY_MODES

    @property
    def allowed_groups(self) -> list[str]:
        return self._string_list("allowed_groups")

    @property
    def keywords(self) -> list[str]:
        return self._string_list("keywords")

    @property
    def schedule(self) -> list[str]:
        """只保留能解析成 HH:MM 的项，写错了不会让定时任务整个跑不起来。"""
        return [item for item in self._string_list("schedule") if minutes_of_day(item) is not None]

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        """静默时段判定，支持跨零点（``["23:00","08:00"]``）。"""
        window = self.raw.get("quiet_hours") or []
        if not isinstance(window, (list, tuple)) or len(window) < 2:
            return False
        start, end = minutes_of_day(window[0]), minutes_of_day(window[1])
        if start is None or end is None or start == end:
            return False
        moment = now or datetime.fromtimestamp(self._clock())
        current = moment.hour * 60 + moment.minute
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def decide(
        self,
        *,
        group: str,
        text: str,
        mentioned: bool = False,
        seconds_since_last: float = float("inf"),
        today_count: int = 0,
        now: datetime | None = None,
        has_image: bool = False,
        gate_ctx: dict[str, Any] | None = None,
    ) -> AutoChatDecision:
        """多层判定：闸门 → 必答 → 发言时机 → 概率。

        先闸门是为了不浪费随机数、也先挡掉刷屏。
        ``gate_ctx`` 只在 ``auto_chat.gate.enabled=true`` 时读取，包含
        ``recent``（最近消息 ``(秒, uid)``）、``hourly_count``、``ignored_last``；
        不传时按「没有近期记录」处理，旧调用与旧单测的行为逐字不变。
        """

        def deny(reason: str, layer: str = "gate") -> AutoChatDecision:
            return AutoChatDecision(False, reason, layer)

        if not self.enabled:
            return deny("disabled")
        if not self.passive_enabled:
            return deny("reply_mode_proactive_only")

        allowed = self.allowed_groups
        if not allowed:
            # 空白名单 = 一个群都不接。这是故意的安全默认：被拉进新群不会立刻开麦。
            return deny("no_group_whitelisted")
        # "*" = 不限制，任何群都接（2026-10-17 主人指定；注意被人拉进陌生群也会开口）
        if "*" not in allowed and group not in allowed:
            return deny("group_not_allowed")
        if self.in_quiet_hours(now):
            return deny("quiet_hours")

        daily_limit = self.int_setting("daily_limit_per_group", 30)
        if daily_limit > 0 and today_count >= daily_limit:
            return deny("daily_limit")

        body = (text or "").strip()
        # 「纯表情/纯图片」消息：没有正文，只有一个占位或 [QQ表情:名字]。
        # 这类消息的时间敏感性很高——别人斗图时你等 30 秒再回，梗都凉了。
        pure_media = has_image and (
            body.startswith(IMAGE_PLACEHOLDER) or body.startswith(FACE_PREFIX)
        )

        cooldown = self.float_setting("cooldown_seconds", 90.0)
        if cooldown > 0 and seconds_since_last < cooldown:
            # 纯表情/纯图片不跟文字消息抢同一个冷却；它有自己的限频（见下面 image_reply_cooldown）
            if not (self.gate_enabled and pure_media):
                return deny("cooldown")

        if len(body) < self.int_setting("min_chars", 2):
            return deny("too_short")
        max_chars = self.int_setting("max_chars", 200)
        if max_chars > 0 and len(body) > max_chars:
            return deny("too_long")

        if mentioned and bool(self.raw.get("reply_when_mentioned", True)):
            return AutoChatDecision(True, "mentioned", "must")

        keyword_hit = next((k for k in self.keywords if k in body), "")
        if keyword_hit:
            return AutoChatDecision(True, f"keyword:{keyword_hit}", "must")

        if self.gate_enabled:
            # 表情包/纯表情：给一次「必答」机会，但按 image_reply_cooldown 限频。
            # 不加这段的话，0.35 的概率门会把大部分表情包静默掉——主人测出来的
            # 「无法识别表情」就是这一层造成的（日志里是 gate_probability）。
            if pure_media:
                image_cooldown = self.float_setting("image_reply_cooldown", 20.0)
                if image_cooldown <= 0 or seconds_since_last >= image_cooldown:
                    return AutoChatDecision(True, "image", "must")

            ctx = gate_ctx or {}
            gate = self.gate.evaluate(
                text=body,
                mentioned=mentioned,
                keyword_hit=bool(keyword_hit),
                question=body.endswith(("?", "？")),
                has_image=has_image,
                seconds_since_last=seconds_since_last,
                ignored_last=bool(ctx.get("ignored_last")),
                recent=ctx.get("recent") or [],
                hourly_count=int(ctx.get("hourly_count") or 0),
            )
            if not gate.allow:
                return deny(gate.reason, "gate")
            # 闸门放行只是「可以说话」，还要过概率门——主人可以把 probability 调成 1
            # 变成完全由评分决定，也可以留 0.3 让它在放行里再挑一部分。
            probability = self.float_setting("probability", 0.1)
            if probability >= 1.0 or (probability > 0 and self._rng() < probability):
                return AutoChatDecision(True, gate.reason, "gate")
            return deny("gate_probability", "gate")

        if bool(self.raw.get("reply_when_question")) and body.endswith(("?", "？")):
            return AutoChatDecision(True, "question", "must")

        probability = self.float_setting("probability", 0.1)
        if probability > 0 and self._rng() < probability:
            return AutoChatDecision(True, "probability", "chance")

        return deny("no_trigger", "none")


class AutoChatState:
    """自动接话的账本：每群上次回复时刻、当日已发条数、主动消息白名单。

    只放在内存里，重启归零——额度是用来「防止打扰」的，不是审计数据，不值得落盘。
    """

    def __init__(self) -> None:
        self._last_reply: dict[str, float] = {}
        self._daily: dict[tuple[str, str], int] = {}
        self._hourly: dict[tuple[str, str], int] = {}
        self._recent: dict[str, deque[tuple[float, str]]] = {}
        self._engaged: dict[str, bool] = {}
        self.proactive_allowed: dict[str, bool] = {}

    @staticmethod
    def _today() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _this_hour() -> str:
        return datetime.now().strftime("%Y-%m-%dT%H")

    def seconds_since_last(self, group: str) -> float:
        last = self._last_reply.get(group)
        if last is None:
            return float("inf")
        return max(0.0, time.monotonic() - last)

    def today_count(self, group: str) -> int:
        return self._daily.get((self._today(), group), 0)

    def hourly_count(self, group: str) -> int:
        return self._hourly.get((self._this_hour(), group), 0)

    def note_message(self, group: str, uid: str) -> None:
        """记一条群消息，供发言时机闸门判断「是不是在多人互聊 / 同一人连发」。

        只留最近 120 秒、最多 40 条；这是纯内存的滑动窗口，重启归零无所谓。
        """
        now = time.monotonic()
        window = self._recent.setdefault(group, deque(maxlen=40))
        window.append((now, uid))
        while window and now - window[0][0] > 120.0:
            window.popleft()
        last = self._last_reply.get(group)
        if last is not None and now - last <= 120.0:
            # 机器人开口后 2 分钟内有人继续说话，视为「接上了」
            self._engaged[group] = True

    def recent_messages(self, group: str) -> list[tuple[float, str]]:
        """返回 ``[(几秒前, 发送者), ...]``，越新越靠后。"""
        now = time.monotonic()
        return [(now - ts, uid) for ts, uid in self._recent.get(group, ())]

    def ignored_last(self, group: str, within: float = 45.0) -> bool:
        """上次发言之后一直没人接话（且已经过了 ``within`` 秒）。"""
        last = self._last_reply.get(group)
        if last is None or self._engaged.get(group, True):
            return False
        return (time.monotonic() - last) >= within

    def note_reply(self, group: str) -> None:
        """真的发出了一条（@回复 / 接话 / 主动发言）才记账。"""
        self._last_reply[group] = time.monotonic()
        self._engaged[group] = False
        today = self._today()
        key = (today, group)
        self._daily[key] = self._daily.get(key, 0) + 1
        hour_key = (self._this_hour(), group)
        self._hourly[hour_key] = self._hourly.get(hour_key, 0) + 1
        # 清掉历史的日期/小时，长期运行不会把内存堆满
        for stale in [k for k in self._daily if k[0] != today]:
            self._daily.pop(stale, None)
        this_hour = self._this_hour()
        for stale in [k for k in self._hourly if k[0] != this_hour]:
            self._hourly.pop(stale, None)

    def note_proactive(self, group: str, allowed: bool) -> None:
        self.proactive_allowed[group] = allowed

    def proactive_ok(self, group: str) -> bool:
        """没收到过 receive/reject 事件时按「允许」算，真发失败由平台兜底。"""
        return self.proactive_allowed.get(group, True)


class AutoChatScheduler:
    """形态②：到点往白名单群发一条主动消息。

    主动消息**不占被动回复窗口**，但吃平台配额（20 条/分·群、1000 条/天·群），
    而且群主随时能在客户端关掉——所以这里失败一律静默，绝不把错误刷进群。
    """

    def __init__(
        self,
        client: Any,
        policy: AutoChatPolicy,
        state: AutoChatState,
        responder: Any,
    ) -> None:
        self.client = client
        self.policy = policy
        self.state = state
        self.responder = responder
        self._task: asyncio.Task[None] | None = None
        self._fired: set[tuple[str, str]] = set()

    def start(self) -> None:
        """由 on_ready 调用：连上网关之后再起定时任务。"""
        if self._task is not None:
            return
        if not (self.policy.enabled and self.policy.proactive_enabled):
            return
        if not self.policy.schedule:
            LOG.info("[qq][auto] 未配置 schedule，定时主动发言不启动")
            return
        if not self.policy.allowed_groups:
            LOG.warning("[qq][auto] 主动发言没有白名单群，不启动（allowed_groups 为空）")
            return
        self._task = asyncio.create_task(self._run())
        LOG.info(
            "[qq][auto] 定时主动发言已启动 %s（%d 个群，dry_run=%s）",
            "、".join(self.policy.schedule),
            len(self.policy.allowed_groups),
            self.policy.dry_run,
        )

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        tick = max(5.0, self.policy.float_setting("tick_seconds", 30.0))
        while True:
            await asyncio.sleep(tick)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # 定时任务绝不能因为一次异常就整体死掉
                LOG.exception("[qq][auto] 定时主动发言出错")

    async def _tick(self) -> None:
        now = datetime.now()
        stamp = now.strftime("%H:%M")
        if stamp not in self.policy.schedule:
            return
        key = (now.strftime("%Y-%m-%d"), stamp)
        if key in self._fired:
            return  # 同一分钟内被 tick 多次也只发一轮
        self._fired = {item for item in self._fired if item[0] == key[0]}
        self._fired.add(key)

        for group in self.policy.allowed_groups:
            try:
                await self._speak(group)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("[qq][auto] 主动发言失败 group=%s", group)

    async def _speak(self, group: str) -> None:
        policy = self.policy
        if policy.in_quiet_hours():
            LOG.info("[qq][auto] 静默时段，跳过主动发言 group=%s", group)
            return
        if not self.state.proactive_ok(group):
            LOG.info("[qq][auto] 该群已关闭主动发言（on_group_msg_reject），跳过 group=%s", group)
            return
        daily_limit = policy.int_setting("daily_limit_per_group", 30)
        if daily_limit > 0 and self.state.today_count(group) >= daily_limit:
            LOG.info("[qq][auto] 已达当日额度 %d，跳过 group=%s", daily_limit, group)
            return

        prompt = str(policy.raw.get("proactive_prompt") or "").strip()
        if not prompt:
            return
        if policy.dry_run:
            LOG.info("[qq][auto][dry_run] 本应主动发言 group=%s prompt=%s", group, prompt[:40])
            return
        if self.responder.model.overloaded:
            LOG.info("[qq][auto] 模型忙，放弃本次主动发言 group=%s", group)
            return

        # 独立会话键：主动发言绝不能拿历史对话往下接，
        # 否则历史里那堆「用户: xxx」会让模型以为有人在跟它说话。
        try:
            reply = await self.responder.model.ask(f"group:{group}#proactive", prompt)
        except Exception:
            LOG.exception("[qq][auto] 主动发言推理失败 group=%s", group)
            return

        text = sanitize(reply, policy.int_setting("proactive_max_chars", 80))
        if not text:
            return
        try:
            # 不带 msg_id / event_id 就是主动消息
            await self.client.api.post_group_message(
                group_openid=group, msg_type=0, content=text
            )
        except Exception:
            LOG.warning(
                "[qq][auto] 主动消息发送失败（群内可能没开「机器人主动在群聊内发言」）group=%s",
                group,
                exc_info=True,
            )
            return
        self.state.note_reply(group)
        LOG.info("[qq][auto] 已主动发言 group=%s：%s", group, text[:60])


class Responder:
    """取回复 → 清洗 → 分片 → 回帖。与 botpy 解耦，便于离线测试。"""

    def __init__(self, model: LocalModel, cfg: Config, personas: PersonaStore) -> None:
        self.model = model
        self.cfg = cfg
        self.personas = personas
        self.image = ImageGen(cfg)
        # 每个会话最近若干张生成图，供"按上一张改"使用（只在内存里）
        self.cache = ImageCache(int(cfg.image_cache_size))
        # 主次切换：谁占着 GPU（chat=聊天为主 / image=生图为主，闲置后自动回落）
        self.priority = ModelPriority(cfg, self.image)
        # 出过图后 ComfyUI 会占着约 14 GB 不放，下次聊天前要先释放
        self.comfy_dirty = False
        # 表情包库：从群聊自动收集，回复时按标签挑一张发出去
        root = getattr(cfg, "sticker_dir", None) or (HERE / "stickers")
        self.stickers = StickerLibrary(Path(root))
        self.sticker_enabled = bool(getattr(cfg, "sticker_enabled", True))
        # 同一个库实例交给模型，search_sticker 工具才存得进来
        self.model.sticker_lib = self.stickers
        # 最近发过的几张（sha1），用于「同一张不连着发」；只留 8 张，重启无所谓
        self._recent_stickers: deque[str] = deque(maxlen=8)
        # 按 openid 记住每个人（关掉时全部退化成无记忆的旧行为）
        self.memory: UserMemory | None = None
        if bool(cfg.values.get("user_memory_enabled", True)):
            mem_path = cfg.values.get("user_memory_path") or (HERE / "memory" / "users.json")
            self.memory = UserMemory(Path(mem_path))

    def sticker_hint(self) -> str:
        """本次请求要追加的表情包说明（含当前可用标签）。

        先 reload：磁盘上的索引可能刚被联网搜图或 stickers.py 改过，
        不重读的话新标签要等重启才生效。
        """
        if not self.sticker_enabled:
            return ""
        self.stickers.reload()
        return build_sticker_hint(
            self.stickers.tag_summary(),
            enabled=True,
            count=len(self.stickers),
            # 联网搜图依赖工具调用，工具关了就别让它以为能搜
            web_enabled=bool(self.cfg.tools_enabled),
            tags=self.stickers.all_tags(),
        )

    def system_context(self) -> str:
        """本次请求要追加的全部 system 后缀：表情包说明 + 你记得的群友。

        记忆摘要**刻意保持稳定**（按 uid 排序、不含随消息变化的计数），
        否则每条消息都会改写 system 前缀、把 llama.cpp 的前缀缓存打掉。
        """
        suffix = FACE_HINT + self.sticker_hint()
        if self.memory is not None:
            suffix += self.memory.digest()
        return suffix

    def memory_command(self, text: str) -> tuple[bool, str]:
        """处理「记忆」/「忘掉 xxx」指令，返回 (是否已处理, 回复)。

        和人格指令一样只认严格前缀：光说「记忆」当查询，带参数才算删除，
        避免把「我记得你昨天说过…」这种日常话误判成指令。
        """
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
                removed = self.memory.clear()
                self.memory.save()
                return True, f"好，{removed} 个人的记忆都删了。"
            removed = self.memory.forget(target)
            self.memory.save()
            if removed:
                return True, f"已经把「{target}」从记忆里删掉了。"
            return True, f"我没记住「{target}」，不用删。"
        return False, ""

    async def answer(
        self,
        session: str,
        prompt: str,
        sender: Sender,
        image_sender: ImageSender | None = None,
        *,
        auto: bool = False,
        speaker: Speaker | None = None,
    ) -> None:
        """处理一条用户消息。

        ``sender(文本, msg_seq)`` 与 ``image_sender(jpeg_bytes, msg_seq)`` 由调用方提供，
        这样本类不依赖 botpy，可以离线测试。

        ``speaker`` 是发言人。给了它就会：记账（次数/昵称）、把 ``[群友 昵称]``
        缀在提问前面让模型认得出是谁、并从回复里摘 ``[记住:xxx]`` 存成印象。

        ``auto=True`` 表示这条来自「自动接话」而不是群友 @ / 私聊，此时有三条铁律：

        1. **不认人格指令、记忆指令、不触发出图** —— 全量模式下群里任何人打一句
           「人格 xxx」就能改掉机器人人设，或者一句「画一只猫」就把显卡抱走几十秒；
        2. **模型忙时完全静默** —— 自动任务优先级必须低于真人，不能回 busy_text 刷屏；
        3. **出错也静默** —— error_text 是给真人看的，不是给群友看的。
        """
        profile: Any = None
        if not auto:
            # 人格指令优先级最高：模型正忙时也应该能切
            handled, command_reply = self.personas.handle_command(prompt)
            if handled:
                LOG.info("[qq] 人格指令 %s -> %s", session, command_reply)
                await sender(command_reply, 1)
                return

            # 记忆指令：查询 / 删除，同样不经模型
            handled, command_reply = self.memory_command(prompt)
            if handled:
                LOG.info("[qq] 记忆指令 %s -> %s", session, command_reply[:40])
                await sender(command_reply, 1)
                return

            # 出图/改图：不经聊天模型、不写聊天历史
            action = detect_image_action(self.cfg, prompt, self.cache.count(session))
            if action is not None:
                if not action.prompt:
                    await sender(str(self.cfg.image_usage_text), 1)
                elif image_sender is None:
                    await sender(str(self.cfg.image_error_text), 1)
                elif action.kind == "edit" and self.cache.get(session, action.ref_index or 1) is None:
                    await sender(str(self.cfg.image_edit_no_ref_text), 1)
                else:
                    await self._answer_image(session, action, sender, image_sender)
                return

        # 记账 + 给提问打上发言人标签。
        # 标签放在 **user 消息**里而不是 system 里：system 要逐字稳定，
        # 前缀缓存才命中；user 消息本来每条都是新的，加多少都不会亏。
        if self.memory is not None and speaker is not None:
            profile = self.memory.touch(speaker.uid, speaker.name)
            self.memory.save()
            # 不分主客：群里一律【群友】，单聊一律【私聊】
            scene = "群友" if speaker.scene == "group" else "私聊"
            prompt = f"[{scene} {profile.display}] {prompt}"

        if self.model.overloaded:
            if auto:
                LOG.info("[qq] 模型忙，放弃自动接话 session=%s", session)
                return
            await sender(str(self.cfg.busy_text), 1)
            return

        # 双向显存互斥的另一半：出过图就先让 ComfyUI 放开显存，
        # 否则 Ollama 加载时只能部分上卡（实测 62% GPU / 38% CPU）
        if self.comfy_dirty:
            await self.image.release_image_model(self.model.client)
            self.comfy_dirty = False

        started = time.monotonic()
        try:
            reply = await self.model.ask(
                session,
                prompt[: int(self.cfg.max_prompt_chars)],
                system_suffix=self.system_context(),
            )
        except Exception:
            LOG.exception("[qq] 本地模型调用失败 session=%s", session)
            if auto:
                return
            await sender(str(self.cfg.error_text), 1)
            return
        # 记忆标记必须在 sanitize/截断 **之前** 摘掉：标记一般写在末尾，
        # 一旦被 max_reply_chars 截掉半截，就会以 "[记住:xxx" 的字面量发进群里。
        reply, notes = parse_memory_marker(reply)
        if notes and profile is not None and self.memory is not None:
            added = self.memory.add_notes(profile.uid, notes)
            if added:
                self.memory.save()
                LOG.info(
                    "[qq] 记住 %s：%s", profile.display, "；".join(notes)
                )
        text = sanitize(reply, int(self.cfg.max_reply_chars))
        # 表情包标记要在分片之前摘掉，否则标记本身会被当正文发出去
        text, tag, wants_sticker = parse_sticker_marker(text)
        # 2026-10-20：模型学会了「只回一个 [表情:xxx]」「只写（甩尾）」「只回？」，
        # 群里表现成「机器人只会发图 / 不说话」。只要正文没有实际内容就重试一次；
        # 用独立 session 键，避免把两次相同的提问塞进主历史。
        if not message_has_content(text):
            LOG.info("[qq] 正文过短/只有标记，重试要求说人话 session=%s", session)
            try:
                retry = await self.model.ask(
                    f"{session}#text-only",
                    prompt[: int(self.cfg.max_prompt_chars)],
                    system_suffix=(
                        "\n\n这次只说话：用中文回一句完整的话（至少 10 个字），"
                        "不能只写括号动作、一个问号，也不要输出任何 [表情:...] 标记。"
                    ),
                )
                retry, retry_notes = parse_memory_marker(retry)
                if retry_notes and profile is not None and self.memory is not None:
                    if self.memory.add_notes(profile.uid, retry_notes):
                        self.memory.save()
                text = sanitize(retry, int(self.cfg.max_reply_chars))
                text, retry_tag, retry_sticker = parse_sticker_marker(text)
                if message_has_content(text):
                    tag, wants_sticker = retry_tag, retry_sticker
            except Exception:
                LOG.exception("[qq] 正文重试失败 session=%s", session)
            if not message_has_content(text):
                # 第二次还是没有正文：宁可回一句完整吐槽，也不能只发图/只发「？」
                LOG.warning("[qq] 重试仍无正文，回兜底句 session=%s", session)
                text = "？ 你发的这个，本鲸鱼没看懂，说人话。"
                wants_sticker = False
        LOG.info("[qq] %s 用时 %.1fs: %s", session, time.monotonic() - started, text[:60])
        await self._reply_with_sticker(text, tag, wants_sticker, sender, image_sender)

    async def _reply_with_sticker(
        self,
        text: str,
        tag: str | None,
        wants_sticker: bool,
        sender: Sender,
        image_sender: ImageSender | None,
    ) -> None:
        """发出正文，随后按需补一张表情包。

        官方平台：被动回复同一 msg_id 最多 5 条，``msg_seq`` 必须互不相同，
        所以正文与表情包各占一个序号（正文可能分片，于是表情包排在它们之后）。
        """
        seq = 1
        if text:
            for part in split_message(text):
                await sender(part, seq)
                seq += 1

        if not wants_sticker or image_sender is None or not self.sticker_enabled:
            if wants_sticker and image_sender is not None:
                LOG.info("[qq] 表情包功能未启用，忽略标记")
            return

        sticker = (
            self.stickers.pick_by_tag(tag, exclude=self._recent_stickers)
            if tag else None
        )
        if sticker is None:
            # 库是空的，或模型编了一个库里没有、也没有情绪近邻的标签。
            # 这里**不再退回随机**：随机挑一张跟当前语气无关的图，
            # 正是主人反馈"表情包和对话不搭"的主要来源。
            LOG.info("[qq] 没有可用表情包 tag=%s 库内=%d", tag, len(self.stickers))
            return
        neighbor = getattr(self.stickers, "last_pick_neighbor", "")
        if neighbor:
            LOG.info("[qq] 表情标签降级命中 %s", neighbor)

        data = self.stickers.read_bytes(sticker)
        if not data:
            return

        # 表情包最多再占一条；若正文已经用掉 5 条，就只能放弃了
        if seq > MAX_PASSIVE_REPLIES:
            LOG.warning("[qq] 正文已用满 %d 条，放弃表情包", MAX_PASSIVE_REPLIES)
            return
        try:
            await image_sender(data, seq)
            self._recent_stickers.append(sticker.sha1)
            # 自学习：模型用某个标签成功发出了这张图，说明这个标签至少是
            # 「模型认为它属于这个情绪」。攒两次以上，库里就慢慢有了可用的标签，
            # 不再完全依赖人工 stickers.py 打标。
            if tag:
                self.stickers.add_tags([tag.strip().lower()], sha1=sticker.sha1)
            LOG.info(
                "[qq] 已发送表情包 tag=%s %s (%d bytes)",
                tag or "-", sticker.filename, len(data),
            )
        except Exception:
            # 发图失败不能影响已经发出的正文
            LOG.exception("[qq] 表情包发送失败 tag=%s", tag)

    async def _answer_image(
        self, session: str, action: ImageAction, sender: Sender, image_sender: ImageSender
    ) -> None:
        """出图或改图：先腾显存（与聊天模型互斥），生成后交给调用方上传发送。"""
        is_edit = action.kind == "edit"
        # 先回一句，别让用户干等 30~70 秒
        await sender(str(self.cfg.image_edit_ack_text if is_edit else self.cfg.image_ack_text), 1)
        started = time.monotonic()
        # 先取"当前是否该用 PE"，**再**提为主。顺序反了的话，第一次出图就会被
        # 判成"生图已为主"而跳过 PE——可那时它还没占据主导，白白损失画质。
        use_pe = self.priority.use_pe()
        promoted = self.priority.note_image()
        try:
            await self.image.release_chat_model(self.model.client)
            if is_edit:
                reference = self.cache.get(session, action.ref_index or 1)
                if reference is None:
                    raise ImageGenError("参考图已不在缓存里（可能被新图挤掉了）")
                png = await self.image.edit(self.model.client, reference["image"], action.prompt)
            else:
                # use_pe 在提为主之前就取好了：第一次出图仍带 PE，之后才简化
                png = await self.image.generate(
                    self.model.client, action.prompt, use_pe=use_pe
                )
            jpeg = encode_jpeg(png, int(self.cfg.image_jpeg_quality))
            await image_sender(jpeg, 2)
        except Exception:
            LOG.exception("[qq] 出图失败 session=%s edit=%s", session, is_edit)
            await sender(str(self.cfg.image_error_text), 3)
            return
        self.cache.add(session, png, action.prompt)
        self.comfy_dirty = True
        LOG.info(
            "[qq] %s完成 %s 用时 %.1fs jpeg=%d 字节，缓存 %d 张（%s%s）",
            "改图" if is_edit else "出图",
            session,
            time.monotonic() - started,
            len(jpeg),
            self.cache.count(session),
            ("PE=%s" % ("用" if use_pe else "跳过")) if not is_edit else "编辑链路",
            "，本次提为主" if promoted else "",
        )


class QQBotClient(botpy.Client):
    """官方 SDK 客户端：把群聊/单聊消息转给本地模型。"""

    def __init__(
        self,
        responder: Responder,
        policy: AutoChatPolicy | None = None,
        state: AutoChatState | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.responder = responder
        self.policy = policy
        self.state = state if state is not None else AutoChatState()
        self.scheduler = (
            AutoChatScheduler(self, policy, self.state, responder) if policy is not None else None
        )
        # 旧行为（auto_chat 关闭时）用的每群冷却（官方路线本来没有任何限流）
        self._last_reply_at: dict[str, float] = {}
        self._reaper_task: asyncio.Task[None] | None = None
        # 防抖聚合：同一人连发几条短消息时先攒 1 秒再判定，避免对着半句话作答。
        self._debounce = max(0.0, policy.float_setting("debounce_seconds", 0.0)) if policy else 0.0
        self._pending: dict[str, list[Any]] = {}
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}

    def _send_guard(self) -> SendGuard:
        """发送熔断器（惰性创建）。连续失败到阈值就先停一会儿，别一直撞墙。"""
        guard = getattr(self, "_send_guard_obj", None)
        if guard is None:
            guard = SendGuard()
            self._send_guard_obj = guard
        return guard

    def _senders(self, message: Any) -> tuple[Sender, ImageSender]:
        """构造文本/图片两个发送回调；群与单聊的上传路由不同。

        两个回调都过一道发送熔断：连续发不出去时先停一会，
        避免对着已经失败的接口猛刷（参考项目的做法，按本项目需要简化）。
        """
        group_openid = getattr(message, "group_openid", None)

        async def sender(content: str, seq: int) -> None:
            guard = self._send_guard()
            if not guard.allow():
                LOG.warning("[qq] 发送熔断中，跳过本条文本（连续失败 %d 次）", guard.failures)
                return
            # 被动回复：msg_id 5 分钟内有效，同一 msg_id 最多 5 条，msg_seq 必须不同。
            try:
                await message.reply(msg_type=0, content=content, msg_seq=seq)
            except Exception:
                guard.fail()
                raise
            guard.ok()

        async def image_sender(data: bytes, seq: int) -> None:
            guard = self._send_guard()
            if not guard.allow():
                LOG.warning("[qq] 发送熔断中，跳过本条图片（连续失败 %d 次）", guard.failures)
                return
            try:
                if group_openid:
                    info = await self._upload_image(
                        {"group_openid": group_openid}, "/v2/groups/{group_openid}/files", data
                    )
                else:
                    info = await self._upload_image(
                        {"openid": message.author.user_openid}, "/v2/users/{openid}/files", data
                    )
                await message.reply(msg_type=7, media={"file_info": info}, msg_seq=seq)
            except Exception:
                guard.fail()
                raise
            guard.ok()

        return sender, image_sender

    async def on_ready(self) -> None:
        """连接建立后的回调。"""
        # 注意：Client 把 Intents 存成 int，这里没有 .value。
        LOG.info(
            "[qq] 机器人已上线: %s intents=%s 主=%s",
            self.robot.name, self.intents, self.responder.priority.primary,
        )
        # 生图为主时，闲置够久自动把聊天切回主并释放 ComfyUI
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(
                self.responder.priority.reaper(self.responder.model.client)
            )
        # 定时主动发言要等连上网关之后再起（发送依赖已鉴权的 api）
        if self.scheduler is not None:
            self.scheduler.start()
        # MCP 外部工具（如星露谷桥）：连上后模型就能调用 mcp__* 工具。
        # 放在后台任务里，失败只记日志，不影响网关和本体。
        if getattr(self, "_mcp_task", None) is None:
            self._mcp_task = asyncio.create_task(self._init_mcp())
        # 星露谷自主循环：她自己看状态、决定做什么、做完来 QQ 汇报
        if getattr(self, "_game_loop", None) is None:
            self._game_loop = self._build_game_loop()
            if self._game_loop is not None:
                self._game_loop.start()

    def _build_game_loop(self):
        """按配置构造自主循环；没开或没配汇报目标就返回 None。"""
        try:
            from game_loop import GameLoop

            values = self.responder.cfg.values
            conf = values.get("game_loop") or {}
            if not conf.get("enabled"):
                LOG.info("[qq][game] 未启用（config.json 的 game_loop.enabled=false）")
                return None
            servers = values.get("mcp_servers") or []
            if not servers:
                LOG.info("[qq][game] 没有 MCP server，跳过自主循环")
                return None
            target: dict[str, str] = {}
            if conf.get("report_group"):
                target["group_openid"] = str(conf["report_group"])
            elif conf.get("report_user"):
                target["user_openid"] = str(conf["report_user"])
            if not target:
                LOG.warning("[qq][game] 没配 report_group / report_user，只记录不汇报")
            return GameLoop(
                client=self,
                model=self.responder.model,
                mcp_servers=servers,
                report_target=target,
                interval=float(conf.get("interval_seconds") or 300),
                log=lambda fmt, *a: LOG.info(fmt, *a),
            )
        except Exception as error:  # noqa: BLE001
            LOG.warning("[qq][game] 自主循环构建失败：%s", error)
            return None

    async def _init_mcp(self) -> None:
        """后台探测 MCP servers：**只有游戏在跑才连**（按需，避免干扰纯聊天）。"""
        try:
            from mcp_bridge import ensure_servers

            configs = self.responder.cfg.values.get("mcp_servers") or []
            if not configs:
                LOG.info("[qq][mcp] 未配置 MCP server（config.json 的 mcp_servers 为空）")
                return
            result = await ensure_servers(configs)
            if not result:
                LOG.info("[qq][mcp] 游戏未运行（bridge 文件不存在），暂不连接 MCP")
                return
            for name, status in result.items():
                LOG.info("[qq][mcp] %s: %s", name, status)
        except Exception as error:  # noqa: BLE001
            LOG.warning("[qq][mcp] 初始化失败：%s", error)

    async def _upload_image(self, route_kwargs: dict[str, Any], route_path: str, data: bytes) -> str:
        """上传图片到 QQ 富媒体接口，返回 ``file_info``。

        为什么不用 SDK 的 ``post_group_file``：它只接受 ``url``，而本机没有公网 IP，
        腾讯服务器拉不到我们的图。官方接口本身支持 ``file_data``（base64），
        只是 SDK 没暴露，所以这里直接走底层路由，复用 botpy 已鉴权的 http 客户端。
        """
        route = Route("POST", route_path, **route_kwargs)
        payload = {
            "file_type": 1,  # 1 = 图片（png/jpg）
            "file_data": base64.b64encode(data).decode(),
            "srv_send_msg": False,  # 只上传，不立即发送
        }
        result = await self.api._http.request(route, json=payload)
        info = result.get("file_info") if isinstance(result, dict) else getattr(result, "file_info", None)
        if not info:
            raise RuntimeError(f"富媒体上传未返回 file_info: {str(result)[:200]}")
        return info

    async def _harvest_stickers(self, message: Any) -> list[str]:
        """把消息里的图片附件收进表情包库（自动收集）。

        官方事件里 ``attachments`` 会推**所有**图片，包括群友的照片和截图。
        能不能收交给 ``StickerLibrary.should_collect`` 判断（gif 必收，
        png/jpg 仅限小尺寸），否则"发个表情"可能翻出人家自拍。

        这个函数**绝不能抛异常**：它跑在处理用户消息的路径上，收图失败不能影响回复。
        返回这几张图在库里的标签（供 ``message_text`` 告诉模型图的意思）。
        """
        lib = getattr(self.responder, "stickers", None)
        if lib is None:
            return []
        attachments = getattr(message, "attachments", None) or []
        if not attachments:
            return []

        # 来源：用 openid 便于回溯，但日志里不打全量，避免刷屏
        group_openid = getattr(message, "group_openid", None)
        author = getattr(message, "author", None)
        who = (
            getattr(author, "member_openid", "")
            or getattr(author, "user_openid", "")
            or ""
        )
        source = f"group:{group_openid}" if group_openid else f"c2c:{who}"
        # 弱标签：同一条消息里如果带文字（「笑死」「麻了」「破防」），文字往往就是
        # 这张图的情绪注脚。本机没有视觉模型，这是零成本的近似，猜错也只是多一个标签。
        weak_tags = infer_tags_from_text(face_text(getattr(message, "content", "") or ""))
        collected_tags: list[str] = []

        for att in attachments:
            try:
                url = getattr(att, "url", None)
                if not url:
                    continue
                content_type = (getattr(att, "content_type", "") or "").lower()
                # 只处理图片，语音/视频/文件一律跳过
                if not content_type.startswith("image/"):
                    continue

                size = int(getattr(att, "size", 0) or 0)
                # 已知太大就别下载了；未知大小则设个上限兜底
                if size and size > MAX_STICKER_BYTES:
                    continue
                width = getattr(att, "width", None)
                height = getattr(att, "height", None)

                # 下载附件。注意：botpy 的 BotHttp 只有 ``request(route)`` 和一个私有的
                # aiohttp session，**没有 ``.get()``**——早先这里写的是 ``self.api._http.get()``，
                # 每次都会抛 AttributeError 被下面的大 except 吞掉，表情包收集因此一直是坏的。
                # 这里改用聊天模型那个 httpx 连接池：同一个事件循环，够用，也不必碰 SDK 私有 API。
                resp = await self.responder.model.client.get(url, timeout=30.0, follow_redirects=True)
                data = resp.content
                if not data or len(data) > MAX_STICKER_BYTES:
                    continue

                st = lib.collect(
                    data,
                    content_type=content_type,
                    filename=str(getattr(att, "filename", "") or ""),
                    width=width,
                    height=height,
                    source=source,
                )
                if st is not None:
                    if weak_tags:
                        # add_tags 内部会 save；只在真的加进去时才多一次写盘
                        lib.add_tags(weak_tags, sha1=st.sha1)
                    for tag in st.tags:
                        if tag not in collected_tags:
                            collected_tags.append(tag)
                    LOG.info(
                        "[qq] 收进表情库 %s (%d bytes, %s) 库内共 %d 张 标签=%s",
                        st.filename, len(data), content_type or "?", len(lib),
                        ",".join(st.tags) or "-",
                    )
            except Exception:
                # 单张失败不影响其它附件
                LOG.warning("[qq] 收藏表情包失败", exc_info=True)
        return collected_tags[:3]

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """群里 @ 机器人（intents: public_messages）。"""
        LOG.info("[qq] 群@消息 group=%s user=%s", message.group_openid, message.author.member_openid)
        if self._is_duplicate(message, getattr(message, "content", "") or ""):
            return
        tags = await self._harvest_stickers(message)
        sender, image_sender = self._senders(message)
        text, _ = message_text(message, tags)
        await self.responder.answer(
            f"group:{message.group_openid}",
            text,
            sender,
            image_sender,
            speaker=speaker_of(message, "group"),
        )
        # 记账：一是喂给自动接话的冷却，二是防止同一条消息被
        # GROUP_AT_MESSAGE_CREATE 与 GROUP_MESSAGE_CREATE 各回一次（双触发）。
        self.state.note_reply(str(message.group_openid))

    def _is_duplicate(self, message: object, content: str) -> bool:
        """平台重推的同一条消息只处理一次（去重窗口 5 分钟）。

        QQ 会重推消息：同一张表情会被收两次、同一句话可能被回两遍。
        键优先用 ``message.id``，拿不到时退回「发送者+内容」指纹。
        """
        guard = getattr(self, "_dedup", None)
        if guard is None:
            guard = MessageDedup()
            self._dedup = guard
        key = MessageDedup.key_of(message, content)
        if guard.seen(key):
            LOG.info("[qq] 重复消息已跳过 key=%s", key[:28])
            return True
        return False

    def _mentions_bot(self, text: str) -> bool:
        """全量群消息里判断「是否 @ 了机器人」。

        开了「接收所有消息」后，平台把 @ 消息也推到全量事件，并把 @ 渲染成
        ``<@openid>`` 留在正文里；机器人自己的 openid 记在 ``auto_chat.bot_openid``。

        没有配置 openid 时一律返回 False —— 退回原来的概率行为，宁可少答，
        也不要把别人之间的 @ 误判成 @ 机器人。
        """
        policy = getattr(self, "policy", None)
        raw = getattr(policy, "raw", None)
        ids = (raw or {}).get("bot_openid") or ""
        if isinstance(ids, str):
            ids = [p for p in re.split(r"[,\s]+", ids) if p]
        if not ids:
            return False
        body = text or ""
        return any(f"<@{i}>" in body for i in ids)

    async def on_group_message_create(self, message: GroupMessage) -> None:
        """群消息（全量模式）：群里每条消息都推过来，不需要 @。

        两个前提，缺一不可：

        1. **在手机 QQ 里开**：群设置 → 机器人设置 → 「机器人可获取的群聊消息范围」
           设为「获取群内全部消息」（想主动发言还要开「机器人主动在群聊内发言」）。
           注意这**不是**开放平台后台的开关——后台找不到这一项。
        2. 配置里 ``auto_chat.enabled=true``（推荐，带白名单/冷却/额度），
           或退回旧的 ``group_reply_all=true``（只在全部群里按固定冷却回复）。

        ``auto_chat`` 关闭时本函数行为与加这套机制之前**完全一致**。

        2026-10-20：加了一层「防抖聚合」。同一个人连发两三条短消息时，旧逻辑来一条
        判一条，经常对着「怎么跑这」这种半句话作答。现在先攒 ``debounce_seconds``
        秒再判定，判定用的文本是这几条拼起来的；@ 机器人不攒，立刻处理。
        """
        # 去重要放在最前面：平台重推的同一张表情不该被收两次、同一句话也不该被回两遍。
        if self._is_duplicate(message, getattr(message, "content", "") or ""):
            return
        # 收藏表情包要放在所有 return **之前**：收图这件事与「要不要回这条消息」
        # 无关，群里别人发的表情包同样值得收进库。
        tags = await self._harvest_stickers(message)

        author = getattr(message, "author", None)
        if author is not None and getattr(author, "bot", False):
            return  # 不理其他机器人，避免互相刷屏

        raw = getattr(message, "content", "") or ""
        if self._debounce > 0 and not self._mentions_bot(raw):
            self._queue_group_message(message, tags)
            return
        # @ 消息不等待，但把这个人在等待窗口里攒的几条一起带进来，避免重复处理
        batch = self._take_pending(message) + [(message, tags)]
        await self._handle_group_message(
            message, batch=batch if len(batch) > 1 else None, tags=tags
        )

    def _queue_group_message(self, message: Any, tags: list[str] | None = None) -> None:
        """把一条群消息放进等待窗口；同一人连发时只保留最新边界。"""
        key = self._batch_key(message)
        self._pending.setdefault(key, []).append((message, tags or []))
        task = self._pending_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        self._pending_tasks[key] = asyncio.create_task(self._flush_group_message(key))

    @staticmethod
    def _batch_key(message: Any) -> str:
        group = str(getattr(message, "group_openid", "") or "")
        author = getattr(message, "author", None)
        uid = getattr(author, "member_openid", "") or "?"
        return f"{group}|{uid}"

    def _take_pending(self, message: Any) -> list[tuple[Any, list[str]]]:
        """取走同一发送者正在等待的消息，并取消它的定时任务。"""
        key = self._batch_key(message)
        task = self._pending_tasks.pop(key, None)
        if task is not None:
            task.cancel()
        return self._pending.pop(key, [])

    async def _flush_group_message(self, key: str) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        self._pending_tasks.pop(key, None)
        batch = self._pending.pop(key, [])
        if not batch:
            return
        try:
            await self._handle_group_message(batch[-1][0], batch=batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("[qq] 聚合群消息处理失败 key=%s", key)

    async def _handle_group_message(
        self,
        message: Any,
        batch: list[tuple[Any, list[str]]] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """真正做判定与回复。``batch`` 非空时，文本取这几条的合并结果。"""
        group = str(message.group_openid)
        author = getattr(message, "author", None)
        who = getattr(author, "member_openid", "") or "?"

        if batch:
            parts: list[str] = []
            has_image = False
            for item, item_tags in batch:
                part, item_has_image = message_text(item, item_tags)
                has_image = has_image or item_has_image
                if part:
                    parts.append(part)
            text = "\n".join(parts).strip()
        else:
            text, has_image = message_text(message, tags)

        if not text:
            # 诊断用：日志里出现「too_short 文本=」时，能看出是哪种消息被解析成了空
            raw_content = getattr(message, "content", "") or ""
            att_info = [
                (getattr(att, "content_type", "") or "?", int(getattr(att, "size", 0) or 0))
                for att in (getattr(message, "attachments", None) or [])
            ]
            LOG.info("[qq][diag] 空文本消息 raw=%r att=%s", raw_content[:120], att_info)

        # 记账要放在判定之前：这一条本来就是「最近的群消息」，闸门需要看到它
        self.state.note_message(group, who)

        policy = self.policy
        if policy is not None and policy.enabled:
            decision = policy.decide(
                group=group,
                text=text,
                # 全量事件里 @ 会被渲染成 <@openid>，用它比对机器人自己的 openid。
                # 旧版这里硬编码 False，导致「@ 机器人」只能靠概率命中（常常不回）。
                mentioned=self._mentions_bot(text),
                seconds_since_last=self.state.seconds_since_last(group),
                today_count=self.state.today_count(group),
                has_image=has_image,
                gate_ctx={
                    "recent": self.state.recent_messages(group),
                    "hourly_count": self.state.hourly_count(group),
                    "ignored_last": self.state.ignored_last(group),
                },
            )
            LOG.info(
                "[qq][auto] group=%s user=%s 判定=%s(%s) 图=%s 文本=%s",
                group, who, decision.reason, decision.layer, int(has_image), text[:40],
            )
            if not decision.allow:
                return
            if policy.dry_run:
                LOG.info("[qq][auto][dry_run] 本应回复 group=%s：%s", group, text[:60])
                return
            if self.responder.model.overloaded:
                # 自动接话优先级低于真人：直接放弃，不排队、不回「忙」
                LOG.info("[qq][auto] 模型忙，放弃本次接话 group=%s", group)
                return
            sender, image_sender = self._senders(message)
            await self.responder.answer(
                f"group:{group}", text, sender, image_sender,
                auto=True, speaker=speaker_of(message, "group"),
            )
            self.state.note_reply(group)
            return

        # ---- 以下为旧行为：auto_chat 未启用时走这里 ----
        if not self.responder.cfg.group_reply_all:
            return
        cooldown = float(self.responder.cfg.group_cooldown_seconds)
        now = time.monotonic()
        if cooldown > 0 and now - self._last_reply_at.get(group, 0.0) < cooldown:
            LOG.info("[qq] 全量群消息冷却中（%.0fs），跳过 group=%s", cooldown, group)
            return
        self._last_reply_at[group] = now
        LOG.info("[qq] 全量群消息 group=%s user=%s", group, who)
        sender, image_sender = self._senders(message)
        await self.responder.answer(
            f"group:{group}", text, sender, image_sender,
            speaker=speaker_of(message, "group"),
        )

    async def on_group_msg_receive(self, event: Any) -> None:
        """群主开启了「机器人主动在群聊内发言」，把这个群记进白名单。"""
        group = str(getattr(event, "group_openid", "") or "")
        if not group:
            return
        self.state.note_proactive(group, True)
        LOG.info("[qq][auto] 群 %s 已允许主动消息", group)

    async def on_group_msg_reject(self, event: Any) -> None:
        """群主关掉了主动发言。记住它，之后别再白发（发了也是失败）。"""
        group = str(getattr(event, "group_openid", "") or "")
        if not group:
            return
        self.state.note_proactive(group, False)
        LOG.info("[qq][auto] 群 %s 已拒绝主动消息，后续跳过", group)

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """用户单聊机器人。"""
        LOG.info("[qq] 单聊 user=%s", message.author.user_openid)
        if self._is_duplicate(message, getattr(message, "content", "") or ""):
            return
        tags = await self._harvest_stickers(message)
        sender, image_sender = self._senders(message)
        text, _ = message_text(message, tags)
        await self.responder.answer(
            f"c2c:{message.author.user_openid}",
            text,
            sender,
            image_sender,
            speaker=speaker_of(message, "c2c"),
        )

    async def on_group_add_robot(self, event: Any) -> None:
        """被拉进群时打个招呼（用 event_id 做被动回复）。"""
        try:
            await self.api.post_group_message(
                group_openid=event.group_openid,
                event_id=event.event_id,
                msg_type=0,
                content="大家好，我是接在本地模型上的助手，@我就能聊。",
                msg_seq=1,
            )
        except Exception:
            LOG.exception("[qq] 入群问候失败")


def build_config(path: Path) -> Config:
    """合并内置默认值、config.json 与 QQ 凭据。"""
    merged = dict(json.loads(path.read_text("utf-8"))) if path.exists() else {}
    cfg = Config(merged)
    cfg.values.setdefault("qq_appid", os.environ.get("QQ_APPID", ""))
    cfg.values.setdefault("qq_secret", os.environ.get("QQ_SECRET", ""))
    return cfg


async def selftest(cfg: Config) -> int:
    """只验证本地模型那一半，不连腾讯网关。"""
    personas = PersonaStore(cfg, HERE / "persona.state", HERE / "roles")
    model = LocalModel(cfg, personas)
    try:
        print(f"[selftest] 当前人设：{personas.label()}")
        started = time.monotonic()
        reply = await model.ask("selftest", "用一句话说说你在群里能帮上什么忙。")
        print(f"[selftest] {time.monotonic() - started:.1f}s  {sanitize(reply, 200)}")
        return 0
    except Exception as error:
        print(f"[selftest] FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        await model.aclose()


def main() -> int:
    """解析参数并启动。"""
    parser = argparse.ArgumentParser(description="QQ 开放平台机器人 ↔ 本地 Ollama 桥接")
    parser.add_argument("--config", default=str(HERE / "config.json"))
    parser.add_argument("--selftest", action="store_true", help="只测本地模型，不连腾讯网关")
    args = parser.parse_args()

    cfg = build_config(Path(args.config))
    if args.selftest:
        return asyncio.run(selftest(cfg))

    appid = str(cfg.values.get("qq_appid") or "")
    secret = str(cfg.values.get("qq_secret") or "")
    if not appid or not secret:
        print("缺少凭据：设置 QQ_APPID / QQ_SECRET 环境变量，或写进 config.json。", file=sys.stderr)
        return 2

    personas = PersonaStore(cfg, HERE / "persona.state", HERE / "roles")
    responder = Responder(LocalModel(cfg, personas), cfg, personas)

    # 自动聊天：策略与账本是纯内存对象，跟 botpy 完全解耦
    policy = AutoChatPolicy(cfg.values.get("auto_chat"))
    state = AutoChatState()
    if policy.enabled:
        LOG.info(
            "[qq][auto] 自动聊天已启用：mode=%s dry_run=%s 白名单 %d 个群 冷却=%.0fs 概率=%.2f",
            policy.reply_mode,
            policy.dry_run,
            len(policy.allowed_groups),
            policy.float_setting("cooldown_seconds", 90.0),
            policy.float_setting("probability", 0.1),
        )
        LOG.info(
            "[qq][auto] 发言时机闸门=%s 阈值=%.2f 硬冷却=%.0fs 每小时上限=%d 防抖=%.1fs",
            "开" if policy.gate_enabled else "关",
            policy.gate.float_setting("min_score"),
            policy.gate.float_setting("hard_cooldown_seconds"),
            policy.gate.int_setting("max_per_hour"),
            policy.float_setting("debounce_seconds", 0.0),
        )
        if not policy.allowed_groups:
            LOG.warning("[qq][auto] allowed_groups 为空 = 一个群都不接（这是刻意的安全默认）")
    else:
        LOG.info("[qq][auto] 自动聊天未启用（auto_chat.enabled=false）")

    # 想让群里"不 @ 也回"，就得先让 SDK 认得全量群消息事件。
    # 还要在**手机 QQ** 里开：群设置 → 机器人设置 → 消息范围设为「获取群内全部消息」。
    # （不是开放平台后台的开关，后台找不到这一项）
    if install_group_message_create_support():
        LOG.info("[qq] 已为 botpy 补上 group_message_create 事件支持")

    # botpy 把发送者昵称丢了，不补上就只能看到一串 openid
    patched = install_sender_username_support()
    if patched:
        LOG.info("[qq] 已为 botpy 补上发送者昵称：%s", "、".join(patched))
    if responder.memory is not None:
        LOG.info(
            "[qq] 用户记忆已启用：已记住 %d 人（%s）",
            len(responder.memory.users),
            responder.memory.path,
        )
    else:
        LOG.info("[qq] 用户记忆未启用（user_memory_enabled=false）")
    if policy.enabled:
        LOG.info(
            "[qq][auto] 注意：若日志里始终没有「全量群消息」，说明手机 QQ 的"
            "「获取群内全部消息」还没开，腾讯不会推该事件。"
        )
    elif cfg.group_reply_all:
        LOG.info(
            "[qq] group_reply_all=True：群里不 @ 也会回复，每群冷却 %.0fs。"
            "若日志里始终没有「全量群消息」，说明手机 QQ 的「获取群内全部消息」没开。",
            float(cfg.group_cooldown_seconds),
        )

    intents = botpy.Intents(public_messages=True)
    client = QQBotClient(responder, policy, state, intents=intents)
    LOG.info("[qq] 正在连接网关（AppID=%s）… 当前人设=%s", appid, personas.label())
    client.run(appid=appid, secret=secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
