#!/usr/bin/env python3
"""星露谷 AI 自主循环（策略层）。

架构借鉴 agent-stardew / SPIKE：

  ① 规则层（每轮，零 token）：体力 < 20% 或 18:00 后 → 收工
  ② 现场候选层（每轮，零 token）：读逐格数据，从**当前现场**生成原子动作
       - 熟作物 → 去收（最高优先）
       - 该浇水的作物 → 去浇
       - 能清杂草/石头/树枝 → 去清
     一次一个原子动作，做完重新观察再决定（不按剧本走）
  ③ 战略层（只在事件边界调模型）：现场没活才问模型去 farm/mine/fish/follow

其它借鉴点：
  - 同条件下失败两次的动作暂时排除
  - 记录"干活证据"（收了几格/浇了几格），不靠模型自称完成
  - 汇报只发**私聊**
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

DEFAULT_INTERVAL = 60.0
REPORT_COOLDOWN = 300.0

STRATEGY_MODES = {"farm", "mine", "fish", "follow", "idle"}
INSTANT_ACTIONS = {"harvest_all", "water_all"}
ALLOWED_ACTIONS = STRATEGY_MODES | INSTANT_ACTIONS

STAMINA_LOW = 15.0
HOUR_QUIT = 18
HOUR_RESUME = 6
DEFAULT_COMPANION = "Dafeiyu"

DEFAULT_REPORTS = {
    "farm": "去农场干活了。",
    "mine": "下矿去了，挖点宝贝回来 (￣▽￣)",
    "fish": "钓鱼去了，别打扰本鲸鱼。",
    "follow": "跟着主人走。",
    "idle": "原地待命。",
    "harvest_all": "熟的全收了。",
    "water_all": "水浇完了，累死。",
}

TILE_ACTION = {"harvest": "harvest", "water": "water",
               "clear_weed": "clear", "clear_stone": "clear", "clear_twig": "clear"}
CLEARABLE = {"weed": "clear_weed", "stone": "clear_stone", "twig": "clear_twig"}


class EventTrigger:
    """SPIKE 事件触发：只在事件边界调模型。"""

    def __init__(self, refresh_steps: int = 3, stall_threshold: int = 4) -> None:
        self.refresh_steps = max(1, refresh_steps)
        self.stall_threshold = max(1, stall_threshold)
        self.steps = 0
        self.stall = 0
        self.last_key: str | None = None

    def decide(self, state: dict) -> tuple[bool, str]:
        self.steps += 1
        key = "|".join([
            str(state.get("season")), str(state.get("day")), str(state.get("time")),
            str(state.get("location")),
            str([(c.get("mode"), c.get("tile")) for c in (state.get("companions") or [])]),
        ])
        if self.steps >= self.refresh_steps:
            self.steps = 0; self.stall = 0; self.last_key = key
            return True, "periodic_refresh"
        if self.last_key is not None and key != self.last_key:
            self.last_key = key; self.stall = 0
            return True, "state_change"
        self.last_key = key
        self.stall += 1
        if self.stall >= self.stall_threshold:
            self.stall = 0
            return True, "stall"
        return False, "reactive"


class GameLoop:
    """AI 自主游戏循环。"""

    def __init__(self, client: Any, model: Any, mcp_servers: list[dict[str, Any]],
                 report_target: dict[str, str] | None = None,
                 interval: float = DEFAULT_INTERVAL,
                 companion: str = DEFAULT_COMPANION, log: Any = None,
                 backend: str = "stardew", values: dict[str, Any] | None = None) -> None:
        self.client = client
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.report_target = report_target or {}
        self.interval = max(30.0, float(interval))
        self.companion = companion
        self.log = log or (lambda *a, **k: None)
        self._task: asyncio.Task[None] | None = None
        self._last_report: dict[str, float] = {}
        self._current_mode = ""
        self.trigger = EventTrigger()
        # 两种「身体」：stardew=同伴 NPC（文件桥，玩家照常玩）；
        # nagi=直接驾驶玩家本人（HTTP）。见 game_backend.py。
        from game_backend import make_backend
        self.backend = make_backend(backend, companion=self.companion, values=values, log=self.log)
        self.done = {"harvest": 0, "water": 0, "clear": 0}
        # 只有「核验过」的动作才进 done；verified 是其中复核通过的那部分
        self.verified = {"harvest": 0, "water": 0, "clear": 0}
        self.failures: dict[tuple[str, int, int], int] = {}
        # 预算（抄 agent-stardew 的思路）：防止一晚上把体力和时间烧光
        conf = (values or {}).get("game_loop") or {}
        budget = conf.get("budget") or {}
        self.max_actions = int(budget.get("max_actions", 80))
        self.max_model_calls = int(budget.get("max_model_calls", 40))
        self.max_minutes = float(budget.get("max_minutes", 45))
        self.actions = 0
        self.model_calls = 0
        self._started = time.monotonic()
        self._stopped = False
        # 工具动画要几百毫秒才落地；立刻复核会把「还没生效」误判成失败
        self.verify_delay = float(budget.get("verify_delay_seconds", 0.8))
        # 大树桩/原木/巨石要挥好几下；每下都算一次动作预算
        self.max_swings = int(budget.get("max_swings", 6))
        self._player_mode_warned = False

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._task is not None or not self.mcp_servers:
            return
        self._task = asyncio.create_task(self._run())
        self.log("[game] 自主循环已启动，每 %.0f 秒醒一次", self.interval)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(20)
        while not self._stopped:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                self.log("[game] tick 失败：%s", error)
            await asyncio.sleep(self.interval)

    # ---- 观察 ----

    async def _observe(self) -> tuple[dict, list[dict]] | None:
        return await self.backend.observe()

    # ---- 现场候选 ----

    def _build_candidates(self, comp: dict, tiles: list[dict]) -> list[dict[str, Any]]:
        tile = comp.get("tile") or {}
        cx, cy = int(tile.get("x", 0)), int(tile.get("y", 0))
        out: list[dict[str, Any]] = []
        for t in tiles:
            x, y = int(t.get("x", 0)), int(t.get("y", 0))
            dist = max(abs(x - cx), abs(y - cy))
            if dist > 12:
                continue
            kind, priority = None, 0
            if t.get("cropReady"):
                kind, priority = "harvest", 100
            elif t.get("crop") and t.get("waterState") == 0:
                kind, priority = "water", 90
            elif t.get("breakable"):
                ot = str(t.get("objType") or "").lower()
                if ot in CLEARABLE:
                    kind, priority = CLEARABLE[ot], 40
            if not kind:
                continue
            if self.failures.get((kind, x, y), 0) >= 2:
                continue
            out.append({"kind": kind, "x": x, "y": y, "dist": dist, "priority": priority})
        out.sort(key=lambda c: (-c["priority"], c["dist"]))
        return out

    # ---- 一轮 ----

    async def _tick(self) -> None:
        reason = self._budget_reason()
        if reason:
            self.log("[game] 预算用尽（%s），停止循环", reason)
            await self._report(f"今天的农活额度用完了（{reason}），我先收工 (´・ω・)")
            self._stopped = True
            return
        if self.backend.kind == "stardew":
            from mcp_bridge import ensure_servers, tool_schemas
            await ensure_servers(self.mcp_servers)
            if not tool_schemas():
                return
        observed = await self._observe()
        if observed is None:
            return
        state, tiles = observed
        comp = (state.get("companions") or [{}])[0]

        # ① 场景同步：星露谷只模拟玩家所在场景，伙伴留在别处会完全冻结、
        #    而且玩家也看不见她。所以一旦不在同一场景，立刻传送过去。
        if str(comp.get("location") or "") != str(state.get("location") or ""):
            await self._sync_location(state)
            return

        rule = self._rule_based(state, comp)
        if rule is not None:
            await self._apply_mode(rule["action"], rule["report"])
            return

        if not tiles:
            await self._ensure_player_mode()
            return

        cands = self._build_candidates(comp, tiles)
        if cands:
            await self._do_tile_action(comp, cands[0])
            return

        escalate, reason = self.trigger.decide(state)
        if not escalate:
            self.log("[game] 现场没活，无事件（%s），跳过模型", reason)
            return
        self.log("[game] 现场没活，事件触发模型（%s）", reason)
        decision = await self._decide_strategy(state)
        if not decision:
            return
        decision = self._guard(decision, state, comp)
        await self._apply_mode(decision["action"], decision.get("report", ""))

    def _budget_reason(self) -> str:
        """超预算时返回可读原因，否则空串。"""
        if self.actions >= self.max_actions:
            return f"动作 {self.actions}/{self.max_actions}"
        if self.model_calls >= self.max_model_calls:
            return f"模型调用 {self.model_calls}/{self.max_model_calls}"
        minutes = (time.monotonic() - self._started) / 60.0
        if minutes >= self.max_minutes:
            return f"时长 {minutes:.0f}/{self.max_minutes:.0f} 分钟"
        return ""

    async def _sync_location(self, state: dict) -> None:
        """把伙伴传送到玩家所在场景（否则她会被冻结在别的场景里）。"""
        loc = str(state.get("location") or "")
        if not loc:
            return
        pos = (state.get("player") or {}).get("position") or {}
        px, py = int(pos.get("x", 0) // 64), int(pos.get("y", 0) // 64)
        await self.backend.warp(loc, px + 1, py)
        self.log("[game] %s 不在玩家场景，传送到 %s (%d,%d)", self.companion, loc, px, py)

    async def _ensure_player_mode(self) -> None:
        ensure = getattr(self.backend, "ensure_player_mode", None)
        if ensure is None:
            return  # nagi 后端本来就在观察玩家，不需要切模式
        if self._current_mode == "player":
            return
        await ensure()
        self._current_mode = "player"

    async def _do_tile_action(self, comp: dict, cand: dict[str, Any]) -> None:
        kind, x, y, dist = cand["kind"], cand["x"], cand["y"], cand["dist"]
        tile = comp.get("tile") or {}
        dx = int(x) - int(tile.get("x", 0))
        dy = int(y) - int(tile.get("y", 0))
        diagonal = bool(dx) and bool(dy)
        if dist > 1 or (diagonal and kind != "harvest"):
            # 斜对角不能直接动手（工具只作用于正前方），先走到目标格再打
            self.actions += 1
            await self.backend.move_to(x, y)
            self.log("[game] 靠近 (%d,%d) 去干 %s", x, y, kind)
            return
        bucket = "harvest" if kind == "harvest" else ("water" if kind == "water" else "clear")
        # 多击资源（树桩/原木/巨石）一次挥不完：连续挥到核验通过或用完挥击次数
        swings = self.max_swings if kind.startswith("clear_") else 1
        ok, result = False, "超过挥击次数仍未清掉"
        for _ in range(max(1, swings)):
            self.actions += 1
            ok, result = await self.backend.act(kind, x, y)
            if not ok:
                break
            if await self._verify_tile(kind, x, y):
                break
            ok = False  # 这一下没清掉，继续挥
            result = "上报成功但现场未核验到变化"
        if ok:
            self.done[bucket] += 1
            self.verified[bucket] += 1
            self.failures.pop((kind, x, y), None)
            self.log("[game] %s (%d,%d) 成功（已核验）", kind, x, y)
        else:
            self.failures[(kind, x, y)] = self.failures.get((kind, x, y), 0) + 1
            self.log("[game] %s (%d,%d) 失败：%s", kind, x, y, str(result)[:60])
        if self.done[bucket] and self.done[bucket] % 5 == 0:
            await self._report(f"干完 {bucket} 累计 {self.done[bucket]} 次了 (￣▽￣)")

    async def _verify_tile(self, kind: str, x: int, y: int) -> bool:
        """动作后重新观察，确认那格真的变了。观察失败时不冤枉它（按原结果）。"""
        if self.verify_delay > 0:
            # 挥工具是「开始动画」而不是「立刻生效」，不等就会误判
            await asyncio.sleep(self.verify_delay)
        observed = await self.backend.observe()
        if observed is None:
            return True
        _, tiles = observed
        tile = next((t for t in tiles
                     if int(t.get("x", -1)) == int(x) and int(t.get("y", -1)) == int(y)), None)
        if kind == "harvest":
            return tile is None or not tile.get("cropReady")
        if kind == "water":
            return tile is not None and int(tile.get("waterState", 0)) == 1
        return tile is None or not tile.get("breakable")

    # ---- 规则 & 战略 ----

    def _rule_based(self, state: dict, comp: dict) -> dict[str, Any] | None:
        try:
            stamina = max(0.0, float(comp.get("stamina") or 100.0))
        except (TypeError, ValueError):
            stamina = 100.0
        hour = int(state.get("time") or 600) // 100
        if stamina <= STAMINA_LOW:
            return {
                "action": "idle",
                "report": f"体力见底了（{stamina:.0f}%），得回去睡一觉才能接着干 (´・ω・)",
            }
        if hour >= HOUR_QUIT or hour < HOUR_RESUME:
            return {"action": "idle", "report": "天黑了，收工。明天再干。"}
        return None

    def _guard(self, decision: dict[str, Any], state: dict, comp: dict) -> dict[str, Any]:
        if decision.get("action") != "idle":
            return decision
        try:
            stamina = max(0.0, float(comp.get("stamina") or 100.0))
        except (TypeError, ValueError):
            stamina = 100.0
        hour = int(state.get("time") or 600) // 100
        if stamina > 30.0 and HOUR_RESUME <= hour < HOUR_QUIT:
            return {"action": "mine", "report": "闲得慌，本鲸鱼下矿去了 (￣▽￣)"}
        return decision

    async def _decide_strategy(self, state: dict) -> dict[str, Any] | None:
        comp = (state.get("companions") or [{}])[0]
        brief = {
            "time": state.get("time"), "season": state.get("season"),
            "day": state.get("day"), "weather": state.get("weather"),
            "location": state.get("location"),
            "money": (state.get("player") or {}).get("money"),
            "companion": {"mode": comp.get("mode"), "stamina": comp.get("stamina")},
            "done_so_far": self.done,
        }
        prompt = (
            "星露谷当前情况：\n" + json.dumps(brief, ensure_ascii=False) + "\n\n"
            "现场已经没有能直接干的农活了。现在往哪个方向去？只能选一个：\n"
            "- farm：留在农场（还有杂物可清）\n- mine：下矿挖矿打怪\n"
            "- fish：去钓鱼\n- follow：跟着主人\n- idle：原地待命\n\n"
            "严格只输出两行：\n<action>动作名</action>\n"
            "<report>不超过 25 字、带大肥鱼语气；没事就留空</report>"
        )
        self.model_calls += 1
        try:
            # 统一走 model.ask：LocalModel 和 DshAgent 都有这个接口。
            # 早先这里直接拼 ollama_base + model.client，换上 DSH 后端就会炸
            # （DshAgent 没有 .client），战略层因此静默失效。
            reply = await self.model.ask(
                "game:strategy",
                prompt,
                system_suffix="你只输出指定的两行标签，不要解释。",
            )
        except Exception as error:  # noqa: BLE001
            self.log("[game] 战略决策失败：%s: %s", type(error).__name__, error)
            return None
        return self._parse(reply)

    @staticmethod
    def _parse(text: str) -> dict[str, Any] | None:
        action_m = re.search(r"<action>\s*([a-z_]+)\s*</action>", text, re.I)
        report_m = re.search(r"<report>(.*?)</report>", text, re.S)
        if not action_m:
            return None
        action = action_m.group(1).strip().lower()
        if action not in ALLOWED_ACTIONS:
            return None
        report = ""
        if report_m:
            report = re.sub(r"\s+", " ", report_m.group(1)).strip()[:70]
        return {"action": action, "report": report}

    async def _apply_mode(self, action: str, report: str) -> None:
        if action not in ALLOWED_ACTIONS:
            return
        if action in STRATEGY_MODES:
            supported = await self.backend.set_mode(action)
            if not supported:
                self.log("[game] 后端(%s)不支持模式 %s，退回 idle", self.backend.kind, action)
                await self.backend.set_mode("idle")
                action = "idle"
            changed = action != self._current_mode
            self._current_mode = action
            self.log("[game] 模式 → %s", action)
        else:
            ok = await self.backend.instant(action)
            changed = False
            self.log("[game] 执行 %s：%s", action, "成功" if ok else "不支持或失败")
        if not report:
            report = DEFAULT_REPORTS.get(action, "")
        if not report:
            return
        if action in STRATEGY_MODES and not changed:
            return
        now = time.monotonic()
        if now - self._last_report.get(action, 0.0) < REPORT_COOLDOWN:
            return
        self._last_report[action] = now
        await self._report(report)

    async def _report(self, text: str) -> None:
        user = str(self.report_target.get("user_openid") or "")
        group = str(self.report_target.get("group_openid") or "")
        try:
            if user:
                await self.client.api.post_c2c_message(openid=user, msg_type=0, content=text)
                self.log("[game] 已汇报到私聊：%s", text)
            elif group:
                await self.client.api.post_group_message(group_openid=group, msg_type=0, content=text)
                self.log("[game] 已汇报到群：%s", text)
            else:
                self.log("[game] 没有汇报目标：%s", text)
        except Exception as error:  # noqa: BLE001
            self.log("[game] 汇报失败：%s", error)
