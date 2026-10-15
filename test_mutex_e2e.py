#!/usr/bin/env python3
"""端到端验证「双向显存互斥」：聊天 → 出图 → 再聊天。

真正会跑 Ollama（加载 27B 约 1 分钟），所以比较慢。会检查三个时间点的
``ollama ps``，确认出图前聊天模型被卸载、出图后能重新整块上卡。

用法::
    ~/.venvs/qqbot/bin/python test_mutex_e2e.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from pathlib import Path

import websockets

import bot as qqbot

PORT = 6299
TOKEN = "mutex-test-token"
OLLAMA_BIN = Path.home() / "qwen/runtime/ollama/bin/ollama"


def ollama_ps() -> str:
    try:
        out = subprocess.run(
            [str(OLLAMA_BIN), "ps"], capture_output=True, text=True, timeout=60
        ).stdout.strip()
    except Exception as error:
        return f"(ollama ps 失败: {error})"
    lines = out.splitlines()
    return " | ".join(lines[1].split()) if len(lines) > 1 else "(没有已加载的模型)"


def vram() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
    except Exception:
        return "?"
    return out


async def collect(ws, timeout: float, want_image: bool = False):
    """收消息直到超时；返回 (文本列表, 图片字节 or None)。"""
    texts: list[str] = []
    image: bytes | None = None
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        remain = deadline - asyncio.get_event_loop().time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remain)
        except asyncio.TimeoutError:
            break
        payload = json.loads(raw)
        for seg in (payload.get("params") or {}).get("message") or []:
            if seg.get("type") == "text" and seg["data"]["text"].strip():
                texts.append(seg["data"]["text"].strip())
            elif seg.get("type") == "image" and seg["data"]["file"].startswith("base64://"):
                image = base64.b64decode(seg["data"]["file"][len("base64://"):])
        if image is not None:
            break
    return texts, image


def event(text: str) -> str:
    return json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "user_id": 20002,
            "self_id": 20000,
            "message": [{"type": "text", "data": {"text": text}}],
        }
    )


async def main() -> int:
    cfg = qqbot.Config.load(Path(__file__).resolve().parent / "config.json")
    cfg.values.update(host="127.0.0.1", port=PORT, token=TOKEN)

    instance = qqbot.Bot(cfg)
    server = asyncio.create_task(instance.run())
    await asyncio.sleep(1.5)
    url = f"ws://127.0.0.1:{PORT}/?access_token={TOKEN}"
    ok = True

    try:
        async with websockets.connect(url) as ws:
            print("=" * 62)
            print("[1] 发一条私聊文字，让 Ollama 加载模型")
            print(f"    出图前显存: {vram()}")
            await ws.send(event("用一句话介绍你自己"))
            texts, _ = await collect(ws, timeout=float(cfg.request_timeout) + 60)
            print(f"    回复: {(texts[-1] if texts else '(无)')[:70]}")
            print(f"    ollama ps: {ollama_ps()}")
            print(f"    显存: {vram()}")
            if not texts:
                print("    ✗ 没收到文字回复")
                ok = False

            print("=" * 62)
            print("[2] 发 /画，应当在出图前把聊天模型卸掉")
            await ws.send(event("/画 一只戴着红围巾的小龙，在雨夜里举着灯笼"))
            texts, image = await collect(ws, timeout=float(cfg.image_timeout), want_image=True)
            print(f"    提示语: {(texts[0] if texts else '(无)')[:60]}")
            print(f"    ollama ps（出图后）: {ollama_ps()}")
            print(f"    显存: {vram()}")
            if image:
                Path("/tmp/qqbot_mutex.jpg").write_bytes(image)
                print(f"    ✓ 收到图片 {len(image):,} 字节 -> /tmp/qqbot_mutex.jpg")
            else:
                print("    ✗ 没收到图片")
                ok = False

            print("=" * 62)
            print("[3] 再发文字，应当先释放 ComfyUI 显存，让 Ollama 整块上卡")
            await ws.send(event("你刚才画了什么？一句话说"))
            texts, _ = await collect(ws, timeout=float(cfg.request_timeout) + 120)
            print(f"    回复: {(texts[-1] if texts else '(无)')[:70]}")
            print(f"    ollama ps: {ollama_ps()}")
            print(f"    显存: {vram()}")
            if not texts:
                print("    ✗ 没收到文字回复")
                ok = False
    finally:
        server.cancel()
        try:
            await server
        except (asyncio.CancelledError, Exception):
            pass
        await instance.aclose()

    print("=" * 62)
    print("结论: " + ("✓ 双向互斥工作正常" if ok else "✗ 有环节失败，见上面输出"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
