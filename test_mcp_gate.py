#!/usr/bin/env python3
"""离线单测 MCP 健康门：游戏没开就不挂载。

不需要真的 MCP 子进程，只测「健康检查」这一层判断。

用法::

    python3 test_mcp_gate.py
"""

from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mcp_bridge  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    if actual == expected:
        print(f"  ✓ {name}")
        return
    FAILURES.append(name)
    print(f"  ✗ {name}\n      期望 {expected!r}\n      实际 {actual!r}")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args) -> None:  # 安静
        pass


def main() -> int:
    print("=" * 50)
    print("MCP 健康门 离线单测")

    print("\n[1] 空地址视为不需要检查")
    check("空串放行", mcp_bridge.health_ok(""), True)

    print("\n[2] 死端口 → 不就绪")
    check("关闭端口返回 False", mcp_bridge.health_ok("http://127.0.0.1:9", timeout=1.0), False)

    print("\n[3] 活服务 → 就绪")
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        check(f"200 端口 {port} 返回 True",
              mcp_bridge.health_ok(f"http://127.0.0.1:{port}/status"), True)
    finally:
        server.shutdown()

    print("\n" + "=" * 50)
    if FAILURES:
        print(f"✗ {len(FAILURES)} 项失败：")
        for name in FAILURES:
            print(f"    - {name}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
