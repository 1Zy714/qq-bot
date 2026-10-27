#!/usr/bin/env python3
"""NagiBridge 只读对照：把它的 HTTP /state 和现有 MCP 的 bridge_data.json 摆在一起看。

用途：决定值不值得把 NagiBridge 当「第二身体」接进来。
**只读**：只调 GET，不碰 /move /tool /cheat 这类会改变游戏的动作端点。

前置：
1. 游戏已用 SMAPI 启动并载入存档
2. Mods 里有 NagiBridge（端口 7842）和原来的 StardewMCPBridge（bridge_data.json）

用法：
    python3 tools/compare_nagibridge.py
    python3 tools/compare_nagibridge.py --bridge "Z:/STEAAM/.../bridge_data.json"

在 WSL 里跑要注意：NagiBridge 跑在 Windows，靠 mirrored 网络用 127.0.0.1 直通；
连不上时先 curl http://127.0.0.1:7842/status 试探。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BRIDGE = Path("/mnt/z/STEAAM/steamapps/common/Stardew Valley/Mods/StardewMCPBridge/bridge_data.json")
NAGI = "http://localhost:7842"  # Windows HttpListener 只认 localhost 这个 Host


def get_json(url: str, timeout: float = 5.0) -> tuple[bool, object]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:  # noqa: BLE001 - 对照脚本，错误要原样显示
        return False, f"{type(error).__name__}: {error}"


def short(value: object, limit: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", default=str(DEFAULT_BRIDGE))
    parser.add_argument("--nagi", default=NAGI)
    args = parser.parse_args()

    print("=" * 70)
    print("NagiBridge ↔ StardewMCPBridge 只读对照")
    print("=" * 70)

    ok, status = get_json(f"{args.nagi}/status")
    print(f"\n[1] NagiBridge /status  {'OK' if ok else '失败'}")
    if not ok:
        print(f"    {status}")
        print("    → 游戏没开 / Mod 没加载 / 端口没起来。先在游戏里载入存档再试。")
    else:
        print(f"    {short(status, 300)}")

    nagi_state: object = None
    ok_state, nagi_state = get_json(f"{args.nagi}/state")
    print(f"\n[2] NagiBridge /state   {'OK' if ok_state else '失败'}")
    if ok_state and isinstance(nagi_state, dict):
        keys = sorted(nagi_state.keys())
        print(f"    字段 {len(keys)} 个: {', '.join(keys[:18])}{'…' if len(keys) > 18 else ''}")
        player = nagi_state.get("player") or nagi_state
        for key in ("position", "tile", "location", "inventory", "health", "stamina"):
            if isinstance(player, dict) and key in player:
                print(f"    player.{key}: {short(player[key])}")
    elif not ok_state:
        print(f"    {nagi_state}")

    bridge_path = Path(args.bridge)
    print(f"\n[3] MCP bridge_data.json：{bridge_path}")
    mcp = None
    if bridge_path.exists():
        try:
            mcp = json.loads(bridge_path.read_text("utf-8"))
            print(f"    OK，顶层字段: {', '.join(sorted(mcp.keys())[:15])}")
            player = mcp.get("player") or {}
            for key in ("position", "tile", "location", "name"):
                if key in player:
                    print(f"    player.{key}: {short(player[key])}")
        except Exception as error:  # noqa: BLE001
            print(f"    读取失败: {error}")
    else:
        print("    文件不存在（MCP Mod 没加载，或游戏没进存档）")

    print("\n[4] 对照结论")
    if ok_state and mcp is not None:
        print("    两边都有数据 → 可以逐字段比：重点看仓库/商店/节日相关的端点")
        print("    建议再单独 curl 这些 Nagi 独有的只读端点：")
        for path in ("/surroundings?radius=6", "/map", "/scan", "/menu", "/machines", "/animals", "/festival", "/minigame/state", "/chat/history"):
            good, body = get_json(f"{args.nagi}{path}")
            size = len(json.dumps(body, ensure_ascii=False)) if good else 0
            print(f"      {path:24s} {'OK ' + str(size) + 'B' if good else '不可用  ' + short(body, 50)}")
    else:
        print("    数据不全，先保证游戏在跑且两个 Mod 都加载")
    print("=" * 70)
    return 0 if (ok_state or mcp is not None) else 1


if __name__ == "__main__":
    sys.exit(main())
