#!/usr/bin/env python3
"""离线单测游戏后端抽象：Nagi 字段归一化、动作映射、模式降级。

不连游戏、不发 HTTP——`NagiBackend._request` 被替换成记账的假实现。

用法::

    python3 test_game_backend.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import game_backend as gb  # noqa: E402

FAILURES: list[str] = []
CALLS: list[tuple[str, str, object]] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


class FakeNagi(gb.NagiBackend):
    """按路径返回固定数据，同时记账所有调用。"""

    def __init__(self, overrides: dict[str, dict] | None = None) -> None:
        super().__init__()
        self.overrides = overrides or {}
        self._player = {"x": 10, "y": 10}

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        CALLS.append((method, path, payload))
        for prefix, body in self.overrides.items():
            if path.startswith(prefix):
                return body
        return {"ok": True}

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        return self._request(method, path, payload)


STATE = {
    "ok": True,
    "worldReady": True,
    "player": {"name": "joe", "x": 10, "y": 10, "health": 100, "stamina": 54,
               "maxStamina": 270, "money": 500, "facingDirection": 2},
    "location": {"name": "Farm"},
    "time": {"timeOfDay": 930, "dayOfMonth": 12, "season": "fall", "year": 2},
    "inventory": [], "npcs": [],
}

SUR = {
    "ok": True,
    "tiles": [
        {"x": 11, "y": 10, "passable": False, "terrain": "HoeDirt",
         "crop": "24", "cropPhase": 4, "harvestable": True},
        {"x": 12, "y": 10, "passable": True, "terrain": "HoeDirt", "crop": "24", "cropPhase": 1},
        {"x": 12, "y": 11, "passable": True, "terrain": "HoeDirt", "watered": True, "crop": "24"},
        {"x": 13, "y": 10, "passable": False, "object": "Weeds"},
        {"x": 14, "y": 10, "passable": False, "object": "Stone"},
        {"x": 15, "y": 10, "passable": False, "resource": "LargeStump"},
        {"x": 16, "y": 10, "passable": True},
    ],
}


def test_observe() -> None:
    print("\n[1] 观察归一化")
    backend = FakeNagi({"/state": STATE, "/surroundings": SUR})

    async def run():
        return await backend.observe()

    state, tiles = asyncio.run(run())
    comp = state["companions"][0]
    check("时间/季节/天数", (state["time"], state["season"], state["day"]), (930, "fall", 12))
    check("地点", state["location"], "Farm")
    check("把玩家合成 companion", (comp["name"], comp["tile"]), ("joe", {"x": 10, "y": 10}))
    check("体力换算成百分比", comp["stamina"], 20.0)
    check("没有天气字段就留 None", state["weather"], None)

    by_xy = {(t["x"], t["y"]): t for t in tiles}
    check("成熟作物 → cropReady", by_xy[(11, 10)]["cropReady"], True)
    check("熟作物不再算缺水", by_xy[(11, 10)]["waterState"], 1)
    check("未浇水作物 → waterState=0", by_xy[(12, 10)]["waterState"], 0)
    check("浇过水的 → waterState=1", by_xy[(12, 11)]["waterState"], 1)
    check("杂草 → breakable/weed", (by_xy[(13, 10)]["breakable"], by_xy[(13, 10)]["objType"]), (True, "weed"))
    check("石头 → stone", by_xy[(14, 10)]["objType"], "stone")
    check("大树桩也算可清", (by_xy[(15, 10)]["breakable"], by_xy[(15, 10)]["objType"]), (True, "stone"))
    check("空地不可清", by_xy[(16, 10)]["breakable"], False)


def test_actions() -> None:
    print("\n[2] 动作映射")
    backend = FakeNagi({"/harvest": {"ok": True, "harvested": 2}})
    backend._player = {"x": 10, "y": 10}

    async def run(kind, x, y):
        CALLS.clear()
        return await backend.act(kind, x, y)

    ok, _ = asyncio.run(run("harvest", 11, 10))
    check("收割成功判定", ok, True)
    check("收割前先转向", CALLS[0], ("POST", "/face", {"direction": 1}))
    check("收割走 /harvest", CALLS[1][1], "/harvest?radius=4")

    asyncio.run(run("water", 10, 12))
    check("浇水选水壶", CALLS[1], ("POST", "/select", {"name": "Watering Can"}))
    check("浇水用工具", CALLS[2], ("POST", "/tool", {"name": "Watering Can"}))
    check("向下浇水朝南", CALLS[0][2], {"direction": 2})

    asyncio.run(run("clear_stone", 14, 11))
    check("清石头用镐", CALLS[1], ("POST", "/select", {"name": "Pickaxe"}))

    asyncio.run(run("clear_twig", 14, 11))
    check("清树枝用斧", CALLS[1], ("POST", "/select", {"name": "Axe"}))

    backend_empty = FakeNagi({"/harvest": {"ok": True, "harvested": 0}})
    ok, detail = asyncio.run(backend_empty.act("harvest", 11, 10))
    check("一格没收成就不算成功", ok, False)


def test_modes() -> None:
    print("\n[3] 模式降级")
    backend = FakeNagi()

    async def run(mode):
        CALLS.clear()
        return await backend.set_mode(mode)

    check("farm 由循环驱动，直接支持", asyncio.run(run("farm")), True)
    check("farm 不调远端", CALLS, [])

    check("fish 开鱼 bot", asyncio.run(run("fish")), True)
    check("fish 端点", CALLS[-1], ("POST", "/fishbot", {"action": "on"}))

    check("idle 关鱼 bot + 停", asyncio.run(run("idle")), True)
    check("idle 先关鱼 bot", CALLS[0], ("POST", "/fishbot", {"action": "off"}))

    check("mine 明确不支持", asyncio.run(run("mine")), False)
    check("follow 明确不支持", asyncio.run(run("follow")), False)


def test_instant_and_factory() -> None:
    print("\n[4] 批量动作 & 工厂")
    backend = FakeNagi({"/harvest": {"ok": True, "harvested": 3}})

    async def run(action):
        CALLS.clear()
        return await backend.instant(action)

    check("harvest_all 可用", asyncio.run(run("harvest_all")), True)
    check("harvest_all 半径 15", CALLS[-1][1], "/harvest?radius=15")
    check("water_all 如实说不支持", asyncio.run(run("water_all")), False)

    check("nagi 后端", gb.make_backend("nagi").kind, "nagi")
    check("未知值退回 stardew", gb.make_backend("whatever").kind, "stardew")
    check("nagi_base 可配置", gb.make_backend("nagi", values={"nagi_base": "http://localhost:9"}).base,
          "http://localhost:9")


def main() -> int:
    print("=" * 60)
    print("游戏后端抽象 离线单测")
    test_observe()
    test_actions()
    test_modes()
    test_instant_and_factory()
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
