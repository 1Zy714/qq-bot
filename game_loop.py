#!/usr/bin/env python3
"""星露谷 AI 自主循环：她看状态 → 自己决定做什么 → 干完主动来 QQ 汇报。

工作方式：
    定时（默认 5 分钟）醒一次，读游戏状态，交给模型判断"有没有事该做"。
    有事就通过 MCP 调工具去做，做完用 QQ **主动消息**告诉主人。
    没事就闭嘴（不打扰）。

安全阀：
    - 游戏没跑（bridge 文件不存在）→ 直接跳过
    - 模型忙 → 跳过（不排队）
    - 汇报冷却：同一个动作 10 分钟内不重复报
    - 只在配置的目标群/私聊汇报
    - 失败只在日志里留痕，不往群里刷错误
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

DEFAULT_INTERVAL = 300.0   # 5 分钟醒一次
REPORT_COOLDOWN = 600.0    # 同一动作 10 分钟内不重复汇报

ALLOWED_ACTIONS = {"harvest_all", "water_all", "mine", "fish", "none"}


class GameLoop:
    """AI 自主游戏循环。"""

    def __init__(
        self,
        client: Any,
        model: Any,
        mcp_servers: list[dict[str, Any]],
        report_target: dict[str, str] | None = None,
        interval: float = DEFAULT_INTERVAL,
        log: Any = None,
    ) -> None:
        self.client = client
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.report_target = report_target or {}
        self.interval = max(60.0, float(interval))
        self.log = log or (lambda *a, **k: None)
        self._task: asyncio.Task[None] | None = None
        self._last_report: dict[str, float] = {}

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
        # 启动先等一会，避免和网关、模型加载抢资源
        await asyncio.sleep(30)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 循环不能因一次异常死掉
                self.log("[game] tick 失败：%s", error)
            await asyncio.sleep(self.interval)

    # ---- 一轮 ----

    async def _tick(self) -> None:
        from mcp_bridge import call_tool, ensure_servers, tool_schemas

        # ① 游戏在跑吗（没有 bridge 文件就什么都不做）
        await ensure_servers(self.mcp_servers)
        if not tool_schemas():
            return

        # ② 读状态
        state_raw = await call_tool("mcp__stardew__stardew_get_state", {})
        if not state_raw or state_raw.lstrip().startswith('{"error"'):
            return

        # ③ 让模型决定
        decision = await self._decide(state_raw)
        if not decision:
            return
        action = decision.get("action") or "none"
        report = str(decision.get("report") or "").strip()
        if action == "none":
            self.log("[game] 模型判断：无需动作")
            return

        # ④ 执行
        self.log("[game] 决定执行 %s（%s）", action, report[:40])
        # 工具全名是 mcp__<server>__<rawTool>；星露谷桥的 rawTool 自带 stardew_ 前缀，
        # 所以完整名是 mcp__stardew__stardew_harvest_all（少一层就是未知工具）。
        result = await call_tool(f"mcp__stardew__stardew_{action}", {})
        self.log("[game] 执行结果：%s", result[:80])

        # ⑤ 汇报（带冷却）
        if not report:
            return
        now = time.monotonic()
        if now - self._last_report.get(action, 0.0) < REPORT_COOLDOWN:
            return
        self._last_report[action] = now
        await self._report(report)

    # ---- 决策 ----

    async def _decide(self, state_raw: str) -> dict[str, Any] | None:
        """让模型判断「该不该做事」。只允许白名单动作。

        注意：**故意不喂完整人设、不喂整个 state JSON**。
        游戏运行时显存已经很紧（16 GB 里模型占 13+ GB），prompt 越长瓶颈越明显
        （实测 5562 tokens 时，光读 prompt 就要 200+ 秒）。这里只保留判断需要的字段。
        """
        # 从 state 里挑出决策真正需要的字段，避免整段 JSON 灌进去
        brief: dict[str, Any] = {}
        try:
            data = json.loads(state_raw)
            brief = {
                "season": data.get("season"),
                "day": data.get("day"),
                "time": data.get("time"),
                "weather": data.get("weather"),
                "location": data.get("location"),
                "player": {
                    "stamina": (data.get("player") or {}).get("stamina"),
                    "money": (data.get("player") or {}).get("money"),
                },
                "companions": [
                    {"name": c.get("name"), "mode": c.get("mode"), "status": c.get("status")}
                    for c in (data.get("companions") or [])
                ],
            }
        except Exception:  # noqa: BLE001 - JSON 解析失败就退回原始片段
            brief = {"raw": state_raw[:400]}

        prompt = (
            "星露谷农场状态：\n"
            f"{json.dumps(brief, ensure_ascii=False)}\n\n"
            "现在有没有该做的事？可选动作（只能选一个）：\n"
            "harvest_all / water_all / mine / fish / none\n\n"
            "严格只输出两行：\n"
            "<action>动作名</action>\n"
            "<report>若做了事，用不超过 20 字、大肥鱼语气向主人汇报；none 时留空</report>"
        )
        try:
            # 决策只需要文本推理，**故意不带工具**：带 25 个游戏工具会让模型
            # 犹豫很久甚至超时（表现为空异常的 TimeoutError）。这里直接打端点。
            cfg = self.model.cfg
            url = str(cfg.ollama_base).rstrip("/") + "/chat/completions"
            payload = {
                "model": cfg.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是大肥鱼，在星露谷里帮主人干活。只输出指定的两行标签，不要解释。",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 120,
                "temperature": 0.7,
                "stream": False,
                "reasoning_effort": "none",
            }
            # 游戏占着显存时模型会明显变慢，给足时间（默认 90s 不够）
            response = await self.model.client.post(
                url, json=payload, timeout=300.0
            )
            response.raise_for_status()
            data = response.json()
            reply = str((data["choices"][0]["message"] or {}).get("content") or "")
        except Exception as error:  # noqa: BLE001
            self.log("[game] 决策失败：%s: %s", type(error).__name__, error)
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
            report = re.sub(r"\s+", " ", report_m.group(1)).strip()[:60]
        return {"action": action, "report": report}

    # ---- 汇报 ----

    async def _report(self, text: str) -> None:
        """主动发 QQ 消息汇报。"""
        group = str(self.report_target.get("group_openid") or "")
        user = str(self.report_target.get("user_openid") or "")
        try:
            if group:
                await self.client.api.post_group_message(
                    group_openid=group, msg_type=0, content=text
                )
                self.log("[game] 已汇报到群 %s：%s", group[:8], text)
            elif user:
                await self.client.api.post_c2c_message(
                    openid=user, msg_type=0, content=text
                )
                self.log("[game] 已汇报到私聊 %s：%s", user[:8], text)
            else:
                self.log("[game] 没有汇报目标，只记日志：%s", text)
        except Exception as error:  # noqa: BLE001
            self.log("[game] 汇报失败：%s", error)
