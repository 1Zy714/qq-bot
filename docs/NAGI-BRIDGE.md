# NagiBridge → MCP 薄封装

把星露谷 [NagiBridge](https://github.com/anqinou-art/NagiBridge) 的 HTTP 端点包成 MCP 工具，
让机器人像用原来的 `stardew` 工具一样用它。

```
QQ → DSH(可选) → 机器人模型 → MCP(nagi) → HTTP :7842 → NagiBridge Mod → 游戏
```

## 为什么加这一层

| | NagiBridge | 原 StardewMCPBridge |
|---|---|---|
| 动作端点 | **40+**（买/卖/收/存/箱/制作/机器/动物/节日/小游戏/睡觉/钓鱼 bot） | 25 个工具（含 6 模式、同伴 NPC） |
| 通信 | HTTP 请求-响应 | 文件桥（30 tick 写一次） |
| 编译 | 免编译，Release DLL | 需要 .NET 编译 |
| 独有 | 商店/节日/小游戏/机器/动物 | **同伴 NPC、天气、6 种模式** |

结论：Nagi 是更好的「手」，MCP 的同伴和天气继续留着，两者**并存不冲突**。

## 安装

1. 下载 [Release](https://github.com/anqinou-art/NagiBridge/releases/latest) 的 `NagiBridge.zip`
2. 解压到 `Stardew Valley/Mods/`（得到 `Mods/NagiBridge/NagiBridge.dll`）
3. 用 SMAPI 启动游戏并载入存档，日志里应出现：
   `[NagiBridge] NagiBridge HTTP server started on port 7842`

> WSL 里装：写 `Mods` 会被沙箱拦（工作区之外），用 Windows PowerShell 解压：
> ```powershell
> Expand-Archive -LiteralPath '\\wsl.localhost\Ubuntu-24.04\tmp\NagiBridge.zip' `
>   -DestinationPath 'Z:\STEAAM\steamapps\common\Stardew Valley\Mods' -Force
> ```

## 从 WSL 访问：必须用 localhost

Windows 的 HttpListener 只认 `localhost` 这个 Host：

```bash
curl http://localhost:7842/status                    # ✅
curl -H 'Host: localhost:7842' http://127.0.0.1:7842/status   # ✅
curl http://127.0.0.1:7842/status                    # ❌ 400 Invalid Hostname
```

## 工具清单（28 个）

只读：`status` `state` `surroundings` `map` `scan` `menu` `machines` `animals`
`festival` `minigame_state` `chat_history`

动作：`move` `stop` `face` `select` `tool_use` `use` `interact` `harvest`
`store` `buy` `sell` `craft` `sleep` `wakeup` `warp` `key` `chat_send`

**有意不暴露**（在 `tools/nagi_mcp.py` 的 `call_tool` 之外）：
`/cheat`（改规则）、`/position`（瞬移，会和寻路打架）、`/queue`（批量，一条错指令连锁执行）。

## 配置

`config.json` 的 `mcp_servers` 追加：

```json
{
  "name": "nagi",
  "command": "/home/lzy959/.venvs/qqbot/bin/python",
  "args": ["/home/lzy959/qq-bot/tools/nagi_mcp.py"],
  "env": {"NAGI_BASE": "http://localhost:7842", "NAGI_TIMEOUT": "20"},
  "timeout": 30
}
```

- 工具名会以 `mcp__nagi__*` 出现，和 `mcp__stardew__*` 并存
- `mcp_bridge.ensure_servers` 用**第一个** server 的 `STARDEW_BRIDGE_PATH` 判断游戏是否在跑，
  所以 nagi 必须排在 stardew **之后**（否则游戏没开也会尝试连）
- 改完重启机器人：`bash ~/qq-bot/svc.sh restart official`

## 验证

```bash
# 1) 离线单测（不需要游戏）
python3 test_nagi_mcp.py

# 2) 服务器直连
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python3 tools/nagi_mcp.py

# 3) 走机器人自己的 MCP 客户端
~/.venvs/qqbot/bin/python - <<'PY'
import asyncio, json, sys
sys.path.insert(0, "/home/lzy959/qq-bot")
import mcp_bridge
async def main():
    cfg = json.load(open("/home/lzy959/qq-bot/config.json"))
    nagi = [s for s in cfg["mcp_servers"] if s.get("name") == "nagi"]
    print(await mcp_bridge.init_servers(nagi))
    print(await mcp_bridge.call_tool("mcp__nagi__status"))
    await mcp_bridge.shutdown()
asyncio.run(main())
PY
```

## 排障

| 症状 | 原因 |
|---|---|
| `400 Invalid Hostname` | 用了 127.0.0.1，改 `localhost` |
| `连不上 NagiBridge` | 游戏没开 / 存档没载入 / Mod 没加载 |
| 工具列表里没有 `mcp__nagi__*` | config 里排在了 stardew 之前，或改完没重启 |
| 动作返回 `ok:false` | 看返回里的 `reason`/`observed`；`sleep` 尤其要看 `state` |
| `move` 总是失败 | 目标不可达；先 `surroundings` 看 `passable` |
| 商店买不了 | 先 `menu` 看当前菜单和物品 id/价格 |

## 后续

`game_loop.py` 现在还在用 `mcp__stardew__*`。迁移路径：把候选动作生成里的
`harvest/water/clear` 换成 `mcp__nagi__harvest` / `tool_use` + `surroundings`，
可以顺便去掉文件桥的同步延迟。
