#!/usr/bin/env python3
"""直接驱动星露谷「玩家角色」的客户端（走 MCP，不用键盘模拟）。

为什么不用键盘：实测 SendInput / keybd_event / SendKeys 对这个游戏都不稳定，
而且游戏失焦会暂停。改成让 mod 直接推进 Game1.player.Position，不受焦点影响。

mod 侧提供三个动作（走 stardew_action 通道）：
    move_player(x, y)   走到 tile
    stop_player         停下
    player_use_tool(x, y)  面向该格并用当前工具
"""

from __future__ import annotations

import asyncio
import json

BRIDGE = "/mnt/z/STEAAM/steamapps/common/Stardew Valley/Mods/StardewMCPBridge/bridge_data.json"


def read_state() -> dict | None:
    try:
        with open(BRIDGE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def player_tile() -> tuple[int, int] | None:
    d = read_state()
    if not d:
        return None
    pos = (d.get("player") or {}).get("position") or {}
    try:
        return int(float(pos.get("x", 0)) // 64), int(float(pos.get("y", 0)) // 64)
    except (TypeError, ValueError):
        return None


class PlayerDriver:
    """把「走到某格」封装成一次 await。"""

    def __init__(self, call_tool, poll: float = 0.4) -> None:
        self._call = call_tool
        self.poll = poll

    async def move_to(self, tx: int, ty: int,
                      timeout: float = 20.0, tol: int = 1) -> tuple[bool, str]:
        """走到 tile。返回 (是否到达, 说明)。"""
        start = player_tile()
        await self._call(
            "mcp__stardew__stardew_action",
            {"actionType": "move_player", "x": tx, "y": ty},
        )
        deadline = asyncio.get_event_loop().time() + timeout
        last = start
        stuck = 0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(self.poll)
            cur = player_tile()
            if cur is None:
                return False, "读不到位置"
            if abs(cur[0] - tx) <= tol and abs(cur[1] - ty) <= tol:
                return True, f"到达 ({cur[0]},{cur[1]})"
            if last == cur:
                stuck += 1
                if stuck >= 4:      # 约 1.6 秒没动 = 卡住了
                    if cur != (tx, ty):
                        await self.stop()
                    return False, f"卡在 ({cur[0]},{cur[1]})"
            else:
                stuck = 0
            last = cur
        return False, "超时"

    async def stop(self) -> None:
        await self._call("mcp__stardew__stardew_action",
                         {"actionType": "stop_player", "x": 0, "y": 0})

    async def use_tool(self, tx: int, ty: int) -> None:
        await self._call("mcp__stardew__stardew_action",
                         {"actionType": "player_use_tool", "x": tx, "y": ty})

    async def walk_and_use(self, tx: int, ty: int, **kw) -> tuple[bool, str]:
        """走到目标格旁边再用工具（相邻一格即可）。"""
        cur = player_tile()
        if cur is None:
            return False, "读不到位置"
        if max(abs(cur[0] - tx), abs(cur[1] - ty)) > 1:
            ok, msg = await self.move_to(tx, ty, **kw)
            if not ok:
                return False, msg
        await self.use_tool(tx, ty)
        return True, "已使用工具"
