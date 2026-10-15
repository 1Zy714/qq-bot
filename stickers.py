#!/usr/bin/env python3
"""表情包库管理（命令行）。

为什么需要它：自动收集来的表情包**没有标签**（本机没有视觉模型去做图像理解），
所以按标签发送的关键词永远是空的。要按「开心」「无语」这类语义发送，就得有个人
给它们打标签——这个工具就是干这个的。

用法::

    python3 stickers.py list              # 列出现有表情包（带序号）
    python3 stickers.py tag 1 开心 猫     # 给第 1 张打上标签
    python3 stickers.py tag --all 群表情  # 给全部打同一个标签
    python3 stickers.py untag 1 猫        # 去掉标签
    python3 stickers.py stats             # 看库存与占用
    python3 stickers.py rm 1              # 删除一张

序号按 ``list`` 的输出为准。库为空时先让机器人在群里收几张。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sticker_lib import DEFAULT_QUOTA_BYTES, StickerLibrary  # noqa: E402


def resolve_root() -> Path:
    """优先用 config.json 里的 sticker_dir，缺省用 qq-bot/stickers。"""
    cfg_path = HERE / "config.json"
    config: dict = {}
    if cfg_path.exists():
        try:
            import json

            config = json.loads(cfg_path.read_text("utf-8"))
        except (OSError, ValueError):
            config = {}
    configured = str(config.get("sticker_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return HERE / "stickers"


def human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"


def cmd_list(lib: StickerLibrary, args: argparse.Namespace) -> int:
    items = list(lib._items.values())
    if not items:
        print(f"表情包库是空的: {lib.files_dir}")
        print("让机器人待在群里，别人发表情包时会自动收进来（gif 必收，png/jpg 仅限小图）。")
        return 0

    items.sort(key=lambda s: s.added_at)
    print(f"共 {len(items)} 张  目录 {lib.files_dir}")
    print()
    print(f"{'#':>3}  {'尺寸':>11}  {'像素':>11}  {'标签':<20} 文件")
    print("-" * 78)
    for i, s in enumerate(items, start=1):
        px = f"{s.width}x{s.height}" if s.width and s.height else "-"
        tags = ",".join(s.tags) if s.tags else "(无)"
        print(f"{i:>3}  {human_size(s.size):>11}  {px:>11}  {tags:<20} {s.filename[:20]}…")
    print()
    print("打标签: python3 stickers.py tag <序号> <标签...>")
    return 0


def cmd_tag(lib: StickerLibrary, args: argparse.Namespace) -> int:
    items = list(lib._items.values())
    if not items:
        print("库是空的，没有可以打标签的对象。", file=sys.stderr)
        return 1

    if args.all:
        n = lib.add_tags(args.tags)
        print(f"给全部 {len(items)} 张加了标签 {args.tags}（实际更新 {n} 张）")
        return 0

    if args.index is None:
        print("需要序号，或用 --all。先跑 list 看序号。", file=sys.stderr)
        return 2
    if not (1 <= args.index <= len(items)):
        print(f"序号超范围：1~{len(items)}", file=sys.stderr)
        return 2

    target = items[args.index - 1]
    n = lib.add_tags(args.tags, sha1=target.sha1)
    print(f"第 {args.index} 张 -> 标签 {target.tags}（更新 {n} 张）")
    return 0


def cmd_untag(lib: StickerLibrary, args: argparse.Namespace) -> int:
    items = list(lib._items.values())
    if not (1 <= (args.index or 0) <= len(items)):
        print(f"序号超范围：1~{len(items) or 0}", file=sys.stderr)
        return 2
    target = items[args.index - 1]
    remove = {t.strip() for t in args.tags}
    before = list(target.tags)
    target.tags = [t for t in target.tags if t not in remove]
    lib.save()
    print(f"第 {args.index} 张: {before} -> {target.tags}")
    return 0


def cmd_rm(lib: StickerLibrary, args: argparse.Namespace) -> int:
    items = list(lib._items.values())
    if not (1 <= (args.index or 0) <= len(items)):
        print(f"序号超范围：1~{len(items) or 0}", file=sys.stderr)
        return 2
    target = items[args.index - 1]
    path = lib.files_dir / target.filename
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"删除文件失败: {exc}", file=sys.stderr)
        return 1
    lib._items.pop(target.sha1, None)
    lib.save()
    print(f"已删除第 {args.index} 张: {target.filename}")
    return 0


def cmd_stats(lib: StickerLibrary, args: argparse.Namespace) -> int:
    total = lib.total_bytes()
    tags = lib.all_tags()
    print(f"目录      : {lib.files_dir}")
    print(f"索引      : {lib.index_path}")
    print(f"库存      : {len(lib)} 张")
    print(f"占用      : {human_size(total)} / {human_size(lib.quota_bytes)}")
    print(f"标签      : {', '.join(tags) if tags else '(还没有标签，用 tag 子命令添加)'}")
    print()
    print("发送方式  : 模型在回复里写 [表情:标签]，按标签精确匹配（不再随机发）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="表情包库管理", formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出现有表情包")

    p_tag = sub.add_parser("tag", help="给表情包打标签")
    p_tag.add_argument("index", nargs="?", type=int, help="序号（list 里看到的）")
    p_tag.add_argument("tags", nargs="+", help="标签，可多个")
    p_tag.add_argument("--all", action="store_true", help="给全部表情包打同一批标签")

    p_un = sub.add_parser("untag", help="去掉标签")
    p_un.add_argument("index", type=int)
    p_un.add_argument("tags", nargs="+")

    p_rm = sub.add_parser("rm", help="删除一张")
    p_rm.add_argument("index", type=int)

    sub.add_parser("stats", help="看库存与占用")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 0

    lib = StickerLibrary(resolve_root(), quota_bytes=DEFAULT_QUOTA_BYTES)
    handlers = {
        "list": cmd_list, "tag": cmd_tag, "untag": cmd_untag,
        "rm": cmd_rm, "stats": cmd_stats,
    }
    return handlers[args.cmd](lib, args)


if __name__ == "__main__":
    raise SystemExit(main())
