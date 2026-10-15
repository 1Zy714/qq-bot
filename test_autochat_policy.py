#!/usr/bin/env python3
"""离线单测「自动聊天」的决策层。

``AutoChatPolicy`` / ``AutoChatState`` 是刻意设计成纯函数 + 纯内存账本的：
不连腾讯网关、不碰模型、不起事件循环。所以这个测试**瞬间跑完**，
可以放心在改任何阈值前后跑一遍。

用法::

    ~/.venvs/qqbot/bin/python test_autochat_policy.py

对应方案：``docs/AUTOCHAT.md``（形态① 群内接话 / 形态② 定时主动发言）。
"""

from __future__ import annotations

from datetime import datetime

import official_bot as ob

NOON = datetime(2026, 10, 15, 12, 0)      # 白天，不在静默时段
MIDNIGHT = datetime(2026, 10, 16, 1, 0)   # 凌晨，落在 23:00–08:00 里

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    """和既有测试脚本一致：手写断言，失败只记账不中断，最后一起报。"""
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


def policy(**overrides: object) -> ob.AutoChatPolicy:
    """默认造一个「已开启、白名单只有 G1、纯关键词触发」的策略。"""
    block: dict[str, object] = {
        "enabled": True,
        "dry_run": True,
        "allowed_groups": ["G1"],
        "keywords": ["小助手"],
        "probability": 0.0,   # 默认关掉概率层，让判定结果确定
        "cooldown_seconds": 60,
        "quiet_hours": ["23:00", "08:00"],
    }
    block.update(overrides)
    return ob.AutoChatPolicy(block, rng=lambda: 0.99)


def test_defaults_are_off() -> None:
    print("\n[1] 默认必须全关（不能因为漏配就自己开麦）")
    p = ob.AutoChatPolicy(None)
    check("enabled 默认 False", p.enabled, False)
    check("dry_run 默认 True", p.dry_run, True)
    check("未配置时判定被拒", p.decide(group="G1", text="小助手在吗").reason, "disabled")
    check("空白名单", ob.AutoChatPolicy({"enabled": True}).decide(
        group="G1", text="你好").reason, "no_group_whitelisted")


def test_gates() -> None:
    print("\n[2] 闸门层：白名单 / 静默时段 / 冷却 / 额度 / 长度")
    check("白名单外的群不接", policy().decide(
        group="G9", text="小助手在吗").reason, "group_not_allowed")
    check("静默时段不接", policy().decide(
        group="G1", text="小助手在吗", now=MIDNIGHT).reason, "quiet_hours")
    check("冷却中不接", policy().decide(
        group="G1", text="小助手在吗", seconds_since_last=10, now=NOON).reason, "cooldown")
    check("冷却过了就放行", policy().decide(
        group="G1", text="小助手在吗", seconds_since_last=61, now=NOON).allow, True)
    check("额度用完不接", policy().decide(
        group="G1", text="小助手在吗", today_count=30, now=NOON).reason, "daily_limit")
    check("字数不足不接", policy().decide(group="G1", text="嗯", now=NOON).reason, "too_short")
    check("超长不接", policy(max_chars=10).decide(
        group="G1", text="小助手" + "长" * 20, now=NOON).reason, "too_long")
    check("proactive 模式下不开接话", policy(reply_mode="proactive").decide(
        group="G1", text="小助手在吗").reason, "reply_mode_proactive_only")


def test_layers() -> None:
    print("\n[3] 必答层与概率层")
    check("@ 命中必答", policy().decide(
        group="G1", text="在吗", mentioned=True, now=NOON).layer, "must")
    check("@ 必答可关", policy(reply_when_mentioned=False).decide(
        group="G1", text="好无聊啊", mentioned=True, now=NOON).allow, False)
    check("关键词命中", policy().decide(
        group="G1", text="小助手帮我看看", now=NOON).reason, "keyword:小助手")
    check("问号默认不接", policy().decide(
        group="G1", text="这个怎么办呢？", now=NOON).allow, False)
    check("问号开关打开则接", policy(reply_when_question=True).decide(
        group="G1", text="这个怎么办呢？", now=NOON).reason, "question")
    check("都没命中就是不接", policy().decide(
        group="G1", text="今天天气不错", now=NOON).reason, "no_trigger")


def test_probability() -> None:
    print("\n[4] 概率层：注入随机数，结果必须确定")
    lucky = ob.AutoChatPolicy(
        {"enabled": True, "allowed_groups": ["G1"], "probability": 0.5,
         "keywords": [], "quiet_hours": []},
        rng=lambda: 0.1)
    check("骰子过线就接", lucky.decide(group="G1", text="随便说点什么", now=NOON).reason,
          "probability")
    unlucky = ob.AutoChatPolicy(
        {"enabled": True, "allowed_groups": ["G1"], "probability": 0.5,
         "keywords": [], "quiet_hours": []},
        rng=lambda: 0.9)
    check("骰子没过就不接", unlucky.decide(group="G1", text="随便说点什么", now=NOON).reason,
          "no_trigger")


def test_quiet_hours() -> None:
    print("\n[5] 静默时段解析（含跨零点 / 写错不炸）")
    check("23:00 跨零点命中凌晨", policy().in_quiet_hours(MIDNIGHT), True)
    check("23:00 跨零点白天不命中", policy().in_quiet_hours(NOON), False)
    check("白天窗口命中", policy(quiet_hours=["09:00", "18:00"]).in_quiet_hours(NOON), True)
    check("空窗口 = 不静默", policy(quiet_hours=[]).in_quiet_hours(NOON), False)
    check("两头相同视为关闭", policy(quiet_hours=["08:00", "08:00"]).in_quiet_hours(NOON), False)
    check("垃圾值不炸", policy(quiet_hours=["晚上", "早上"]).in_quiet_hours(NOON), False)
    check("HH:MM 解析", ob.minutes_of_day("23:00"), 1380)
    check("非法时间返回 None", ob.minutes_of_day("25:99"), None)


def test_bad_config() -> None:
    print("\n[6] 手写 JSON 常见的类型错误不能把机器人搞崩")
    weird = ob.AutoChatPolicy({
        "enabled": True, "allowed_groups": "G1",          # 该是 list 却写成 str
        "probability": "高",                              # 该是数字却写成中文
        "cooldown_seconds": None, "daily_limit_per_group": "30",
        "keywords": [None, "", "小助手"], "schedule": ["09:00", "乱写", 42],
    })
    check("字符串白名单不会逐字符匹配", weird.allowed_groups, [])
    check("非法概率回落到默认 0.1", weird.float_setting("probability", 0.1), 0.1)
    check("None 冷却回落到默认", weird.float_setting("cooldown_seconds", 90.0), 90.0)
    check("字符串数字能读成 int", weird.int_setting("daily_limit_per_group", 0), 30)
    check("关键词里的空值被清掉", weird.keywords, ["小助手"])
    check("schedule 只留合法项", weird.schedule, ["09:00"])


def test_state() -> None:
    print("\n[7] 账本：冷却 / 当日额度 / 主动消息白名单")
    st = ob.AutoChatState()
    check("没记过账=无限久以前", st.seconds_since_last("G1"), float("inf"))
    check("没记过账=今日 0 条", st.today_count("G1"), 0)
    st.note_reply("G1")
    check("记一笔后今日 1 条", st.today_count("G1"), 1)
    check("记一笔后冷却开始计时", st.seconds_since_last("G1") < 5, True)
    st.note_reply("G1")
    check("再记一笔 = 2 条", st.today_count("G1"), 2)
    check("别的群不受影响", st.today_count("G2"), 0)
    check("未知群默认允许主动发言", st.proactive_ok("G1"), True)
    st.note_proactive("G1", False)
    check("被群主关掉后不再主动发", st.proactive_ok("G1"), False)


def main() -> int:
    print("=" * 62)
    print("自动聊天决策层单测（纯离线，不连网关、不跑模型）")
    for func in (
        test_defaults_are_off,
        test_gates,
        test_layers,
        test_probability,
        test_quiet_hours,
        test_bad_config,
        test_state,
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
    raise SystemExit(main())
