#!/usr/bin/env python3
"""游戏后端抽象：同一套循环，两种「身体」。

- ``stardew``：原来的同伴 NPC 路线（MCP 文件桥）。玩家照常玩，AI 操作二号角色。
- ``nagi``：NagiBridge 路线（HTTP）。AI 直接接管**玩家本人**的按键。

两条路的数据形状不一样，这里统一成 game_loop 认识的格式：

    observe() → (state, tiles)
      state: time / season / day / weather / location / player / companions
      tiles: {x, y, cropReady, crop, waterState, breakable, objType}

``companions`` 在 nagi 路线里就是玩家自己（合成一个），game_loop 的上层逻辑
因此可以原样复用；但语义差别必须知道：**nagi 会动主人自己的角色**。

NagiBridge 的 HTTP 约定：只认 ``localhost`` 这个 Host（127.0.0.1 会 400）。
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent

# Nagi 的 object 名 → 我们的可清物类型
CLEARABLE_OBJECTS = {"weeds": "weed", "stone": "stone", "twig": "twig", "grass": "weed"}
CLEARABLE_RESOURCES = {"largestump": "stone", "largeboulder": "stone",
                       "meteoriteore": "stone", "largelog": "twig"}
# 清不同东西该拿的工具
CLEAR_TOOL = {"weed": "Scythe", "stone": "Pickaxe", "twig": "Axe"}

DIRECTION_NAMES = {0: "up", 1: "right", 2: "down", 3: "left"}


class BaseBackend:
    kind = "base"

    def __init__(self, log: Callable[..., None] | None = None) -> None:
        self.log = log or (lambda *a, **k: None)

    async def observe(self) -> tuple[dict, list[dict]] | None:
        raise NotImplementedError

    async def move_to(self, x: int, y: int) -> bool:
        raise NotImplementedError

    async def act(self, kind: str, x: int, y: int) -> tuple[bool, str]:
        raise NotImplementedError

    async def set_mode(self, mode: str) -> bool:
        raise NotImplementedError

    async def instant(self, action: str) -> bool:
        raise NotImplementedError

    async def warp(self, location: str, x: int, y: int) -> bool:
        raise NotImplementedError


class StardewBackend(BaseBackend):
    """原来的同伴 NPC 路线。行为和迁移前逐字一致。"""

    kind = "stardew"

    def __init__(self, companion: str, log: Callable[..., None] | None = None) -> None:
        super().__init__(log)
        self.companion = companion
        self._player_mode = False
        self._mode = ""

    async def observe(self):
        from mcp_bridge import call_tool
        raw = await call_tool("mcp__stardew__stardew_get_state", {})
        if not raw or raw.lstrip().startswith('{"error"'):
            return None
        try:
            state = json.loads(raw)
        except ValueError:
            return None
        comps = state.get("companions") or []
        if not comps:
            return None
        self.companion = str(comps[0].get("name") or self.companion)

        sur = await call_tool("mcp__stardew__stardew_get_surroundings", {"companion": self.companion})
        tiles: list[dict] = []
        if sur and "surroundings" in str(sur):
            try:
                tiles = (json.loads(sur).get("surroundings") or {}).get("tiles") or []
            except ValueError:
                tiles = []
        elif not self._player_mode:
            self.log("[game] 暂时拿不到周围数据（不在 player 模式）")
            await self.ensure_player_mode()
        return state, tiles

    async def ensure_player_mode(self) -> None:
        from mcp_bridge import call_tool
        if self._player_mode:
            return
        await call_tool("mcp__stardew__stardew_set_mode", {"target": self.companion, "mode": "player"})
        self._player_mode = True
        self.log("[game] 切到 player 模式以获取周围数据")

    async def move_to(self, x: int, y: int) -> bool:
        from mcp_bridge import call_tool
        await call_tool("mcp__stardew__stardew_move_to", {"companion": self.companion, "x": x, "y": y})
        return True

    async def act(self, kind: str, x: int, y: int) -> tuple[bool, str]:
        from mcp_bridge import call_tool
        action_type = {"harvest": "harvest", "water": "water"}.get(kind, "clear")
        result = await call_tool("mcp__stardew__stardew_action",
                                 {"actionType": action_type, "x": x, "y": y})
        text = str(result)
        ok = "Command sent" in text or "success" in text.lower()
        return ok, text

    async def set_mode(self, mode: str) -> bool:
        from mcp_bridge import call_tool
        if mode not in ("farm", "mine", "fish", "follow", "idle"):
            return False
        await call_tool("mcp__stardew__stardew_set_mode", {"target": self.companion, "mode": mode})
        self._mode = mode
        return True

    async def instant(self, action: str) -> bool:
        from mcp_bridge import call_tool
        if action == "harvest_all":
            await call_tool("mcp__stardew__stardew_harvest_all", {})
            return True
        if action == "water_all":
            await call_tool("mcp__stardew__stardew_water_all", {})
            return True
        return False

    async def warp(self, location: str, x: int, y: int) -> bool:
        from mcp_bridge import call_tool
        await call_tool("mcp__stardew__stardew_warp_companion",
                        {"companion": self.companion, "location": location, "x": x, "y": y})
        return True


class NagiBackend(BaseBackend):
    """NagiBridge 路线：AI 直接驾驶玩家本人。

    注意：没有「模式」系统——``farm`` 由本循环逐格驱动，``fish`` 用鱼 bot，
    ``mine``/``follow`` 没有对应能力（返回 False，上层会退回 idle）。
    """

    kind = "nagi"

    def __init__(self, base: str = "http://localhost:7842", timeout: float = 20.0,
                 log: Callable[..., None] | None = None) -> None:
        super().__init__(log)
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._player: dict[str, Any] = {}
        self._facing = 2

    # ---- HTTP ----

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return {"ok": False, "error": f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:160]}"}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "error": f"非 JSON 返回：{body[:120]}"}

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        return await asyncio.to_thread(self._request, method, path, payload)

    # ---- 观察 ----

    async def observe(self) -> tuple[dict, list[dict]] | None:
        state = await self._call("GET", "/state")
        if not state.get("ok"):
            return None
        player = state.get("player") or {}
        time_obj = state.get("time") or {}
        self._player = player
        self._facing = int(player.get("facingDirection") or 2)
        stamina = player.get("stamina")
        max_stamina = player.get("maxStamina") or 1
        try:
            stamina_pct = max(0.0, min(100.0, float(stamina) / float(max_stamina) * 100.0))
        except (TypeError, ValueError, ZeroDivisionError):
            stamina_pct = 100.0
        location = str((state.get("location") or {}).get("name") or "")
        normalized = {
            "time": time_obj.get("timeOfDay"),
            "season": time_obj.get("season"),
            "day": time_obj.get("dayOfMonth"),
            "weather": None,  # Nagi 没暴露天气
            "location": location,
            "player": {"name": player.get("name"), "money": player.get("money")},
            "companions": [{
                "name": player.get("name") or "player",
                "tile": {"x": player.get("x"), "y": player.get("y")},
                "location": location,
                "stamina": round(stamina_pct, 1),
                "mode": "player",
            }],
        }
        sur = await self._call("GET", "/surroundings?radius=12")
        tiles = self._normalize_tiles(sur.get("tiles") or [])
        return normalized, tiles

    @staticmethod
    def _normalize_tiles(tiles: list[dict]) -> list[dict]:
        """Nagi 字段 → game_loop 认识的字段。"""
        out: list[dict] = []
        for t in tiles:
            if not isinstance(t, dict):
                continue
            obj = str(t.get("object") or "").lower()
            res = str(t.get("resource") or "").lower()
            obj_type = CLEARABLE_OBJECTS.get(obj) or CLEARABLE_RESOURCES.get(res)
            ready = bool(t.get("harvestable"))
            has_crop = "crop" in t
            out.append({
                "x": t.get("x"),
                "y": t.get("y"),
                "cropReady": ready,
                "crop": has_crop,
                # 熟了的作物不再需要浇水（game_loop 先判 cropReady，这里保持一致语义）
                "waterState": 0 if (has_crop and not t.get("watered") and not ready) else 1,
                "breakable": bool(obj_type),
                "objType": obj_type or obj or res,
            })
        return out

    # ---- 动作 ----

    async def move_to(self, x: int, y: int) -> bool:
        result = await self._call("POST", "/move", {"x": int(x), "y": int(y)})
        return bool(result.get("ok"))

    @staticmethod
    def _face_towards(px: int, py: int, tx: int, ty: int) -> int:
        """朝向：0上 1右 2下 3左。优先走差距大的那个轴。"""
        dx, dy = tx - px, ty - py
        if abs(dx) >= abs(dy):
            return 1 if dx > 0 else 3
        return 2 if dy > 0 else 0

    async def act(self, kind: str, x: int, y: int) -> tuple[bool, str]:
        px = int(self._player.get("x") or 0)
        py = int(self._player.get("y") or 0)
        direction = self._face_towards(px, py, int(x), int(y))
        await self._call("POST", "/face", {"direction": direction})
        self._facing = direction

        if kind == "harvest":
            # /harvest 按半径批量收，不需要对准某一格
            result = await self._call("POST", "/harvest?radius=4", {})
            done = int(result.get("harvested") or 0)
            return done > 0, json.dumps(result, ensure_ascii=False)[:180]

        if kind == "water":
            tool = "Watering Can"
        else:
            # game_loop 传的是 clear_weed / clear_stone / clear_twig
            base = kind[len("clear_"):] if kind.startswith("clear_") else kind
            tool = CLEAR_TOOL.get(base, "Scythe")
        selected = await self._call("POST", "/select", {"name": tool})
        if not selected.get("ok"):
            return False, f"选不到工具 {tool}: {json.dumps(selected, ensure_ascii=False)[:120]}"
        result = await self._call("POST", "/tool", {"name": tool})
        return bool(result.get("ok")), json.dumps(result, ensure_ascii=False)[:180]

    async def set_mode(self, mode: str) -> bool:
        if mode == "farm":
            return True  # 农活由本循环逐格驱动
        if mode == "fish":
            result = await self._call("POST", "/fishbot", {"action": "on"})
            return bool(result.get("ok"))
        if mode == "idle":
            await self._call("POST", "/fishbot", {"action": "off"})
            await self._call("POST", "/stop", {})
            return True
        # mine / follow：Nagi 没有对应能力
        return False

    async def instant(self, action: str) -> bool:
        if action == "harvest_all":
            result = await self._call("POST", "/harvest?radius=15", {})
            return bool(result.get("ok"))
        if action == "water_all":
            # 没有批量浇水端点：让循环逐格处理，这里如实返回失败
            return False
        return False

    async def warp(self, location: str, x: int, y: int) -> bool:
        result = await self._call("POST", "/warp", {"location": location, "x": int(x), "y": int(y)})
        return bool(result.get("ok"))

    async def sleep(self) -> bool:
        result = await self._call("POST", "/sleep", {})
        return bool(result.get("ok"))


def make_backend(kind: str, companion: str = "Dafeiyu",
                 values: dict[str, Any] | None = None,
                 log: Callable[..., None] | None = None) -> BaseBackend:
    """按配置造后端。未知值一律退回 stardew（保守默认）。"""
    values = values or {}
    if str(kind).strip().lower() == "nagi":
        return NagiBackend(
            base=str(values.get("nagi_base") or os.environ.get("NAGI_BASE") or "http://localhost:7842"),
            timeout=float(values.get("nagi_timeout") or 20.0),
            log=log,
        )
    return StardewBackend(companion=companion, log=log)
