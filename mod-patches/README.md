# StardewMCPBridge 本地改动（相对上游 amarisaster/StardewValley-MCP）

本目录收录我们对 SMAPI mod 的修改源码，便于回溯与重建。
原项目：https://github.com/amarisaster/StardewValley-MCP

## 改动清单

### 1. `ModEntry.cs`

| 改动 | 原因 |
|---|---|
| 注册 `Portraits/Dafeiyu`、`Characters/Dafeiyu` | 自定义角色需要三处资源注册（头像/走路图/NPC 数据），只注册名字不注册资源 → **角色不可见** |
| `Data/Characters` 增加 `Dafeiyu` 条目 | 游戏要把这个角色当成合法 NPC |
| 监听 `GameLoop.Saving` / `GameLoop.Saved` | 存档前把伙伴 NPC 从场景摘掉，存完挂回 —— 否则 **存档直接失败** |

### 2. `BotManager.cs`

| 改动 | 原因 |
|---|---|
| `spawn` 支持 `name` 参数，默认 `Dafeiyu`，只生成 1 个角色 | 上游硬编码生成 `Companion1`/`Companion2` |
| `move_to` 改成「每帧直接推进像素」 | **`PathFindController` 对 NPC 完全无效**（实测开阔地 20 秒 0 像素），改用位置累加 |
| 新增 `StepNpcToward` / `StepPlayerToward` | 伙伴与玩家的推进逻辑 |
| **新增 `move_player` / `stop_player` / `player_use_tool` 动作** | **直接驱动 `Game1.player`** —— 不受窗口焦点、输入法、overlay 影响（键盘模拟实测全部失效） |
| 新增 `DetachForSave` / `ReattachAfterSave` | 配合 `ModEntry` 修存档序列化崩溃 |
| 帧号驱动：`Sprite.CurrentFrame = (2 - facing + 4) % 4 * 4 + walkFrame` | mod 原先从不设置 `CurrentFrame`，导致**角色永远停在第 0 行**（一直看到同一个朝向） |

### 3. `CompanionAI.cs`

| 改动 | 原因 |
|---|---|
| `PathTo` 改成记录目标而非设置 controller | 同 `move_to`，controller 无效 |
| 新增 `StepManualMove` + `manualTarget` 字段 | 每帧推进；`Tick` 里悬空时回到站立帧 |

## 关键常量

```
贴图行序：0=朝下(正面) 1=朝右 2=朝上(背面) 3=朝左
朝向编号：0=上 1=右 2=下 3=左
换算：row = (2 - facing + 4) % 4
```

## 重新编译

```bash
# Windows 侧
cd C:\Users\12451\StardewValley-MCP\smapi-mod
C:\Users\12451\dotnet\dotnet.exe build -c Release -p:EnableModDeploy=false
# 游戏关闭时部署
copy bin\Release\net6.0\StardewMCPBridge.dll "Z:\STEAAM\steamapps\common\Stardew Valley\Mods\StardewMCPBridge\"
```

**注意**：游戏运行时 dll 被占用，必须先退出游戏才能复制。
