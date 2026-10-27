#!/usr/bin/env python3
"""离线单测 Nagi MCP 薄封装：工具清单、参数映射、错误降级。

不需要游戏、不连 NagiBridge——`request` 被替换成假的返回，所以一秒跑完。

用法::

    python3 test_nagi_mcp.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("nagi_mcp", HERE / "tools" / "nagi_mcp.py")
assert spec and spec.loader
nagi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nagi)

FAILURES: list[str] = []
CALLS: list[tuple[str, str, object]] = []
# 保存真实实现：后面的测试会把 nagi.request 换成假的，这里留一份原件
REAL_REQUEST = nagi.request


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


def fake_request(method: str, path: str, payload: dict | None = None) -> str:
    CALLS.append((method, path, payload))
    return json.dumps({"ok": True, "echo": path}, ensure_ascii=False)


def test_tools() -> None:
    print("\n[1] 工具清单")
    names = [t["name"] for t in nagi.TOOLS]
    check("工具不重名", len(names), len(set(names)))
    check("包含核心动作", {"move", "harvest", "sleep", "store", "buy"} <= set(names), True)
    check("暴露了 28 个", len(names), 28)
    check("没有暴露 cheat", "cheat" in names, False)
    check("没有暴露 position 瞬移", "position" in names, False)
    for item in nagi.TOOLS:
        assert item["inputSchema"]["type"] == "object", item["name"]
        assert "description" in item and item["description"], item["name"]


def test_mapping() -> None:
    print("\n[2] 参数映射")
    nagi.request = fake_request  # type: ignore[assignment]

    nagi.call_tool("status", {})
    check("status → GET /status", CALLS[-1], ("GET", "/status", None))

    nagi.call_tool("surroundings", {"radius": 99})
    check("radius 被夹到 20", CALLS[-1][1], "/surroundings?radius=20")

    nagi.call_tool("surroundings", {})
    check("radius 默认 8", CALLS[-1][1], "/surroundings?radius=8")

    nagi.call_tool("move", {"x": 12, "y": 9})
    check("move 参数", CALLS[-1], ("POST", "/move", {"x": 12, "y": 9}))

    nagi.call_tool("tool_use", {})
    check("tool_use 留空用 current", CALLS[-1][2], {"name": "current"})

    nagi.call_tool("buy", {"id": "24", "count": 2})
    check("buy 缺省价格", CALLS[-1][2], {"id": "24", "count": 2})

    nagi.call_tool("store", {"x": 3, "y": 4, "name": ""})
    check("store 丢掉空参数", CALLS[-1][2], {"x": 3, "y": 4})

    nagi.call_tool("warp", {"location": "UndergroundMine5"})
    check("warp 不强塞 x/y", CALLS[-1][2], {"location": "UndergroundMine5"})


def test_errors() -> None:
    print("\n[3] 错误处理")
    # 打一个必然关闭的端口，走 request() 的真实错误分支（不依赖游戏）
    real_base = nagi.BASE
    nagi.BASE = "http://127.0.0.1:9"
    text = REAL_REQUEST("GET", "/status")
    nagi.BASE = real_base
    check("连不上给可读提示", "连不上 NagiBridge" in text, True)
    check("提示里带排查方向", "SMAPI" in text, True)

    nagi.request = lambda m, p, payload=None: '{"ok": false, "reason": "no bed"}'  # type: ignore
    text = nagi.call_tool("sleep", {})
    check("ok:false 标成未成功", text.startswith("操作未成功"), True)

    reply = nagi.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                         "params": {"name": "nope", "arguments": {}}})
    check("未知工具不崩", "未知工具" in reply["result"]["content"][0]["text"], True)

    reply = nagi.handle({"jsonrpc": "2.0", "id": 10, "method": "who/knows"})
    check("未知方法回 -32601", reply["error"]["code"], -32601)

    check("通知不回包", nagi.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}), None)


def test_protocol() -> None:
    print("\n[4] 协议")
    reply = nagi.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("协议版本", reply["result"]["protocolVersion"], "2024-11-05")
    check("声明 tools 能力", "tools" in reply["result"]["capabilities"], True)
    reply = nagi.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    check("tools/list 有 28 个", len(reply["result"]["tools"]), 28)


def main() -> int:
    print("=" * 56)
    print("Nagi MCP 薄封装 离线单测")
    test_tools()
    test_mapping()
    test_errors()
    test_protocol()
    print("\n" + "=" * 56)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
