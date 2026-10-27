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


def test_strategy_model() -> None:
    print("\n[5] 战略决策走 model.ask（不再依赖 .client）")
    import game_loop as gl

    class FakeModel:
        def __init__(self) -> None:
            self.calls = []

        async def ask(self, session, prompt, system_suffix=""):
            self.calls.append((session, system_suffix))
            return "<action>farm</action>\n<report>去农场</report>"

    model = FakeModel()
    loop = gl.GameLoop(client=None, model=model, mcp_servers=[], backend="nagi")
    decision = asyncio.run(loop._decide_strategy({"time": 700, "location": "Farm"}))
    check("解析出动作", decision["action"], "farm")
    check("走的是 model.ask", model.calls and model.calls[0][0], "game:strategy")
    check("带标签约束", "只输出" in model.calls[0][1], True)


class SeqBackend:
    """观察返回固定 tiles，动作永远成功——用来测「核验」本身。"""

    kind = "nagi"

    def __init__(self, tiles):
        self.tiles = tiles
        self.acted = []

    async def observe(self):
        return ({"companions": [{"tile": {"x": 0, "y": 0}}], "time": 600,
                 "location": "Farm"}, self.tiles)

    async def act(self, kind, x, y):
        self.acted.append((kind, x, y))
        return True, "reported ok"

    async def move_to(self, x, y):
        return True

    async def set_mode(self, mode):
        return True

    async def instant(self, action):
        return True

    async def warp(self, location, x, y):
        return True


def test_verify_and_budget() -> None:
    print("\n[6] 核验与预算")
    import game_loop as gl

    class FakeModel:
        async def ask(self, *a, **k):
            return "<action>farm</action>"

    def make_loop(tiles):
        loop = gl.GameLoop(client=None, model=FakeModel(), mcp_servers=[], backend="nagi")
        loop.backend = SeqBackend(tiles)
        loop.verify_delay = 0  # 单测不真等动画
        return loop

    cand = {"kind": "harvest", "x": 5, "y": 5, "dist": 1}

    comp = {"tile": {"x": 5, "y": 4}}  # 正上方邻格，正交
    ok_loop = make_loop([{"x": 5, "y": 5, "cropReady": False}])
    asyncio.run(ok_loop._do_tile_action(comp, cand))
    check("核验通过才计数", ok_loop.done["harvest"], 1)

    bad_loop = make_loop([{"x": 5, "y": 5, "cropReady": True}])
    asyncio.run(bad_loop._do_tile_action(comp, cand))
    check("上报成功但现场没变 → 不计数", bad_loop.done["harvest"], 0)
    check("失败进黑名单", bad_loop.failures.get(("harvest", 5, 5)), 1)

    water_loop = make_loop([{"x": 5, "y": 5, "crop": True, "waterState": 1}])
    asyncio.run(water_loop._do_tile_action(comp, {"kind": "water", "x": 5, "y": 5, "dist": 1}))
    check("浇水的核验看 waterState", water_loop.done["water"], 1)

    clear_loop = make_loop([])
    asyncio.run(clear_loop._do_tile_action({"tile": {"x": 4, "y": 5}}, {"kind": "clear_weed", "x": 5, "y": 5, "dist": 1}))
    check("格子消失也算清掉了", clear_loop.done["clear"], 1)

    diag_loop = make_loop([])
    asyncio.run(diag_loop._do_tile_action({"tile": {"x": 4, "y": 4}}, {"kind": "clear_twig", "x": 5, "y": 5, "dist": 1}))
    check("斜对角先靠近不动手", diag_loop.done["clear"], 0)
    check("斜对角不记失败", diag_loop.failures, {})

    # 多击资源：前两次观察仍是可清物，第三次才消失
    class MultiBackend(SeqBackend):
        def __init__(self):
            super().__init__([[{"x": 5, "y": 5, "breakable": True}],
                              [{"x": 5, "y": 5, "breakable": True}],
                              []])

        async def observe(self):
            tiles = self.tiles.pop(0) if self.tiles else []
            return ({"companions": [{"tile": {"x": 4, "y": 5}}]}, tiles)

    multi = gl.GameLoop(client=None, model=FakeModel(), mcp_servers=[], backend="nagi")
    multi.backend = MultiBackend()
    multi.verify_delay = 0
    asyncio.run(multi._do_tile_action({"tile": {"x": 4, "y": 5}}, {"kind": "clear_twig", "x": 5, "y": 5, "dist": 1}))
    check("多击资源挥到消失才算成", multi.done["clear"], 1)
    check("挥了不止一下", len(multi.backend.acted) >= 2, True)

    # 一直打不动：用完挥击次数后记失败
    class StuckBackend(SeqBackend):
        async def observe(self):
            return ({"companions": [{"tile": {"x": 4, "y": 5}}]},
                    [{"x": 5, "y": 5, "breakable": True}])

    stuck = gl.GameLoop(client=None, model=FakeModel(), mcp_servers=[], backend="nagi")
    stuck.backend = StuckBackend([])
    stuck.verify_delay = 0
    stuck.max_swings = 3
    asyncio.run(stuck._do_tile_action({"tile": {"x": 4, "y": 5}}, {"kind": "clear_stone", "x": 5, "y": 5, "dist": 1}))
    check("打不动时记失败", stuck.failures.get(("clear_stone", 5, 5)), 1)
    check("用完挥击次数就停", len(stuck.backend.acted), 3)

    budget_loop = make_loop([])
    budget_loop.max_actions = 2
    budget_loop.actions = 2
    check("动作超预算", budget_loop._budget_reason().startswith("动作"), True)
    budget_loop.max_actions = 999
    budget_loop.max_model_calls = 1
    budget_loop.model_calls = 1
    check("模型调用超预算", budget_loop._budget_reason().startswith("模型调用"), True)
    budget_loop.max_model_calls = 999
    budget_loop._started -= 4000
    check("时长超预算", "分钟" in budget_loop._budget_reason(), True)


def main() -> int:
    print("=" * 60)
    print("游戏后端抽象 离线单测")
    test_observe()
    test_actions()
    test_modes()
    test_instant_and_factory()
    test_strategy_model()
    test_verify_and_budget()
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
