#!/usr/bin/env python3
"""OneBot v11 反向 WebSocket → Ollama(OpenAI 兼容) 的 QQ 群聊机器人适配器。

设计约束来自本机实测，改动前先读 ~/qq-bot/PLAN.md 第 1、6 节：

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
    "reply_with_at": True,
    "busy_text": "我这边排队有点长，稍等一下再叫我～",
    "error_text": "本地模型好像卡住了，等会儿再试试。",
    "reset_words": ["重置", "reset", "清空"],
    "personas": {},
    "persona": "",
    "persona_words": ["人格", "人设", "persona"],
    "persona_alias_words": [
        "切换成", "切换到", "切换为", "切换",
        "换成", "换为", "扮演", "变成", "变为",
    ],
    "persona_aliases": {},
    "tools_enabled": True,
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
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(cfg.request_timeout))

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
        if mentioned:
            for seg in event.get("message", []):
                if seg.get("type") == "at":
                    stripped = stripped.replace(f"@{self_id}", "", 1)
            return True, stripped.strip()
        return False, ""

    def _session_key(self, event: dict[str, Any]) -> str:
        if event.get("message_type") == "group":
            return f"group:{event.get('group_id')}"
        return f"private:{event.get('user_id')}"

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

        cooldown = float(self.cfg.group_cooldown_seconds)
        if event.get("message_type") == "group" and cooldown > 0:
            if time.monotonic() - self.last_reply_at.get(session, 0.0) < cooldown:
                LOG.info("cooldown skip session=%s", session)
                return

        if self.queue.qsize() >= int(self.cfg.queue_limit):
            LOG.warning("queue full (%s), rejecting session=%s", self.queue.qsize(), session)
            await self.send(ws, event, str(self.cfg.busy_text))
            return

        prompt = prompt[: int(self.cfg.max_prompt_chars)]
        LOG.info("enqueue session=%s prompt=%r qsize=%s", session, prompt[:60], self.queue.qsize())
        await self.queue.put({"ws": ws, "event": event, "session": session, "prompt": prompt})

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
        session = job["session"]
        turns = max(1, int(self.cfg.history_turns)) * 2
        buf = self.history.setdefault(session, deque(maxlen=turns))
        messages = [{"role": "system", "content": self.personas.prompt()}]
        messages.extend(dict(item) for item in buf)
        messages.append({"role": "user", "content": job["prompt"]})

        started = time.monotonic()
        reply = await self.ollama_chat(messages)
        elapsed = time.monotonic() - started
        reply = reply[: int(self.cfg.max_reply_chars)]
        LOG.info("reply session=%s in %.1fs: %r", session, elapsed, reply[:60])

        buf.append({"role": "user", "content": job["prompt"]})
        buf.append({"role": "assistant", "content": reply})
        self.last_reply_at[session] = time.monotonic()
        await self.send(job["ws"], job["event"], reply)

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
        """起 worker 与 WS 服务，直到被取消。"""
        self.worker_task = asyncio.create_task(self.worker())
        async with ws_serve(self.handler, self.cfg.host, int(self.cfg.port), ping_interval=30):
            LOG.info("listening ws://%s:%s  model=%s", self.cfg.host, self.cfg.port, self.cfg.model)
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
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print()
        LOG.info("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
