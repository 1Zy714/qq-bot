#!/usr/bin/env python3
"""生成几只鲸鱼娘测试表情包（临时工具的产物，不是功能本体）。

为什么要这个：表情包功能上线后库是空的，模型永远不会写 [表情] 标记，
所以没法验证「她发表情包」这条链路。这里用 PIL 画几张，把链路跑通。

正式使用时这些图可以删掉，换成群里收集来的真表情包。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sticker_lib import StickerLibrary  # noqa: E402

FONT_PATH = "/tmp/msyh.ttc"
SIZE = 240

# 鲸鱼娘配色：深蓝发 / 鲸尾 / 蓝白女仆
BG_TOP = (222, 238, 252)
BG_BOTTOM = (186, 216, 245)
WHALE_BODY = (108, 158, 214)
WHALE_BELLY = (240, 248, 255)
WHALE_DARK = (62, 104, 156)
WHALE_EYE = (38, 54, 78)
BLUSH = (255, 170, 180)
TEXT_COLOR = (30, 58, 96)


def rounded_bg() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 竖向渐变背景 + 圆角
    grad = Image.new("RGBA", (SIZE, SIZE))
    gd = ImageDraw.Draw(grad)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        gd.line(
            [(0, y), (SIZE, y)],
            fill=(
                int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
                255,
            ),
        )
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=34, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def draw_whale(d: ImageDraw.ImageDraw, *, mood: str) -> None:
    """画一只小鲸鱼。mood 决定眼睛和嘴的形状。

    鲸鱼整体圈在 y=70..195，底部 195..240 留给文字——第一版让文字压在腹部上，
    看起来像贴纸被人踩了一脚。
    """
    # 尾巴（在左后方）
    d.polygon(
        [(46, 130), (12, 102), (24, 130), (12, 158)],
        fill=WHALE_BODY, outline=WHALE_DARK,
    )
    # 身体
    d.ellipse([48, 80, 202, 180], fill=WHALE_BODY, outline=WHALE_DARK, width=4)
    # 腹部
    d.ellipse([76, 126, 176, 176], fill=WHALE_BELLY, outline=WHALE_DARK, width=3)
    # 头顶喷水
    d.arc([108, 50, 142, 84], start=200, end=340, fill=WHALE_DARK, width=3)
    d.ellipse([120, 42, 130, 52], fill=WHALE_BELLY, outline=WHALE_DARK, width=2)

    # 眼睛（整体上移到 108..134）
    if mood == "happy":
        d.arc([90, 94, 120, 118], start=200, end=340, fill=WHALE_EYE, width=5)
        d.arc([136, 94, 166, 118], start=200, end=340, fill=WHALE_EYE, width=5)
    elif mood == "angry":
        d.line([(90, 96), (118, 108)], fill=WHALE_EYE, width=5)
        d.line([(166, 96), (138, 108)], fill=WHALE_EYE, width=5)
        d.ellipse([98, 110, 112, 124], fill=WHALE_EYE)
        d.ellipse([144, 110, 158, 124], fill=WHALE_EYE)
    else:  # normal
        d.ellipse([96, 100, 116, 122], fill=WHALE_EYE)
        d.ellipse([142, 100, 162, 122], fill=WHALE_EYE)
        d.ellipse([100, 103, 108, 112], fill=(255, 255, 255))
        d.ellipse([146, 103, 154, 112], fill=(255, 255, 255))

    # 腮红
    d.ellipse([82, 126, 104, 138], fill=BLUSH)
    d.ellipse([154, 126, 176, 138], fill=BLUSH)

    # 嘴
    if mood == "happy":
        d.arc([112, 122, 146, 148], start=0, end=180, fill=WHALE_EYE, width=4)
    elif mood == "angry":
        d.arc([112, 138, 146, 160], start=180, end=360, fill=WHALE_EYE, width=4)
    else:
        d.arc([116, 126, 142, 144], start=0, end=180, fill=WHALE_EYE, width=3)


def draw_text(img: Image.Image, text: str) -> None:
    """文字固定贴在底部区域，与鲸鱼不重叠。"""
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 40)
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (SIZE - w) // 2 - bbox[0]
    y = SIZE - h - 12 - bbox[1]
    # 白色描边让文字在任何背景上都清楚
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)):
        d.text((x + dx, y + dy), text, font=font, fill=(255, 255, 255))
    d.text((x, y), text, font=font, fill=TEXT_COLOR)


# (文件名标签, 显示文字, 情绪, 语义标签)
SPECS = [
    ("kaixin", "开心", "happy", ["开心", "高兴", "笑"]),
    ("wuyu", "无语", "normal", ["无语", "冷漠"]),
    ("shengqi", "生气了", "angry", ["生气", "愤怒", "炸毛"]),
    ("shuijiao", "困了", "normal", ["困", "睡觉", "累"]),
    ("zaijian", "再见", "happy", ["再见", "拜拜"]),
    ("haode", "好滴", "happy", ["好的", "同意", "收到"]),
]


def main() -> int:
    lib = StickerLibrary(HERE.parent / "stickers")
    print(f"库目录: {lib.files_dir}")
    print()
    made = 0
    for name, text, mood, tags in SPECS:
        img = rounded_bg()
        draw_whale(ImageDraw.Draw(img), mood=mood)
        draw_text(img, text)

        out = Path("/tmp") / f"whalegirl_{name}.png"
        img.save(out, "PNG")

        st = lib.collect(
            out.read_bytes(), "image/png", filename=out.name,
            width=SIZE, height=SIZE, source="generated",
        )
        if st is None:
            print(f"  ✗ {text}  未收录（被过滤规则拦下？）")
            continue
        lib.add_tags(tags, sha1=st.sha1)
        made += 1
        print(f"  ✓ {text:<6} -> {st.filename[:16]}…  标签={tags}")

    print()
    print(f"生成并入库 {made} 张，库内共 {len(lib)} 张")
    print(f"可用标签: {'、'.join(lib.all_tags())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
