# 游戏后端抽象：同伴 NPC / 玩家自动驾驶（2026-10-27）

`game_loop.py` 不再直接调某一种游戏接口，而是通过 `game_backend.py` 的两个后端：

```
game_loop.py（策略层，逻辑不变）
   ├── StardewBackend ── MCP 文件桥 ── 同伴 NPC（原行为）
   └── NagiBackend   ── HTTP :7842 ── 玩家本人（NagiBridge）
```

配置：`game_loop.backend = "stardew" | "nagi"`。

## 一、Nagi 字段 → 循环认识的形状

| 循环需要 | Nagi 提供 |
|---|---|
| `companions[0].tile` | `state.player.x/y`（合成一个 companion） |
| `companions[0].stamina` | `stamina / maxStamina × 100` |
| `cropReady` | `harvestable` |
| `waterState` | 没浇水且没熟 → 0，其余 → 1 |
| `breakable` / `objType` | `object`（Weeds/Stone/Twig）或 `resource`（LargeStump/LargeLog…） |

## 二、动作映射

| 循环动作 | Nagi |
|---|---|
| 移动 | `POST /move`（先 `face` 再动作） |
| 收割 | `POST /harvest?radius=4`（批量） |
| 浇水 | `select Watering Can` → `tool` |
| 清杂草/石/枝 | `select Scythe / Pickaxe / Axe` → `tool` |
| 换模式 | farm=本地循环；fish=`/fishbot on`；idle=`/fishbot off`+`/stop` |

## 三、动作核验（不采信「上报成功」）

动作后等工具动画落地（`verify_delay_seconds`，默认 0.8s），再观察那一格：

- 收割 → 不再 `cropReady`
- 浇水 → `waterState == 1`
- 清理 → 不再 `breakable` 或格子消失

没变化一律按失败记账。

## 四、三个实测踩出来的坑

1. **斜对角打错格**：工具只作用于正前方一格；斜对角硬打会「返回 ok 但没变化」。
   现在斜对角先走一步、站到正邻格再动手。
2. **多击资源**：树桩/原木/巨石一刀掉不了。现在连续挥（`max_swings` 默认 6），
   每下计一次动作预算，挥完没掉才记失败。
3. **游戏失焦即暂停**：世界时间不走时 `/warp`、`/move` 会「成功但没发生」。
   实测时间戳停在 6:00 不动就是它，点一下游戏窗口立刻恢复。

## 五、预算（防跑飞）

`game_loop.budget`：`max_actions`（80）/ `max_model_calls`（40）/ `max_minutes`（45），
任一超限就收工并汇报。配合「同一格失败 2 次排除」，不会卡死在一处空转。

## 六、实测

```
[game] clear_weed  (61,18) 成功（已核验）
[game] clear_stone (62,19) 成功（已核验）
[game] 模式 → idle / 已汇报到私聊：天黑了，收工。
```

## 七、能力差异

| | 同伴（stardew） | 玩家（nagi） |
|---|---|---|
| 主人能否同时玩 | ✅ | ❌ AI 独占玩家角色 |
| 天气 | ✅ | ❌ |
| mine / follow | ✅ | ❌ 自动降级 idle |
| 商店/节日/小游戏 | ❌ | ✅ |

相关提交：`bb277ce`（抽象）、`085adc6`（战略层兼容 DSH）、`672a0c0`（核验+预算+多击）。
