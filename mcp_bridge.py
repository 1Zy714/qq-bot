#!/usr/bin/env python3
"""MCP 客户端桥接（手写 JSON-RPC 版）。

为什么不用官方 mcp SDK：
    它的 ``stdio_client`` 基于 anyio task group，要求上下文的 enter/exit
    在**同一个 task** 内完成；在常驻 asyncio 服务里跨方法持有连接时，
    退出会报 ``Attempted to exit cancel scope in a different task``，或者直接卡死。
    而 MCP over stdio 的本质就是「按行分隔的 JSON-RPC」，自己实现只有几十行，
    生命周期 100% 由我们掌控，也不会被 SDK 的内部调度问题拖住。

协议流程：
    1. initialize                 → 建立会话
    2. notifications/initialized  → 通知（无 id，不回响应）
    3. tools/list                 → 拿工具清单
    4. tools/call                 → 调具体工具
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

PROTOCOL_VERSION = "2024-11-05"


class McpServer:
    """一个 MCP server 子进程的 JSON-RPC 客户端。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        # 合并当前环境：node 需要 PATH 等基础变量
        self.env = {**os.environ, **(env or {})}
        # 若命令是 Windows 可执行文件（WSL interop），必须用 WSLENV 把自定义
        # 环境变量**透传**给 Windows 进程 —— 直接塞进 subprocess 的 env 是无效的，
        # Windows 侧收到的一律是 undefined。
        if command.lower().endswith((".exe", ".bat", ".cmd")):
            passed = ":".join(k for k in (env or {}) if k)
            if passed:
                existing = self.env.get("WSLENV", "")
                self.env["WSLENV"] = f"{existing}:{passed}" if existing else passed
        self.timeout = timeout
        self.proc: asyncio.subprocess.Process | None = None
        self.tools: list[dict[str, Any]] = []
        self.error: str | None = None
        self._id = 0

    async def connect(self) -> bool:
        """启动子进程并完成 MCP 握手。失败返回 False，不抛。"""
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.env,
            )
            resp = await self._rpc(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "qq-bot", "version": "1.0"},
                },
            )
            if "error" in resp:
                self.error = f"initialize 失败：{resp['error']}"
                return False
            await self._notify("notifications/initialized", {})
            resp = await self._rpc("tools/list", {})
            raw_tools = (resp.get("result") or {}).get("tools") or []
            self.tools = [self._to_openai_tool(t) for t in raw_tools]
            self.error = None
            return True
        except Exception as error:  # noqa: BLE001
            self.error = f"{type(error).__name__}: {error}"
            await self.close()
            return False

    # ---- JSON-RPC 底层 ----

    async def _write(self, message: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("MCP 子进程未就绪")
        self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _read_line(self) -> dict[str, Any]:
        if not self.proc or not self.proc.stdout:
            raise RuntimeError("MCP 子进程未就绪")
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=self.timeout)
        if not line:
            raise RuntimeError("MCP 子进程已关闭输出")
        return json.loads(line.decode("utf-8", "replace"))

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        await self._write(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        )
        # 跳过服务端主动推的通知（没有 id 的消息）
        for _ in range(10):
            message = await self._read_line()
            if message.get("id") == self._id:
                return message
        return {"error": {"message": "未收到匹配的 RPC 响应"}}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    # ---- 工具 ----

    def _to_openai_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": f"mcp__{self.name}__{tool.get('name')}",
                "description": str(tool.get("description") or "")[:400]
                or f"MCP 工具 {tool.get('name')}",
                "parameters": tool.get("inputSchema")
                or {"type": "object", "properties": {}},
            },
        }

    async def call(self, raw_name: str, args: dict[str, Any] | None = None) -> str:
        if not self.proc:
            return f"MCP[{self.name}] 未连接"
        try:
            resp = await self._rpc(
                "tools/call", {"name": raw_name, "arguments": args or {}}
            )
        except Exception as error:  # noqa: BLE001
            return f"MCP[{self.name}] 调用失败：{type(error).__name__}: {error}"
        if "error" in resp:
            return f"MCP[{self.name}] 错误：{resp['error']}"
        result = resp.get("result") or {}
        parts: list[str] = []
        for item in result.get("content") or []:
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        joined = "\n".join(parts).strip()
        return (joined or "（工具无返回内容）")[:2000]

    async def close(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.proc = None


# ---- 全局注册表 ----

SERVERS: dict[str, McpServer] = {}
_INITED = False


async def init_servers(configs: list[dict[str, Any]] | None) -> dict[str, str]:
    """按配置建立连接（幂等）。返回 {server: 状态} 便于日志排查。"""
    global _INITED
    result: dict[str, str] = {}
    if not configs:
        return result
    if _INITED:
        return {name: "已初始化" for name in SERVERS}
    _INITED = True
    for cfg in configs:
        name = str(cfg.get("name") or "mcp")
        server = McpServer(
            name=name,
            command=str(cfg.get("command") or ""),
            args=[str(a) for a in (cfg.get("args") or [])],
            env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            timeout=float(cfg.get("timeout") or 60.0),
        )
        ok = await server.connect()
        if ok:
            SERVERS[name] = server
            result[name] = f"已连接，{len(server.tools)} 个工具"
        else:
            result[name] = f"连接失败：{server.error}"
    return result


def _to_wsl_path(path: str) -> str:
    """Windows 路径（``Z:/x/y``）→ WSL 路径（``/mnt/z/x/y``）。"""
    if len(path) > 2 and path[1] == ":":
        drive = path[0].lower()
        rest = path[2:].lstrip("\\/").replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return path


def _exists_any(path: str) -> bool:
    """文件存在性检查：原样与 WSL 映射路径都试一遍。

    配置里给 MCP server 的是 **Windows 路径**（因为 server 用 Windows node 跑），
    但本进程在 WSL 里，直接 ``os.path.exists`` 会误判成"文件不存在"。
    """
    return os.path.exists(path) or os.path.exists(_to_wsl_path(path))


async def ensure_servers(configs: list[dict[str, Any]] | None) -> dict[str, str]:
    """**按需连接**：只有「游戏真的在跑」时才连 MCP。

    判断依据：配置里第一个 server 的 ``STARDEW_BRIDGE_PATH`` 文件是否存在。
    游戏没开 → 返回空，聊天只带内置工具（不干扰性格）。
    游戏开了 → 连接一次，之后复用。
    """
    if not configs or SERVERS:
        return {name: "已连接" for name in SERVERS} if SERVERS else {}
    first = configs[0] if configs else {}
    bridge = str((first.get("env") or {}).get("STARDEW_BRIDGE_PATH") or "").strip()
    if bridge and not _exists_any(bridge):
        return {}  # 游戏没跑，不连
    return await init_servers(configs)


def tool_schemas() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for server in SERVERS.values():
        out.extend(server.tools)
    return out


def has_tool(full_name: str) -> bool:
    return any(
        item["function"]["name"] == full_name
        for server in SERVERS.values()
        for item in server.tools
    )


async def call_tool(full_name: str, args: dict[str, Any] | None = None) -> str:
    for server in SERVERS.values():
        for item in server.tools:
            if item["function"]["name"] == full_name:
                raw = full_name[len(f"mcp__{server.name}__"):]
                return await server.call(raw, args or {})
    return f"未知的 MCP 工具：{full_name}"


async def shutdown() -> None:
    for server in list(SERVERS.values()):
        await server.close()
    SERVERS.clear()
