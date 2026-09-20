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
import json
import os
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bot import Config, PersonaStore, clean_reply  # noqa: E402  复用非官方路线的配置与清洗逻辑
from web_tools import chat_with_tools  # noqa: E402  联网工具（两条路线共用）

import botpy  # noqa: E402
from botpy import logging as botpy_logging  # noqa: E402
from botpy.message import C2CMessage, GroupMessage  # noqa: E402

LOG = botpy_logging.get_logger()

# 平台错误码 40054010：消息里不允许出现 URL；40054007：消息长度超限。
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# 兜底分片长度，避免撞上长度上限。
CHUNK_CHARS = 450

# 事件最外层 id 与 msg_id 的对应关系见官方文档"发送群聊消息"。
PASSIVE_WINDOW_SECONDS = 300.0
MAX_PASSIVE_REPLIES = 5


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
            self.client, self.cfg, messages, max_rounds=2 if self.cfg.tools_enabled else 0
        )
        content = clean_reply(content)
        if not content:
            raise RuntimeError("模型返回空正文（思考没关掉？检查 reasoning_effort）")
        return content

    async def ask(self, session: str, prompt: str) -> str:
        """带历史的串行推理。"""
        self._waiting += 1
        try:
            async with self._lock:
                buf = self.history.setdefault(
                    session, deque(maxlen=max(1, int(self.cfg.history_turns)) * 2)
                )
                system = self.personas.prompt() if self.personas else str(self.cfg.system_prompt)
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


class Responder:
    """取回复 → 清洗 → 分片 → 回帖。与 botpy 解耦，便于离线测试。"""

    def __init__(self, model: LocalModel, cfg: Config, personas: PersonaStore) -> None:
        self.model = model
        self.cfg = cfg
        self.personas = personas

    async def answer(self, session: str, prompt: str, sender: Sender) -> None:
        """处理一条用户消息。``sender(内容, msg_seq)`` 由调用方提供。"""
        # 人格指令优先级最高：模型正忙时也应该能切
        handled, command_reply = self.personas.handle_command(prompt)
        if handled:
            LOG.info("[qq] 人格指令 %s -> %s", session, command_reply)
            await sender(command_reply, 1)
            return
        if self.model.overloaded:
            await sender(str(self.cfg.busy_text), 1)
            return
        started = time.monotonic()
        try:
            reply = await self.model.ask(session, prompt[: int(self.cfg.max_prompt_chars)])
        except Exception:
            LOG.exception("[qq] 本地模型调用失败 session=%s", session)
            await sender(str(self.cfg.error_text), 1)
            return
        text = sanitize(reply, int(self.cfg.max_reply_chars))
        LOG.info("[qq] %s 用时 %.1fs: %s", session, time.monotonic() - started, text[:60])
        for seq, part in enumerate(split_message(text), start=1):
            await sender(part, seq)


class QQBotClient(botpy.Client):
    """官方 SDK 客户端：把群聊/单聊消息转给本地模型。"""

    def __init__(self, responder: Responder, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.responder = responder

    async def on_ready(self) -> None:
        """连接建立后的回调。"""
        # 注意：Client 把 Intents 存成 int，这里没有 .value。
        LOG.info("[qq] 机器人已上线: %s intents=%s", self.robot.name, self.intents)

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        """群里 @ 机器人（intents: public_messages）。"""
        LOG.info("[qq] 群消息 group=%s user=%s", message.group_openid, message.author.member_openid)

        async def sender(content: str, seq: int) -> None:
            # 被动回复：msg_id 5 分钟内有效，同一 msg_id 最多 5 条，msg_seq 必须不同。
            await message.reply(msg_type=0, content=content, msg_seq=seq)

        await self.responder.answer(f"group:{message.group_openid}", message.content or "", sender)

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        """用户单聊机器人。"""
        LOG.info("[qq] 单聊 user=%s", message.author.user_openid)

        async def sender(content: str, seq: int) -> None:
            await message.reply(msg_type=0, content=content, msg_seq=seq)

        await self.responder.answer(f"c2c:{message.author.user_openid}", message.content or "", sender)

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
    personas = PersonaStore(cfg, HERE / "persona.state")
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

    personas = PersonaStore(cfg, HERE / "persona.state")
    responder = Responder(LocalModel(cfg, personas), cfg, personas)
    intents = botpy.Intents(public_messages=True)
    client = QQBotClient(responder, intents=intents)
    LOG.info("[qq] 正在连接网关（AppID=%s）… 当前人设=%s", appid, personas.label())
    client.run(appid=appid, secret=secret)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
