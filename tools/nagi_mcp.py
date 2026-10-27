#!/usr/bin/env python3
"""NagiBridge → MCP 薄封装：把星露谷的 HTTP 端点暴露成 MCP 工具。

为什么是「薄」的：NagiBridge 已经是一个完整的动作服务器（40+ 端点、请求-响应、
无共享文件），MCP 只负责把它的 REST 端点翻译成工具，**不重新实现任何游戏逻辑**。
好处是机器人侧零改动——`mcp_bridge.py` 把 `mcp__nagi__*` 和原来的
`mcp__stardew__*` 一视同仁。

协议：MCP over stdio，换行分隔 JSON-RPC（和 `mcp_bridge.py` 手写客户端对齐，
版本 ``2024-11-05``）。只用标准库，不依赖 mcp SDK。

环境变量：
- ``NAGI_BASE``：默认 ``http://localhost:7842``。**必须用 localhost**——
  Windows 的 HttpListener 只认这个 Host，用 127.0.0.1 会回 400 Invalid Hostname。
- ``NAGI_TIMEOUT``：单请求超时秒数，默认 20。

安全边界（有意不暴露）：
- ``/cheat`` 系列：改游戏规则
- ``/position``：坐标瞬移（会和寻路打架）
- ``/queue``：批量命令（一条错误指令能连锁执行）

用法（人工自测）::

    echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 tools/nagi_mcp.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
BASE = os.environ.get("NAGI_BASE", "http://localhost:7842").rstrip("/")
TIMEOUT = float(os.environ.get("NAGI_TIMEOUT", "20"))
MAX_TEXT = 20000  # 工具回给模型的文本上限，避免一次扫描把上下文吃光


# ---------- HTTP ----------

def request(method: str, path: str, payload: dict[str, Any] | None = None) -> str:
    """调 NagiBridge，返回给模型看的文本（成功是 JSON，失败是可读错误）。"""
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        return f"NagiBridge {method} {path} 返回 HTTP {error.code}：{detail}"
    except Exception as error:  # noqa: BLE001 - 游戏没开/端口没起时给可读原因
        return (
            f"连不上 NagiBridge（{BASE}{path}）：{type(error).__name__}: {error}\n"
            "检查：游戏是否用 SMAPI 启动、Mods 里有没有 NagiBridge、存档是否已载入。"
        )
    return body[:MAX_TEXT]


def as_ok(result: str) -> str:
    """把裸 JSON 包一层，方便模型一眼看出成功与否。"""
    try:
        parsed = json.loads(result)
    except ValueError:
        return result
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        return "操作未成功：" + result[:MAX_TEXT]
    return result


# ---------- 工具定义 ----------

def tool(name: str, description: str, props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
        },
    }


XY = {"x": {"type": "integer", "description": "格子坐标 X"}, "y": {"type": "integer", "description": "格子坐标 Y"}}

TOOLS: list[dict[str, Any]] = [
    # --- 只读 ---
    tool("status", "NagiBridge 是否就绪：worldReady、是否多人。动作前先看这个。", {}),
    tool("state", "玩家完整状态：位置、血量、体力、金钱、当前工具、朝向、背包、时间。", {}),
    tool("surroundings", "扫描玩家周围格子（含可通行性）、NPC、怪物、其他农夫。",
         {"radius": {"type": "integer", "description": "半径，默认 8，最大 20"}}),
    tool("map", "当前地图：建筑、传送点、动物。", {}),
    tool("scan", "扫描当前地图全部物体（数量多，先看 count 再决定要不要细看）。", {}),
    tool("menu", "当前打开的菜单详情：类型、对话、选项、商店物品、按钮坐标。商店购物前必看。", {}),
    tool("machines", "当前地图所有机器及状态（empty/processing/ready）。", {}),
    tool("animals", "动物详情：今天摸过没、好感、心情、饱食度、产物是否可收。", {}),
    tool("festival", "当前节日信息。", {}),
    tool("minigame_state", "小游戏状态（JotPK/CalicoJack 等）及 bot 是否在跑。", {}),
    tool("chat_history", "游戏内聊天面板的历史消息。", {}),

    # --- 移动 / 手持 ---
    tool("move", "寻路走到指定格子（游戏内自动寻路，遇阻会失败）。", XY, ["x", "y"]),
    tool("stop", "停止当前移动。", {}),
    tool("face", "转向：0=上 1=右 2=下 3=左。", {"direction": {"type": "integer", "description": "0上 1右 2下 3左"}}, ["direction"]),
    tool("select", "选择背包里的物品作为手持。", {"name": {"type": "string", "description": "物品名，如 Hoe / Watering Can"}}, ["name"]),

    # --- 动作 ---
    tool("tool_use", "使用当前或指定工具（对着面前/指定格）。", {"name": {"type": "string", "description": "工具名；留空用当前"}}),
    tool("use", "使用手持物品（放种子、放家具这类）。", {}),
    tool("interact", "与面前的格子/物体交互。", {}),
    tool("harvest", "收割成熟作物。", {}),
    tool("store", "把物品存进箱子。", {**XY, "name": {"type": "string", "description": "物品名；留空存手持"}}),
    tool("buy", "购买（需先打开商店菜单，价格可留空）。",
         {"id": {"type": "string"}, "count": {"type": "integer"}, "price": {"type": "integer"}}, ["id"]),
    tool("sell", "把物品放进发货箱出售。", {"name": {"type": "string"}, "count": {"type": "integer"}}),
    tool("craft", "制作物品。", {"name": {"type": "string"}, "count": {"type": "integer"}}, ["name"]),
    tool("sleep", "回自己的床睡觉（自动找床）。注意 ok:true 不等于新一天开始，要看 state。", {}),
    tool("wakeup", "起床。", {}),
    tool("warp", "传送到地图/矿洞层（UndergroundMine5 这种写法）。",
         {"location": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}}, ["location"]),
    tool("key", "模拟按键：confirm/ok/cancel/skip/F1-F12。", {"key": {"type": "string"}}, ["key"]),
    tool("chat_send", "以玩家身份在游戏里发一条聊天消息。", {"message": {"type": "string"}}, ["message"]),
]


def call_tool(name: str, args: dict[str, Any]) -> str:
    """工具名 → HTTP 调用。参数校验交给 NagiBridge，这里只做拼装。"""
    if name == "status":
        return request("GET", "/status")
    if name == "state":
        return request("GET", "/state")
    if name == "surroundings":
        radius = int(args.get("radius") or 8)
        radius = max(1, min(20, radius))
        return request("GET", f"/surroundings?radius={radius}")
    if name == "map":
        return request("GET", "/map")
    if name == "scan":
        return request("GET", "/scan")
    if name == "menu":
        return request("GET", "/menu")
    if name == "machines":
        return request("GET", "/machines")
    if name == "animals":
        return request("GET", "/animals")
    if name == "festival":
        return request("GET", "/festival")
    if name == "minigame_state":
        return request("GET", "/minigame/state")
    if name == "chat_history":
        return request("GET", "/chat/history")
    if name == "move":
        return as_ok(request("POST", "/move", {"x": int(args["x"]), "y": int(args["y"])}))
    if name == "stop":
        return as_ok(request("POST", "/stop", {}))
    if name == "face":
        return as_ok(request("POST", "/face", {"direction": int(args["direction"])}))
    if name == "select":
        return as_ok(request("POST", "/select", {"name": str(args["name"])}))
    if name == "tool_use":
        return as_ok(request("POST", "/tool", {"name": str(args.get("name") or "current")}))
    if name == "use":
        return as_ok(request("POST", "/use", {}))
    if name == "interact":
        return as_ok(request("POST", "/interact", {}))
    if name == "harvest":
        return as_ok(request("POST", "/harvest", {}))
    if name == "store":
        payload = {k: args[k] for k in ("x", "y", "name") if args.get(k) not in (None, "")}
        return as_ok(request("POST", "/store", payload))
    if name == "buy":
        payload = {"id": str(args["id"]), "count": int(args.get("count") or 1)}
        if args.get("price") is not None:
            payload["price"] = int(args["price"])
        return as_ok(request("POST", "/buy", payload))
    if name == "sell":
        payload = {k: args[k] for k in ("name", "count") if args.get(k) not in (None, "")}
        return as_ok(request("POST", "/sell", payload))
    if name == "craft":
        return as_ok(request("POST", "/craft", {"name": str(args["name"]), "count": int(args.get("count") or 1)}))
    if name == "sleep":
        return as_ok(request("POST", "/sleep", {}))
    if name == "wakeup":
        return as_ok(request("POST", "/wakeup", {}))
    if name == "warp":
        payload = {"location": str(args["location"])}
        for key in ("x", "y"):
            if args.get(key) is not None:
                payload[key] = int(args[key])
        return as_ok(request("POST", "/warp", payload))
    if name == "key":
        return as_ok(request("POST", "/key", {"key": str(args["key"])}))
    if name == "chat_send":
        return as_ok(request("POST", "/chat", {"message": str(args["message"])}))
    return f"未知工具：{name}"


# ---------- JSON-RPC 主循环 ----------

def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        return None  # 通知（notifications/initialized 之类），不回
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nagi-bridge", "version": "1.0.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            text = call_tool(name, args if isinstance(args, dict) else {})
        except Exception as error:  # noqa: BLE001 - 单次失败不能弄死服务器
            text = f"工具执行失败：{type(error).__name__}: {error}"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"未知方法：{method}"},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
