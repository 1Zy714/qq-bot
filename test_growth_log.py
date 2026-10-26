#!/usr/bin/env python3
"""离线单测「成长附录」：只追加 + 哈希链 + 三重限流。

``growth_log`` 只碰文件系统（临时目录），不连网关、不跑模型。

重点覆盖三件错了很难发现的事：

1. **「只准追加」是真机制**：手工改一行历史，必须被检出「链断」，
   并且断链之后**拒绝继续写**（否则断点会被越埋越深）。
2. **限流三层都在**：单条字数 / 每天条数 / 最小间隔，少一层模型就会刷流水账。
3. **标记不外发**：``[成长:...]`` 必须从回复里摘干净，半截标记不能发进群。

用法::

    python3 test_growth_log.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import growth_log as gl

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


def new_log(root: Path, **kw: object) -> gl.GrowthLog:
    kw.setdefault("min_interval", 0.0)
    return gl.GrowthLog(root / "growth.jsonl", **kw)


def test_marker() -> None:
    print("\n[1] 标记解析")
    text, items = gl.parse_growth_marker("想通了。[成长:不该替群友编造他说过的话]")
    check("正文摘干净", text, "想通了。")
    check("记下一条", items, ["不该替群友编造他说过的话"])

    text, items = gl.parse_growth_marker("【成长：被纠正了，先问再答】好")
    check("全角标记也认", items, ["被纠正了，先问再答"])
    check("正文保留", text.strip(), "好")

    text, items = gl.parse_growth_marker("一条[成长:甲]两条[成长:乙]")
    check("一行多条都收", items, ["甲", "乙"])
    check("多条标记都摘", text.strip(), "一条两条")

    _, items = gl.parse_growth_marker("我成长了很多")
    check("不带冒号不算标记", items, [])


def test_append_and_limits(root: Path) -> None:
    print("\n[2] 追加与三重限流")
    log = new_log(root)
    t0 = time.mktime(time.strptime("2026-10-26 10:00:00", gl.TS_FORMAT))

    ok, reason = log.append("想通了：先问再做", now=t0)
    check("正常写入", (ok, reason), (True, "written"))
    check("落盘一行", len((root / "growth.jsonl").read_text("utf-8").splitlines()), 1)

    ok, reason = log.append("想通了：先问再做", now=t0)
    check("重复内容拒绝", (ok, reason), (False, "duplicate"))

    ok, reason = log.append("", now=t0)
    check("空内容拒绝", (ok, reason), (False, "empty"))

    log_short = gl.GrowthLog(root / "short.jsonl", max_chars=10, min_interval=0.0)
    ok, reason = log_short.append("这句话超过了十个字符的长度限制", now=t0)
    check("超长拒绝", ok, False)
    check("超长原因带字数", reason.startswith("too_long:"), True)

    log_gap = gl.GrowthLog(root / "gap.jsonl", min_interval=1800.0)
    log_gap.append("第一条", now=t0)
    ok, reason = log_gap.append("第二条", now=t0 + 10)
    check("间隔太近拒绝", (ok, reason), (False, "too_soon"))
    ok, _ = log_gap.append("第二条", now=t0 + 3600)
    check("过了间隔可以写", ok, True)

    log_quota = gl.GrowthLog(root / "quota.jsonl", max_per_day=2, min_interval=0.0)
    log_quota.append("一", now=t0)
    log_quota.append("二", now=t0 + 60)
    ok, reason = log_quota.append("三", now=t0 + 120)
    check("每天条数上限", (ok, reason), (False, "daily_quota"))
    ok, _ = log_quota.append("三", now=t0 + 86400)
    check("第二天可以继续", ok, True)


def test_chain(root: Path) -> None:
    print("\n[3] 哈希链")
    log = new_log(root)
    t0 = time.mktime(time.strptime("2026-10-26 11:00:00", gl.TS_FORMAT))
    for i in range(3):
        log.append(f"第 {i} 条反思", now=t0 + i * 60)

    ok, total = log.verify()
    check("链完好", (ok, total), (True, 3))

    path = root / "growth.jsonl"
    lines = path.read_text("utf-8").splitlines()
    record = json.loads(lines[1])
    check("每条都有 prev 和 hash", bool(record.get("prev")) and bool(record.get("hash")), True)

    # 手工改历史：把第二条的内容换掉，哈希就再也对不上
    tampered = dict(record)
    tampered["text"] = "被偷偷改过的内容"
    lines[1] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", "utf-8")

    ok, total = log.verify()
    check("改历史被检出", ok, False)
    check("断点在第 2 条", log.is_broken(), 2)
    check("断链后停止注入", "⚠️链断" in log.digest(), True)
    ok, reason = log.append("断链后还想写", now=t0 + 3600)
    check("断链后拒绝写入", (ok, reason), (False, "chain_broken:line2"))


def test_digest(root: Path) -> None:
    print("\n[4] 注入内容")
    log = new_log(root)
    t0 = time.mktime(time.strptime("2026-10-26 12:00:00", gl.TS_FORMAT))
    check("空文件不注入", log.digest(), "")
    log.append("第一次想通：别抢话", now=t0)
    text = log.digest()
    check("带标题", "只准往后写" in text, True)
    check("带日期", "2026-10-26 12:00" in text, True)
    check("带内容", "别抢话" in text, True)
    check("提醒别写流水账", "流水账" in text, True)

    for i in range(1, 9):
        gl_path = root / "many.jsonl"
        if i == 1:
            log_many = gl.GrowthLog(gl_path, min_interval=0.0)
        log_many.append(f"反思{i}", now=t0 + i * 60)
    digest = log_many.digest(count=6)
    check("最多注入 6 条", digest.count("- 2026"), 6)
    check("只注入最近的", "反思8" in digest and "反思1" not in digest, True)

    describe = log_many.describe(3)
    check("describe 只给 3 条", describe.count("反思"), 3)


def test_persistence(root: Path) -> None:
    print("\n[5] 重启后还在")
    log = new_log(root)
    t0 = time.mktime(time.strptime("2026-10-26 13:00:00", gl.TS_FORMAT))
    log.append("重启也要记得", now=t0)
    again = gl.GrowthLog(root / "growth.jsonl", min_interval=0.0)
    check("新实例读得到", "重启也要记得" in again.digest(), True)
    ok, total = again.verify()
    check("链也没坏", (ok, total), (True, 1))


def test_cli(root: Path) -> None:
    print("\n[6] 命令行自检入口")
    # main() 固定读项目默认路径，单测里只验证入口存在且可调用，不真的去写仓库目录
    check("main 可调用", callable(gl.main), True)
    check("parse_growth_marker 暴露给主程序", callable(gl.parse_growth_marker), True)


def main() -> int:
    print("=" * 62)
    print("成长附录 离线单测")

    test_marker()
    root = Path(tempfile.mkdtemp(prefix="growth_test_"))
    try:
        # 每个用例独立目录 + 独立 jsonl：链校验是全局的，共享文件会互相污染
        test_append_and_limits(root / "limits")
        test_chain(root / "chain")
        test_digest(root / "digest")
        test_persistence(root / "persist")
        test_cli(root / "cli")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
