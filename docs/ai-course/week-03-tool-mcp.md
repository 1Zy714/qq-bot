# Week 3 · 工具调用与 MCP

> 目标：明白「模型不会自己发请求」，并**亲手写一个 MCP 工具**接进机器人。
> 相关：[../LEARN-AI-BASICS.md](../LEARN-AI-BASICS.md) 第 6 节、`tools/nagi_mcp.py`、`mcp_bridge.py`。

---

## Day 1 · 读现成的工具服务器

```bash
sed -n '45,90p' tools/nagi_mcp.py      # request / as_ok
sed -n '135,170p' tools/nagi_mcp.py    # call_tool：工具名 → HTTP
sed -n '209,246p' tools/nagi_mcp.py    # handle：JSON-RPC 分发
```

**要能回答**：MCP 的四个基本方法是什么？
（`initialize` → `notifications/initialized` → `tools/list` → `tools/call`）

---

## Day 2 · 读客户端与门控

```bash
sed -n '199,230p' mcp_bridge.py   # init_servers：拉起子进程 + 握手
sed -n '34,48p' mcp_bridge.py     # health_ok：健康检查
```

**要能回答**：工具名在机器人里长什么样？（`mcp__<server>__<tool>`）

---

## Day 3 · 写 `tools/time_mcp.py`

新建文件，内容如下（纯标准库，可直接跑）：

```python
#!/usr/bin/env python3
"""最小 MCP 工具：提供一个 now 工具，返回当前时间。"""
import json
import sys
import time

TOOLS = [{
    "name": "now",
    "description": "返回服务器当前时间",
    "inputSchema": {"type": "object", "properties": {}},
}]


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "time-mcp", "version": "1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        text = time.strftime("%Y-%m-%d %H:%M:%S") if name == "now" else f"未知工具：{name}"
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": text}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"未知方法：{method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = handle(msg)
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
```

**测**：

```bash
printf '%s\n' \
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' \
'{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"now","arguments":{}}}' \
| python3 tools/time_mcp.py
```

**预期**：第 3 行返回当前时间字符串。

---

## Day 4 · 注册进机器人

编辑 `dsh-qq.patch.yml`，在 **insert 列表里**追加：

```yaml
- insert:
    - id: mcp-time
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: time
        transport: stdio
        command: "/home/lzy959/qq-bot/tools/time_mcp.py"
        args: []
        failOnStartupError: false
```

> 注意：新条目必须在 `insert:` 下，直接写 `- id:` 会报 `entry not found`。

重启：

```bash
bash svc.sh restart official
```

---

## Day 5 · 验证与加固

私聊问「现在几点」，看是否调用 `mcp__time__now`。

加固：把 `handle` 里的异常包一层 `try/except`，任何异常都返回文本，**不能让服务器挂掉**。

---

## 验收清单

- [ ] `time_mcp.py` 用 pipe 能跑通四个方法
- [ ] 机器人能调用 `now` 工具并回答时间
- [ ] 工具有错误处理（未知工具返回可读文本）
- [ ] 提交：`git commit -m "course: week-03 自写 MCP 工具"`

## 常见坑

| 现象 | 原因 |
|---|---|
| 工具列表为空 | `tools/list` 返回结构不对（必须 `result.tools`） |
| 机器人说没有工具 | patch 没生效 / 没重启 / insert 位置错 |
| 子进程起不来 | `command` 路径不对或没执行权限（`chmod +x`） |
