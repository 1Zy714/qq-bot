#!/usr/bin/env python3
"""离线单测：发言时机闸门、消息表情解析、表情包标签学习。

和 ``test_autochat_policy.py`` 一样，全部是纯逻辑，不连腾讯网关、不跑模型，
秒级跑完。改阈值/改评分公式前后都应该跑一遍：

    ~/.venvs/qqbot/bin/python test_speech_gate.py
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import official_bot as ob
from sticker_lib import StickerLibrary, infer_tags_from_text

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


def gate_policy(**gate_overrides: object) -> ob.AutoChatPolicy:
    block: dict[str, object] = {
        "enabled": True,
        "allowed_groups": ["G1"],
        "keywords": [],
        "probability": 1.0,      # 概率门全开，只看闸门
        "cooldown_seconds": 0,
        "min_chars": 1,
        "quiet_hours": [],
        "gate": {"enabled": True, "min_score": 0.55, "hard_cooldown_seconds": 8,
                 "max_per_hour": 12, **gate_overrides},
    }
    return ob.AutoChatPolicy(block, rng=lambda: 0.99)


def test_face_and_image_text() -> None:
    print("\n[1] 消息文本：QQ 表情要变成人话，纯图片要有占位")
    check("faceId 有名", ob.face_text("哈哈<faceType=6,faceId=0>"),
          "哈哈（对方发了个「微笑」表情）")
    check("未知 faceId 兜底", ob.face_text("<faceType=9,faceId=999>"), "（对方发了个表情）")
    check("多个表情", ob.face_text("<faceType=6,faceId=4>x<faceType=6,faceId=5>"),
          "（对方发了个「得意」表情）x（对方发了个「流泪」表情）")
    check("带引号/带 ext 的真实格式",
          ob.face_text('<faceType=6,faceId="0",ext="eyJ0ZXh0Ijoi">'),
          "（对方发了个「微笑」表情）")

    face_msg = SimpleNamespace(content='<faceType=6,faceId="0",ext="e30=">', attachments=[])
    text, has_image = ob.message_text(face_msg)
    check("纯表情有占位", text, "（对方发了个「微笑」表情）")
    check("纯表情算图片信号（会偶尔接一下）", has_image, True)

    image_msg = SimpleNamespace(
        content="", attachments=[SimpleNamespace(content_type="image/gif")]
    )
    text, has_image = ob.message_text(image_msg)
    check("纯表情包有占位", text, "[发了一张表情包/图片]")
    check("纯表情包 has_image", has_image, True)

    text_msg = SimpleNamespace(content="你好<faceType=6,faceId=0>", attachments=[])
    text, has_image = ob.message_text(text_msg)
    check("文字+表情", text, "你好（对方发了个「微笑」表情）")
    check("没图", has_image, False)

    known = SimpleNamespace(content="", attachments=[SimpleNamespace(content_type="image/gif")])
    text, has_image = ob.message_text(known, ["笑"])
    check("认识的图带含义", text, "[发了一张表情包/图片]（表情包含义：笑）")

    known_text = SimpleNamespace(content="哈哈哈", attachments=[SimpleNamespace(content_type="image/gif")])
    text, _ = ob.message_text(known_text, ["笑", "夸赞"])
    check("文字+认识的图", text, "哈哈哈（表情包含义：笑、夸赞）")

    file_msg = SimpleNamespace(
        content="", attachments=[SimpleNamespace(content_type="application/pdf")]
    )
    check("非图片附件不当作图", ob.message_text(file_msg), ("", False))


def test_message_content() -> None:
    print("\n[1b] 正文有效性：括号动作/问号/占位都不算「说话」")
    check("括号动作不算正文", ob.message_has_content("（甩尾）"), False)
    check("只有问号不算正文", ob.message_has_content("？"), False)
    check("图片占位不算正文", ob.message_has_content("[图片]"), False)
    check("短句算正文", ob.message_has_content("就这？"), True)
    check("完整一句算正文", ob.message_has_content("你发的这个我看不懂，说人话。"), True)


def test_gate_off_unchanged() -> None:
    print("\n[2] 闸门关闭时，旧概率行为必须逐字不变")
    p = ob.AutoChatPolicy(
        {"enabled": True, "allowed_groups": ["G1"], "keywords": [],
         "probability": 0.5, "quiet_hours": [], "gate": {"enabled": False}},
        rng=lambda: 0.1,
    )
    check("旧概率层仍生效", p.decide(group="G1", text="随便说点什么").reason, "probability")
    check("gate_enabled=False", p.gate_enabled, False)


def test_gate_signals() -> None:
    print("\n[3] 闸门评分：该说话时给分，插话时扣分")
    p = gate_policy()
    # 普通闲聊：分数不够 → 静默（这正是旧版 80% 概率乱接的反面）
    check("普通闲聊不接", p.decide(group="G1", text="今天天气不错").reason.startswith("gate:low_score"),
          True)
    # 提问加分
    check("提问能过线", p.decide(group="G1", text="这个怎么做？").allow, True)
    # 图片加分
    check("表情包能过线", p.decide(group="G1", text="[发了一张表情包/图片]", has_image=True).allow,
          True)

    quiet = gate_policy()
    ctx = {"recent": [(3.0, "A"), (5.0, "B"), (7.0, "A")], "hourly_count": 0}
    check("多人互聊被扣到静默",
          quiet.decide(group="G1", text="这个怎么做？", gate_ctx=ctx).allow, False)

    repeat = gate_policy()
    ctx = {"recent": [(3.0, "A"), (9.0, "A")], "hourly_count": 0}
    check("同一人连发被扣分",
          repeat.decide(group="G1", text="这个怎么做？", gate_ctx=ctx).allow, False)

    ignored = gate_policy()
    check("上次被无视后更保守",
          ignored.decide(group="G1", text="这个怎么做？",
                         gate_ctx={"ignored_last": True, "hourly_count": 0}).allow, False)


def test_gate_hard_limits() -> None:
    print("\n[4] 闸门硬限制：冷却 / 每小时额度 / @ 必答")
    p = gate_policy()
    check("硬冷却内不接", p.decide(group="G1", text="这个怎么做？",
                                   seconds_since_last=3).reason, "gate:hard_cooldown")
    check("每小时额度用完不接", p.decide(group="G1", text="这个怎么做？",
                                        gate_ctx={"hourly_count": 12}).reason,
          "gate:hourly_limit")
    check("@ 仍然必答（不受闸门影响）", p.decide(
        group="G1", text="<@123> 你好", mentioned=True).layer, "must")

    rng_policy = ob.AutoChatPolicy(
        {"enabled": True, "allowed_groups": ["G1"], "keywords": [],
         "probability": 0.3, "quiet_hours": [], "min_chars": 1,
         "gate": {"enabled": True, "min_score": 0.55, "hard_cooldown_seconds": 0}},
        rng=lambda: 0.9,   # 概率门不过
    )
    check("闸门放行后仍可被概率门拦下",
          rng_policy.decide(group="G1", text="这个怎么做？").reason, "gate_probability")


def test_state_window() -> None:
    print("\n[5] 账本：最近消息窗口 / 每小时计数 / 被无视判定")
    st = ob.AutoChatState()
    st.note_message("G1", "A")
    st.note_message("G1", "B")
    recent = st.recent_messages("G1")
    check("窗口记了 2 条", len(recent), 2)
    check("窗口里是人", {uid for _, uid in recent}, {"A", "B"})
    check("没说过话就不算被无视", st.ignored_last("G1"), False)

    st.note_reply("G1")
    check("刚说完不算被无视（45s 窗口内）", st.ignored_last("G1", within=45.0), False)
    check("发言计入小时额度", st.hourly_count("G1"), 1)
    st.note_message("G1", "C")
    check("有人接话后不算被无视", st.ignored_last("G1", within=45.0), False)
    check("其他群额度独立", st.hourly_count("G2"), 0)


def test_sticker_neighbors(tmp_path=None) -> None:
    print("\n[6] 表情包：文字弱标签 / 情绪近邻 / 同图排除")
    check("从文字猜标签", set(infer_tags_from_text("笑死，绷不住了")), {"笑", "无语"})
    check("无情绪词", infer_tags_from_text("今天干了什么"), [])

    import tempfile
    from pathlib import Path

    root = Path(tempfile.mkdtemp(prefix="sticker-test-"))
    lib = StickerLibrary(root)
    # 直接塞两条记录，避免依赖真实图片字节
    from sticker_lib import Sticker
    gif = b"GIF89a" + b"\x00" * 1200
    a = lib.collect(gif, content_type="image/gif", filename="a.gif")
    b = lib.collect(gif + b"x", content_type="image/gif", filename="b.gif")
    assert a is not None and b is not None
    lib.add_tags(["笑"], sha1=a.sha1)
    lib.add_tags(["生气"], sha1=b.sha1)

    got = lib.pick_by_tag("开心")
    check("同义词命中", got is not None and got.sha1 == a.sha1, True)
    got2 = lib.pick_by_tag("无语", exclude=[a.sha1])
    check("近邻退让命中另一张", got2 is not None and got2.sha1 == b.sha1, True)
    check("近邻确实退让了", lib.last_pick_neighbor, "无语->生气")
    check("排除后没有别的图就返回 None", lib.pick_by_tag("笑", exclude=[a.sha1]), None)


def test_image_reply() -> None:
    print("\n[8] 表情包消息：不跟文字抢冷却，按自己的限频必答")
    base = {
        "enabled": True, "allowed_groups": ["G1"], "keywords": [],
        "probability": 0.0, "cooldown_seconds": 30, "image_reply_cooldown": 20,
        "quiet_hours": [], "min_chars": 1,
        "gate": {"enabled": True, "min_score": 0.55, "hard_cooldown_seconds": 0},
    }
    p = ob.AutoChatPolicy(base, rng=lambda: 0.99)
    # 25s：还在文字的 30s 冷却里，但已过表情自己的 20s 冷却 → 应该必答
    d = p.decide(group="G1", text="（对方发了个「微笑」表情）", has_image=True, seconds_since_last=25)
    check("文字冷却中，纯表情仍必答", (d.allow, d.layer), (True, "must"))
    d = p.decide(group="G1", text="[发了一张表情包/图片]", has_image=True, seconds_since_last=25)
    check("纯图片占位也必答", (d.allow, d.layer), (True, "must"))
    d = p.decide(group="G1", text="（对方发了个「微笑」表情）", has_image=True, seconds_since_last=10)
    check("表情自己的冷却内不重复", d.allow, False)
    d = p.decide(group="G1", text="哈哈哈（表情包含义：笑）", has_image=True,
                 seconds_since_last=5)
    check("文字+图仍受文字冷却", d.reason, "cooldown")


def test_debounce_batch() -> None:
    print("\n[7] 防抖聚合：同一人连发合并，不同人分开")
    import asyncio

    async def run_case():
        client = object.__new__(ob.QQBotClient)
        client._debounce = 0.05
        client._pending = {}
        client._pending_tasks = {}
        seen: list = []

        async def fake_handle(message, batch=None, tags=None):
            seen.append((message, batch, tags))

        client._handle_group_message = fake_handle
        a1 = SimpleNamespace(group_openid="G", author=SimpleNamespace(member_openid="A"))
        a2 = SimpleNamespace(group_openid="G", author=SimpleNamespace(member_openid="A"))
        b1 = SimpleNamespace(group_openid="G", author=SimpleNamespace(member_openid="B"))
        client._queue_group_message(a1, ["笑"])
        client._queue_group_message(a2, [])
        client._queue_group_message(b1, [])
        await asyncio.sleep(0.12)
        return seen

    seen = asyncio.run(run_case())
    check("两个人各自成一队", len(seen), 2)
    merged = [item for item in seen if item[1] and len(item[1]) == 2]
    check("同一人两条被合并", len(merged), 1)
    check("合并保留了每条的标签", merged[0][1][0][1], ["笑"])


def main() -> int:
    print("=" * 62)
    print("发言时机 / 表情识别 / 表情包标签 单测（纯离线）")
    for func in (
        test_face_and_image_text,
        test_message_content,
        test_gate_off_unchanged,
        test_gate_signals,
        test_gate_hard_limits,
        test_state_window,
        test_sticker_neighbors,
        test_image_reply,
        test_debounce_batch,
    ):
        func()
    print("\n" + "=" * 62)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
