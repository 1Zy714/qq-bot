#!/usr/bin/env python3
"""消息去重 + 发送熔断。

为什么需要（2026-10-17 实测）：
QQ 官方平台会**重推同一条消息** —— 当晚日志里同一张表情包被收进库两次，
群里也可能因此被回复两遍。这里用消息 id 做时间窗去重；再给发送路径加一层轻量熔断，
避免发送持续失败时一直撞墙刷日志。

思路参考 linxisama/deepseekgirl 的 security.py（令牌桶 + 三态熔断），
但按本项目的实际需要做了简化：不需要令牌桶（频率已由 auto_chat 的概率/冷却管），
只保留「去重」和「连续失败就暂停」两件事。
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

# 去重窗口：同一条消息在这个时间内再次出现，视为平台重推
DEFAULT_TTL_SECONDS = 300.0
DEFAULT_MAXSIZE = 512


class MessageDedup:
    """同一条消息（平台重推）只处理一次。

    用「消息 id」做键最可靠；拿不到 id 时退回「发送者 + 内容」的指纹。
    缓存按时间窗与容量上限双重清理，不会无限增长。
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._ttl = float(ttl)
        self._maxsize = int(maxsize)
        self._seen: OrderedDict[str, float] = OrderedDict()

    @staticmethod
    def key_of(message: object, content: str = "") -> str:
        """算一条消息的去重键；没有 id 时用内容指纹兜底。"""
        mid = getattr(message, "id", None)
        if mid:
            return f"id:{mid}"
        author = getattr(message, "author", None)
        who = (
            getattr(author, "member_openid", "")
            or getattr(author, "user_openid", "")
            or ""
        )
        raw = f"{who}|{content}"
        return "fp:" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()

    def seen(self, key: str) -> bool:
        """返回 True 表示这条之前处理过（调用方应当跳过）。"""
        now = time.monotonic()
        self._evict(now)
        if key in self._seen:
            self._seen.move_to_end(key)
            return True
        self._seen[key] = now
        while len(self._seen) > self._maxsize:
            self._seen.popitem(last=False)
        return False

    def _evict(self, now: float) -> None:
        while self._seen:
            _, when = next(iter(self._seen.items()))
            if now - when > self._ttl:
                self._seen.popitem(last=False)
            else:
                break

    def __len__(self) -> int:
        return len(self._seen)


class SendGuard:
    """发送熔断：连续失败到阈值就先停一会儿，别一直撞墙。

    没有完整的三态机：冷却时间一到就放行（相当于半开试探），一旦成功即清零。
    """

    def __init__(self, threshold: int = 5, cooldown: float = 60.0) -> None:
        self._threshold = max(1, int(threshold))
        self._cooldown = max(1.0, float(cooldown))
        self._failures = 0
        self._opened_at = 0.0

    @property
    def open(self) -> bool:
        """熔断是否生效（生效期间调用方应跳过发送）。"""
        if self._failures < self._threshold:
            return False
        # 冷却结束 → 放行一次试探
        return (time.monotonic() - self._opened_at) < self._cooldown

    def allow(self) -> bool:
        return not self.open

    def ok(self) -> None:
        self._failures = 0

    def fail(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()

    @property
    def failures(self) -> int:
        return self._failures
