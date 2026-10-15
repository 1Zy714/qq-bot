#!/usr/bin/env python3
"""官方开放平台路线的离线自测。

不需要连腾讯、不需要真实群：用假的 sender / image_sender 捕获调用，
并 monkeypatch 掉底层 http，检查富媒体上传的路由与 payload 是否正确。

会真实出图（约 70 秒，需要 ComfyUI 在跑），但不碰聊天模型。

用法::
    ~/.venvs/qqbot/bin/python test_official_image.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import official_bot as ob
from bot import Config, PersonaStore

HERE = Path(__file__).resolve().parent


async def test_upload_payload() -> bool:
    """校验 _upload_image 的路由与 payload 形状。"""
    captured: dict = {}

    class FakeHTTP:
        @staticmethod
        async def request(route, json=None):
            captured["url"] = route.url
            captured["payload"] = json
            return {"file_info": "FAKE_FILE_INFO", "file_uuid": "u1", "ttl": 0}

    class FakeAPI:
        _http = FakeHTTP()

    class FakeSelf:
        api = FakeAPI()

    info = await ob.QQBotClient._upload_image(
        FakeSelf(), {"group_openid": "G1"}, "/v2/groups/{group_openid}/files", b"\xff\xd8\xffJPEGDATA"
    )
    ok = True
    print(f"  上传 URL : {captured['url']}")
    payload = captured["payload"]
    print(f"  file_type: {payload['file_type']}  srv_send_msg: {payload['srv_send_msg']}")
    print(f"  file_data 是 base64: {payload['file_data'][:24]}…")
    if not captured["url"].endswith("/v2/groups/G1/files"):
        print("  ✗ 路由不对")
        ok = False
    if payload["file_type"] != 1 or payload["srv_send_msg"] is not False:
        print("  ✗ file_type/srv_send_msg 不对")
        ok = False
    # base64(b"\xff\xd8\xffJPEGDATA") == "/9j/SlBFR0RBVEE="
    if payload["file_data"] != "/9j/SlBFR0RBVEE=":
        print(f"  ✗ file_data 不是预期的 base64: {payload['file_data']}")
        ok = False
    if info != "FAKE_FILE_INFO":
        print("  ✗ file_info 解析失败")
        ok = False
    print("  ✓ 上传参数正确" if ok else "  ✗ 上传参数有问题")
    return ok


async def test_responder() -> bool:
    cfg = Config.load(HERE / "config.json")
    personas = PersonaStore(cfg, HERE / "persona.state")
    model = ob.LocalModel(cfg, personas)
    responder = ob.Responder(model, cfg, personas)

    calls: list[tuple] = []

    async def sender(text: str, seq: int) -> None:
        calls.append(("text", seq, text))

    async def image_sender(data: bytes, seq: int) -> None:
        calls.append(("image", seq, len(data)))
        Path("/tmp/official_e2e.jpg").write_bytes(data)

    ok = True

    print("[a] 空提示词只回用法，不出图")
    calls.clear()
    await responder.answer("c2c:t", "/画", sender, image_sender)
    print(f"    调用: {[(c[0], c[1]) for c in calls]}")
    if [c[0] for c in calls] != ["text"] or "用法" not in calls[0][2]:
        print("    ✗ 期望只回用法")
        ok = False

    print("[b] 人格指令优先于出图")
    calls.clear()
    await responder.answer("c2c:t", "人格 小皮", sender, image_sender)
    print(f"    调用: {[(c[0], c[1]) for c in calls]}")
    if [c[0] for c in calls] != ["text"]:
        print("    ✗ 人格指令应当只回文本")
        ok = False

    print("[c] 正常出图（真实调用 ComfyUI，约 70 秒）")
    calls.clear()
    await responder.answer(
        "c2c:t", "/画 一只戴着红围巾的小龙，在雨夜里举着灯笼", sender, image_sender
    )
    for kind, seq, val in calls:
        print(f"    {kind} seq={seq} " + (f"{val[:40]}" if kind == "text" else f"{val:,} 字节"))
    kinds = [c[0] for c in calls]
    if kinds != ["text", "image"]:
        print(f"    ✗ 期望先文本后图片，实际 {kinds}")
        ok = False
    elif not responder.comfy_dirty:
        print("    ✗ comfy_dirty 应当被置位")
        ok = False
    else:
        print("    ✓ 先回提示语、再发图，comfy_dirty 已置位")

    await model.aclose()
    return ok


async def main() -> int:
    print("=" * 62)
    print("1) 富媒体上传参数")
    ok1 = await test_upload_payload()
    print("=" * 62)
    print("2) Responder 出图流程")
    ok2 = await test_responder()
    print("=" * 62)
    print("结论: " + ("✓ 官方路线出图接入正常" if (ok1 and ok2) else "✗ 有问题，见上面"))
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
