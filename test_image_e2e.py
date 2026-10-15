#!/usr/bin/env python3
"""端到端自测：假装自己是 NapCat，连上 bot.py 发一条 /画 指令，验证能收到图片。

不需要真实 QQ、不需要 Ollama 在跑（出图链路不依赖聊天模型）。
会在临时端口起一个 Bot 实例，不干扰正在运行的机器人。

用法::
    ~/.venvs/qqbot/bin/python test_image_e2e.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import websockets

import bot as qqbot

PORT = 6299  # 故意避开正式的 6199，避免和正在跑的机器人打架
TOKEN = "e2e-test-token"


async def main() -> int:
    cfg = qqbot.Config.load(Path(__file__).resolve().parent / "config.json")
    cfg.values["host"] = "127.0.0.1"
    cfg.values["port"] = PORT
    cfg.values["token"] = TOKEN
    cfg.values["group_cooldown_seconds"] = 0.0
    cfg.values["image_use_pe"] = True

    instance = qqbot.Bot(cfg)
    server_task = asyncio.create_task(instance.run())
    await asyncio.sleep(1.5)  # 等 WS 服务起来

    url = f"ws://127.0.0.1:{PORT}/?access_token={TOKEN}"
    print(f"[e2e] 连接 {url}")
    ack_seen = False
    image_seen: bytes | None = None

    try:
        async with websockets.connect(url) as ws:
            event = {
                "post_type": "message",
                "message_type": "group",
                "group_id": 10001,
                "user_id": 10002,
                "self_id": 10000,
                "message": [
                    {"type": "text", "data": {"text": "/画 一只戴着红围巾的小龙，在雨夜里举着灯笼"}}
                ],
            }
            await ws.send(json.dumps(event))
            print("[e2e] 已发送 /画 指令，等待回复…")

            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=float(cfg.image_timeout))
                payload = json.loads(raw)
                action = payload.get("action")
                message = (payload.get("params") or {}).get("message") or []
                kinds = [seg.get("type") for seg in message]
                print(f"[e2e] 收到 {action} 段类型={kinds}")

                for seg in message:
                    if seg.get("type") == "text" and seg["data"]["text"].strip():
                        print(f"[e2e]   文本: {seg['data']['text'].strip()[:60]}")
                        ack_seen = True
                    if seg.get("type") == "image":
                        data = seg["data"]["file"]
                        if data.startswith("base64://"):
                            b64 = data[len("base64://"):]
                            print(f"[e2e]   base64 载荷 {len(b64):,} 字节"
                                  f"（客户端上限 1 MiB，合规={len(b64) < 1048576}）")
                            image_seen = base64.b64decode(b64)
                if image_seen:
                    break
    except asyncio.TimeoutError:
        print("[e2e] 等待超时")
    finally:
        server_task.cancel()
        try:
            await server_task
        except (asyncio.CancelledError, Exception):
            pass
        await instance.aclose()

    print()
    if ack_seen:
        print("[e2e] ✓ 收到出图提示语")
    if image_seen:
        head = image_seen[:8]
        if head.startswith(b"\xff\xd8\xff"):
            suffix, kind = "jpg", "JPEG"
        elif head == b"\x89PNG\r\n\x1a\n":
            suffix, kind = "png", "PNG"
        else:
            print(f"[e2e] ✗ 不认识的图片格式，头部={head!r}")
            return 1
        out = Path(f"/tmp/qqbot_e2e.{suffix}")
        out.write_bytes(image_seen)
        print(f"[e2e] ✓ 收到图片段 {len(image_seen):,} 字节（{kind}）-> {out}")
        return 0
    print("[e2e] ✗ 没收到图片")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
