#!/usr/bin/env python3
"""端到端验证：出图 → 进缓存 → 按缓存里的图改 → 再进缓存。

会真实调用 ComfyUI 两次（约 30~70 秒/张）。用官方路线的 Responder，
但把发送回调换成假的，不连腾讯。

用法::
    ~/.venvs/qqbot/bin/python test_edit_cache_e2e.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import official_bot as ob
from bot import Config, PersonaStore

HERE = Path(__file__).resolve().parent
OUT = Path("/tmp")


async def main() -> int:
    cfg = Config.load(HERE / "config.json")
    personas = PersonaStore(cfg, HERE / "persona.state")
    model = ob.LocalModel(cfg, personas)
    responder = ob.Responder(model, cfg, personas)

    sent: list[tuple[str, int, str]] = []
    images: list[bytes] = []

    async def sender(text: str, seq: int) -> None:
        sent.append(("text", seq, text))

    async def image_sender(data: bytes, seq: int) -> None:
        sent.append(("image", seq, f"{len(data)} 字节"))
        images.append(data)

    session = "c2c:e2e"
    ok = True

    try:
        # ---------- 第一次：新画一张 ----------
        print("=" * 64)
        print("[1] 新画一张：「画一只戴着红围巾的小龙」")
        sent.clear()
        await responder.answer(session, "画一只戴着红围巾的小龙", sender, image_sender)
        print(f"    调用: {[(s[0], s[2][:24]) for s in sent]}")
        print(f"    缓存: {responder.cache.count(session)} 张 | {responder.cache.describe(session)}")
        if responder.cache.count(session) != 1 or len(images) != 1:
            print("    ✗ 第一次出图没有正确进缓存")
            ok = False
        else:
            (OUT / "e2e_step1.png").write_bytes(images[0])
            print("    ✓ 已出图并进缓存 -> /tmp/e2e_step1.png")

        first = images[0]

        # ---------- 第二次：按缓存里的那张改 ----------
        print("=" * 64)
        print("[2] 按上一张改：「把背景改成夜晚，加一点月光」")
        images.clear()
        sent.clear()
        await responder.answer(session, "把背景改成夜晚，加一点月光", sender, image_sender)
        print(f"    调用: {[(s[0], s[2][:24]) for s in sent]}")
        print(f"    缓存: {responder.cache.count(session)} 张 | {responder.cache.describe(session)}")
        if len(images) != 1 or responder.cache.count(session) != 2:
            print("    ✗ 改图失败或没进缓存")
            ok = False
        else:
            (OUT / "e2e_step2.png").write_bytes(images[0])
            same = images[0] == first
            print(f"    ✓ 已按缓存改图并进缓存 -> /tmp/e2e_step2.png")
            print(f"      与原图字节相同? {same}  (必须为 False，否则说明没真的改)")
            if same:
                ok = False

        # ---------- 第三次：指定第 2 张（也就是最早那张）再改 ----------
        print("=" * 64)
        print("[3] 指定引用：「把第2张改成水彩画风格」")
        images.clear()
        sent.clear()
        await responder.answer(session, "把第2张改成水彩画风格", sender, image_sender)
        print(f"    调用: {[(s[0], s[2][:24]) for s in sent]}")
        print(f"    缓存: {responder.cache.count(session)} 张 | {responder.cache.describe(session)}")
        if len(images) != 1 or responder.cache.count(session) != 3:
            print("    ✗ 指定引用改图失败")
            ok = False
        else:
            (OUT / "e2e_step3.png").write_bytes(images[0])
            print("    ✓ 按「第2张」改图成功 -> /tmp/e2e_step3.png")

        # ---------- 缓存上限 ----------
        print("=" * 64)
        print(f"[4] 缓存上限（配置 {cfg.image_cache_size}）")
        for i in range(int(cfg.image_cache_size) + 2):
            responder.cache.add(session, b"x", f"填充{i}")
        print(f"    count={responder.cache.count(session)}（应等于上限）")
        if responder.cache.count(session) != int(cfg.image_cache_size):
            print("    ✗ 上限没生效")
            ok = False
        else:
            print("    ✓ 超出上限的最旧图被挤掉")
    finally:
        await model.aclose()

    print("=" * 64)
    print("结论: " + ("✓ 出图→缓存→按缓存改图 全链路正常" if ok else "✗ 有环节失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
