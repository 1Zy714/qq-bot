#!/usr/bin/env python3
"""离线单测记忆检索：标记解析、匹配、时间衰减、注入格式。

不依赖网络与模型，临时目录里造三份数据源即可。

用法::

    python3 test_memory_search.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_search as ms  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


def build(root: Path) -> None:
    now = time.time()
    (root / "users.json").write_text(json.dumps({
        "users": {
            "u1": {"name": "e", "count": 137, "last_seen": now,
                   "notes": ["爱问机器人底层怎么跑", "爱拿大肥鱼测token用量"]},
            "u2": {"name": "小明", "count": 9, "last_seen": now - 86400 * 40,
                   "notes": ["住在杭州", "养了一只橘猫"]},
        }
    }, ensure_ascii=False), "utf-8")

    lines = [
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "text": "反思：学会了先问清再动手"},
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 86400 * 60)), "text": "旧的反思：不该抢话"},
    ]
    (root / "growth.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n", "utf-8")

    diary = root / "diary"
    diary.mkdir()
    (diary / "2026-10-27.jsonl").write_text(json.dumps({
        "ts": now - 3600, "name": "小明", "text": "上次那个崩溃是存档序列化的问题"
    }, ensure_ascii=False) + "\n", "utf-8")


def test_marker() -> None:
    print("\n[1] 标记解析")
    text, query = ms.parse_recall("帮我 [回忆:存档崩溃] 看看")
    check("摘出关键词", query, "存档崩溃")
    check("标记从正文里去掉", "回忆" in text, False)
    check("全角冒号也认", ms.parse_recall("【回忆：杭州】")[1], "杭州")
    check("没有标记原样返回", ms.parse_recall("普通消息"), ("普通消息", ""))


def test_auto_query() -> None:
    print("\n[1.5] 自动触发关键词抽取")
    check("上次类问题会触发", bool(ms.auto_query("上次那个存档崩溃是怎么修的")), True)
    check("抽到实词", "存档" in ms.auto_query("上次那个存档崩溃是怎么修的"), True)
    check("记得类问题会触发", bool(ms.auto_query("你还记得我养的是什么猫吗")), True)
    check("普通闲聊不触发", ms.auto_query("今天天气不错"), "")
    check("太短的线索不触发", ms.auto_query("之前"), "")
    check("没有线索词不触发", ms.auto_query("帮我写一段代码"), "")


def test_min_score(root: Path) -> None:
    print("\n[4.5] 自动触发的最低分门槛")
    engine = ms.MemorySearch(root)
    # 只命中一个二元组、且内容弱相关：低门槛给，高门槛不给
    weak = "机器人"
    check("低门槛会注入", bool(engine.digest(weak, min_score=0.0)), True)
    check("高门槛会过滤", engine.digest(weak, min_score=5.0), "")


def test_search(root: Path) -> None:
    print("\n[2] 检索")
    engine = ms.MemorySearch(root)
    hits = engine.search("杭州")
    check("按内容命中", any("杭州" in h.text for h in hits), True)
    check("来源标注正确", hits[0].source, "人物")
    check("无关词不命中", engine.search("火星基地"), [])

    hits = engine.search("存档 崩溃")
    check("多词交集命中", any("存档" in h.text for h in hits), True)
    check("日记来源可检索", any(h.source == "日记" for h in hits), True)

    hits = engine.search("大肥鱼 token")
    check("英文词也参与", any("token" in h.text.lower() for h in hits), True)


def test_decay(root: Path) -> None:
    print("\n[3] 时间衰减")
    engine = ms.MemorySearch(root)
    # 「反思」只有成长附录命中：一条今天、一条 60 天前
    hits = engine.search("反思")
    check("两条都命中", len(hits) >= 2, True)
    check("新的排在前面", "先问清" in hits[0].text, True)


def test_digest(root: Path) -> None:
    print("\n[4] 注入格式")
    engine = ms.MemorySearch(root)
    text = engine.digest("杭州", max_chars=200)
    check("带证据标题", "记忆检索结果" in text, True)
    check("带来源与时间", "人物·" in text, True)
    check("不超过预算", len(text) <= 200, True)
    check("没命中返回空串", engine.digest("完全不相关的话题"), "")

    many = engine.digest("机器人", max_chars=30)
    check("预算极小时也不会超", len(many) <= 30, True)


def main() -> int:
    print("=" * 56)
    print("记忆检索 离线单测")
    test_marker()
    test_auto_query()
    root = Path(tempfile.mkdtemp(prefix="memsearch_"))
    try:
        build(root)
        test_search(root)
        test_decay(root)
        test_digest(root)
        test_min_score(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
