using System;
using System.Collections.Generic;
using System.Linq;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewValley;
using StardewValley.Pathfinding;
using StardewValley.Tools;

namespace StardewMCPBridge
{
    public class BotManager
    {
        private readonly IMonitor monitor;
        private readonly IModHelper helper;
        private readonly Dictionary<string, CompanionAI> companions = new Dictionary<string, CompanionAI>();

        /// <summary>
        /// 存档期间被临时摘下来的伙伴（name → 原所在场景）。
        /// 自定义 NPC 没有无参构造函数，游戏序列化时会抛
        /// "cannot be serialized because it does not have a parameterless constructor"
        /// 导致整个存档写入失败，所以保存前必须先把她从场景里摘掉。
        /// </summary>
        private readonly Dictionary<string, GameLocation> detachedForSave = new Dictionary<string, GameLocation>();

        /// <summary>
        /// 由 move_player 设置的「玩家角色」移动目标（tile 坐标）。
        /// 直接驱动 Game1.player —— 不受窗口焦点、输入法、overlay 影响，
        /// 比键盘模拟可靠得多。玩家自己按键时会和它抢位置，需要时用 stop_player 停下。
        /// </summary>
        private Vector2? playerTarget = null;

        /// <summary>Per-companion last command results, written to bridge data.</summary>
        private readonly Dictionary<string, object> commandResults = new Dictionary<string, object>();

        /// <summary>
        /// 由 move_to 设置的「手动移动目标」（tile 坐标）。
        /// 不走 PathFindController：实测它对 NPC 完全不生效（角色一像素都不动），
        /// 所以这里改成每帧直接推进位置，和 BotFarmer.tryToMoveInDirection 同一套思路。
        /// </summary>
        private readonly Dictionary<string, Vector2> manualTargets = new Dictionary<string, Vector2>();

        /// <summary>走路动画相位（0-3 循环），用于手动驱动 Sprite 帧号。</summary>
        private int walkPhase = 0;

        /// <summary>
        /// 星露谷角色贴图的行序是「朝下/朝右/朝上/朝左」(0/1/2/3)，
        /// 而 FacingDirection 是「上/右/下/左」(0/1/2/3)，两者顺序不同，要换算。
        /// mod 原先从不设置 Sprite.CurrentFrame，导致永远停在第 0 行——
        /// 所以第 0 行必须是「朝下（正面）」，否则会一直看到后脑勺。
        /// </summary>
        private static int FacingToRow(int facing)
        {
            return (2 - facing + 4) % 4;
        }

        /// <summary>设置站立帧（不动时用）。</summary>
        private static void SetStandingFrame(NPC npc)
        {
            if (npc?.Sprite == null) return;
            npc.Sprite.CurrentFrame = FacingToRow(npc.FacingDirection) * 4;
        }

        public BotManager(IMonitor monitor, IModHelper helper)
        {
            this.monitor = monitor;
            this.helper = helper;
        }

        public void SpawnBot(string name, string type)
        {
            if (!Context.IsWorldReady) return;
            if (this.companions.ContainsKey(name)) return;

            this.monitor.Log($"Spawning {name} with shadow farmer", LogLevel.Info);

            try
            {
                // Remove any game-auto-created NPCs with this name from ALL locations
                // (Data/Characters injection causes the game to auto-spawn them)
                foreach (var loc in Game1.locations)
                {
                    var dupes = loc.characters.Where(c => c.Name == name).ToList();
                    foreach (var dupe in dupes)
                    {
                        loc.characters.Remove(dupe);
                        this.monitor.Log($"Removed duplicate {name} from {loc.Name}", LogLevel.Debug);
                    }
                }

                var portrait = this.helper.ModContent.Load<Microsoft.Xna.Framework.Graphics.Texture2D>($"assets/{name}_portrait.png");

                var spawnPos = Game1.player.Position;
                if (name == "Companion2") spawnPos += new Vector2(64, 0);
                else if (name == "Companion1") spawnPos += new Vector2(-64, 0);

                var botNpc = new CompanionNPC(
                    new AnimatedSprite($"Characters\\{name}", 0, 16, 32),
                    spawnPos,
                    Game1.player.currentLocation.Name,
                    2, name, portrait, false
                );
                botNpc.displayName = name;
                Game1.player.currentLocation.addCharacter(botNpc);
                SetStandingFrame(botNpc);
                this.monitor.Log($"{name} placed at pixel ({spawnPos.X},{spawnPos.Y}), tile ({spawnPos.X / 64f:F1},{spawnPos.Y / 64f:F1})", LogLevel.Info);

                var companionFarmer = new CompanionFarmer(botNpc, name, this.monitor, this.helper);
                var ai = new CompanionAI(companionFarmer, this.monitor);
                ai.Mode = CompanionMode.Follow;

                this.companions.Add(name, ai);
                this.monitor.Log($"{name} spawned with shadow farmer at {Game1.player.currentLocation.Name}", LogLevel.Info);
            }
            catch (Exception ex)
            {
                this.monitor.Log($"Failed to spawn {name}: {ex.Message}\n{ex.StackTrace}", LogLevel.Error);
            }
        }

        public void Update()
        {
            foreach (var kvp in this.companions)
            {
                try
                {
                    kvp.Value.Tick();
                    // 手动移动目标：每帧直接推进位置（PathFindController 对 NPC 不生效）
                    if (this.manualTargets.TryGetValue(kvp.Key, out var target))
                    {
                        this.StepNpcToward(kvp.Key, kvp.Value.Companion.Visual, target);
                    }
                }
                catch (Exception ex)
                {
                    this.monitor.Log($"{kvp.Key}: AI tick error (recovering): {ex.Message}", LogLevel.Error);
                }
            }

            // 玩家角色（Game1.player）的手动推进
            if (this.playerTarget.HasValue)
            {
                try
                {
                    this.StepPlayerToward(this.playerTarget.Value);
                }
                catch (Exception ex)
                {
                    this.playerTarget = null;
                    this.monitor.Log($"player step error: {ex.Message}", LogLevel.Warn);
                }
            }
        }

        /// <summary>
        /// 朝目标 tile 直接推进（每帧一次，速度 = npc.speed）。
        /// 不走 PathFindController —— 实测那玩意对 NPC 完全不生效。
        /// 这里用纯像素推进：只朝差值更大的那个轴走，保证一定能接近目标。
        /// </summary>
        private void StepNpcToward(string name, NPC npc, Vector2 targetTile)
        {
            if (npc == null || npc.currentLocation == null)
            {
                this.manualTargets.Remove(name);
                return;
            }

            Vector2 cur = npc.Tile;
            float dx = targetTile.X - cur.X;
            float dy = targetTile.Y - cur.Y;

            if (Math.Abs(dx) < 0.5f && Math.Abs(dy) < 0.5f)
            {
                this.manualTargets.Remove(name);
                SetStandingFrame(npc);   // 到站了，回到站立帧
                return;
            }

            float speed = Math.Max(1f, (float)npc.speed);
            Vector2 step;
            if (Math.Abs(dx) >= Math.Abs(dy))
            {
                step = new Vector2(Math.Sign(dx) * speed, 0f);
                npc.FacingDirection = step.X > 0f ? 1 : 3;
            }
            else
            {
                step = new Vector2(0f, Math.Sign(dy) * speed);
                npc.FacingDirection = step.Y > 0f ? 2 : 0;
            }

            npc.Position = npc.Position + step;

            // 游戏不会为「手动位移」的 NPC 播走路动画，这里自己驱动帧号
            if (npc.Sprite != null)
            {
                this.walkPhase = (this.walkPhase + 1) % 4;
                int[] cycle = { 0, 1, 2, 1 };
                npc.Sprite.CurrentFrame = FacingToRow(npc.FacingDirection) * 4 + cycle[this.walkPhase];
            }
        }

        /// <summary>
        /// 朝目标 tile 推进玩家角色（像素级到位 + 碰撞检测 + 撞墙换轴）。
        /// 直接驱动 Game1.player.Position，不经过输入层，所以不受焦点/输入法影响。
        /// </summary>
        private void StepPlayerToward(Vector2 targetTile)
        {
            var player = Game1.player;
            var location = player?.currentLocation;
            if (player == null || location == null)
            {
                this.playerTarget = null;
                return;
            }

            // 目标格中心（像素）
            Vector2 want = new Vector2(targetTile.X * 64f + 32f, targetTile.Y * 64f + 32f);
            Vector2 diff = want - player.Position;

            if (Math.Abs(diff.X) <= 8f && Math.Abs(diff.Y) <= 8f)
            {
                this.playerTarget = null;
                this.monitor.Log($"Player arrived ({targetTile.X},{targetTile.Y})", LogLevel.Trace);
                return;
            }

            float speed = Math.Max(2f, (float)player.speed);

            // 差值大的轴优先，被挡就换另一个轴
            bool horizFirst = Math.Abs(diff.X) >= Math.Abs(diff.Y);
            int primary = horizFirst ? (diff.X > 0f ? 1 : 3) : (diff.Y > 0f ? 2 : 0);
            int alt = horizFirst ? (diff.Y > 0f ? 2 : 0) : (diff.X > 0f ? 1 : 3);

            if (this.TryStepPlayer(player, location, primary, speed)) return;
            if (this.TryStepPlayer(player, location, alt, speed)) return;

            // 两个轴都被挡：放弃目标，交给上层重新决策
            this.playerTarget = null;
            this.monitor.Log($"Player blocked at ({player.Tile.X},{player.Tile.Y}) towards ({targetTile.X},{targetTile.Y})", LogLevel.Debug);
        }

        /// <summary>朝指定方向推进一小步；落脚格不可走则返回 false。</summary>
        private bool TryStepPlayer(Farmer player, GameLocation location, int dir, float speed)
        {
            Vector2 next = player.Position;
            switch (dir)
            {
                case 0: next.Y -= speed; break;
                case 1: next.X += speed; break;
                case 2: next.Y += speed; break;
                case 3: next.X -= speed; break;
            }

            int tx = (int)(next.X / 64f);
            int ty = (int)(next.Y / 64f);
            if (!location.isTileOnMap(new Vector2(tx, ty)))
            {
                return false;
            }
            var loc = new xTile.Dimensions.Location(tx, ty);
            if (!location.isTilePassable(loc, Game1.viewport))
            {
                return false;
            }

            player.Position = next;
            player.FacingDirection = dir;
            return true;
        }

        /// <summary>Get expanded per-companion status for bridge data (includes surroundings in Player mode).</summary>
        public object GetBotStatus()
        {
            var statuses = new List<object>();
            foreach (var kvp in this.companions)
            {
                var ai = kvp.Value;
                var companion = ai.Companion;
                var pos = companion.Visual.Position;
                var tile = companion.Visual.Tile;

                var status = new Dictionary<string, object>
                {
                    ["name"] = kvp.Key,
                    ["position"] = new { x = pos.X, y = pos.Y },
                    ["tile"] = new { x = (int)tile.X, y = (int)tile.Y },
                    ["location"] = companion.Visual.currentLocation?.Name ?? "unknown",
                    ["status"] = ai.GetStatusDescription(),
                    ["mode"] = ai.Mode.ToString().ToLower(),
                    ["stamina"] = companion.GetStaminaPercent(),
                    ["health"] = companion.Shadow.health,
                    ["maxHealth"] = companion.Shadow.maxHealth,
                    ["autoCombat"] = ai.AutoCombat
                };

                // Include surroundings scan in Player mode (the companion's "eyes")
                if (ai.Mode == CompanionMode.Player)
                {
                    var scan = companion.GetSurroundings(8);
                    if (scan != null)
                    {
                        status["surroundings"] = new
                        {
                            tiles = scan.Tiles.Select(t => new
                            {
                                x = t.X, y = t.Y,
                                passable = t.Passable,
                                water = t.IsWater,
                                terrain = t.Terrain,
                                crop = t.CropName,
                                cropReady = t.CropReady,
                                waterState = t.WaterState,
                                obj = t.ObjectName,
                                objType = t.ObjectType,
                                breakable = t.Breakable,
                                interactable = t.Interactable
                            }),
                            monsters = scan.Monsters.Select(m => new
                            {
                                name = m.Name,
                                x = m.X, y = m.Y,
                                health = m.Health,
                                maxHealth = m.MaxHealth
                            }),
                            npcs = scan.Npcs.Select(n => new
                            {
                                name = n.Name,
                                x = n.X, y = n.Y
                            })
                        };
                    }

                    status["inventory"] = companion.GetInventoryData();

                    // Include last command result if any
                    if (this.commandResults.TryGetValue(kvp.Key, out var result))
                        status["lastCommandResult"] = result;
                }

                statuses.Add(status);
            }
            return statuses;
        }

        // ======================
        // SLEEP SIGNAL
        // ======================

        /// <summary>Signal all bot farmers as sleep-ready. Call when host goes to bed.</summary>
        public void SignalAllSleepReady()
        {
            foreach (var kvp in this.companions)
            {
                kvp.Value.Companion.SignalSleepReady();
                this.monitor.Log($"{kvp.Key}: Signaled sleep ready", LogLevel.Debug);
            }
        }

        // ======================
        // DAY TRANSITION
        // ======================

        /// <summary>Restore companion stamina on new day; reset state.</summary>
        public void OnDayStarted()
        {
            foreach (var kvp in this.companions)
            {
                var ai = kvp.Value;
                var shadow = ai.Companion.Shadow;
                shadow.WakeUp();

                // Reset AI state for fresh day (keep Player mode if set)
                if (ai.Mode != CompanionMode.Player)
                    ai.Mode = CompanionMode.Follow;

                // If companion was in a mine/dungeon, warp to farm
                var loc = ai.Companion.Visual.currentLocation;
                if (loc is StardewValley.Locations.MineShaft || loc?.Name == "VolcanoDungeon")
                {
                    ai.Companion.WarpTo("Farm", 64, 15);
                    this.monitor.Log($"{kvp.Key}: Was in {loc.Name} at day end 鈥?warped to Farm", LogLevel.Info);
                }

                this.monitor.Log($"{kvp.Key}: New day 鈥?stamina restored, mode: {ai.Mode}", LogLevel.Info);
            }
        }

        /// <summary>Clean up companions on return to title.</summary>
        /// <summary>存档前把伙伴 NPC 从场景摘掉，避免序列化失败。</summary>
        public void DetachForSave()
        {
            this.detachedForSave.Clear();
            foreach (var kvp in this.companions)
            {
                try
                {
                    var npc = kvp.Value.Companion.Visual;
                    var loc = npc?.currentLocation;
                    if (npc != null && loc != null)
                    {
                        loc.characters.Remove(npc);
                        this.detachedForSave[kvp.Key] = loc;
                    }
                }
                catch (Exception ex)
                {
                    this.monitor.Log($"{kvp.Key}: detach for save failed: {ex.Message}", LogLevel.Warn);
                }
            }
            if (this.detachedForSave.Count > 0)
            {
                this.monitor.Log($"Detached {this.detachedForSave.Count} companion(s) for save", LogLevel.Trace);
            }
        }

        /// <summary>存档结束后把伙伴 NPC 挂回原场景。</summary>
        public void ReattachAfterSave()
        {
            foreach (var kvp in this.detachedForSave)
            {
                try
                {
                    if (!this.companions.TryGetValue(kvp.Key, out var ai)) continue;
                    var npc = ai.Companion.Visual;
                    if (npc == null) continue;
                    if (!kvp.Value.characters.Contains(npc))
                    {
                        kvp.Value.characters.Add(npc);
                    }
                }
                catch (Exception ex)
                {
                    this.monitor.Log($"{kvp.Key}: reattach after save failed: {ex.Message}", LogLevel.Warn);
                }
            }
            this.detachedForSave.Clear();
        }

        public void Cleanup()
        {
            foreach (var kvp in this.companions)
            {
                var npc = kvp.Value.Companion.Visual;
                npc.currentLocation?.characters.Remove(npc);
            }
            this.companions.Clear();
            this.commandResults.Clear();
            this.monitor.Log("All companions cleaned up", LogLevel.Info);
        }

        // ======================
        // ACTION PROCESSING
        // ======================

        public void ProcessAction(string json)
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (!root.TryGetProperty("actionType", out var actionType)) return;

            string action = actionType.GetString();

            // Check for companion-targeted commands (Player mode direct control)
            if (root.TryGetProperty("companion", out var companionProp))
            {
                string companionName = companionProp.GetString();
                if (this.companions.TryGetValue(companionName, out var targetAi))
                {
                    this.ProcessCompanionCommand(action, root, companionName, targetAi);
                    return;
                }
                else
                {
                    this.monitor.Log($"Unknown companion: {companionName}", LogLevel.Warn);
                    return;
                }
            }

            // Global commands (existing behavior)
            switch (action)
            {
                case "move_player":
                    {
                        int px = root.GetProperty("x").GetInt32();
                        int py = root.GetProperty("y").GetInt32();
                        this.playerTarget = new Vector2(px, py);
                        this.monitor.Log($"Player: walking to ({px},{py})", LogLevel.Info);
                        break;
                    }

                case "stop_player":
                    this.playerTarget = null;
                    this.monitor.Log("Player: stopped", LogLevel.Info);
                    break;

                case "player_use_tool":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var player = Game1.player;
                        if (player == null) break;
                        Vector2 diff = new Vector2(tx, ty) - player.Tile;
                        if (Math.Abs(diff.X) > Math.Abs(diff.Y))
                            player.FacingDirection = diff.X > 0f ? 1 : 3;
                        else
                            player.FacingDirection = diff.Y > 0f ? 2 : 0;
                        player.BeginUsingTool();
                        this.monitor.Log($"Player: use tool at ({tx},{ty})", LogLevel.Info);
                        break;
                    }

                case "spawn": var spawnName = "Dafeiyu"; if (root.TryGetProperty("name", out var nameProp)) { var n = nameProp.GetString(); if (!string.IsNullOrWhiteSpace(n)) spawnName = n; } this.SpawnBot(spawnName, "Guard");
                    this.SetAllMode(CompanionMode.Follow);
                    this.monitor.Log("Spawned and following", LogLevel.Info);
                    break;

                case "follow":
                    this.SetAllMode(CompanionMode.Follow);
                    break;

                case "stay":
                    this.SetAllMode(CompanionMode.Idle);
                    break;

                case "farm":
                    this.SetAllMode(CompanionMode.Farm);
                    break;

                case "mine":
                    this.SetAllMode(CompanionMode.Mine);
                    break;

                case "fish":
                    this.SetAllMode(CompanionMode.Fish);
                    break;

                case "water_all":
                    this.WaterAll();
                    break;

                case "harvest_all":
                    this.HarvestAll();
                    break;

                // Set individual companion mode
                case "set_mode":
                    if (root.TryGetProperty("target", out var target) &&
                        root.TryGetProperty("mode", out var modeStr))
                    {
                        string targetName = target.GetString();
                        if (this.companions.TryGetValue(targetName, out var ai) &&
                            Enum.TryParse<CompanionMode>(modeStr.GetString(), true, out var mode))
                        {
                            ai.Mode = mode;
                            this.monitor.Log($"Set {targetName} to {mode}", LogLevel.Info);
                        }
                    }
                    break;

                // Warp companions to a specific location
                case "warp":
                    if (root.TryGetProperty("location", out var loc) &&
                        root.TryGetProperty("x", out var wx) &&
                        root.TryGetProperty("y", out var wy))
                    {
                        foreach (var kvp in this.companions)
                            kvp.Value.Companion.WarpTo(loc.GetString(), wx.GetInt32(), wy.GetInt32());
                    }
                    break;

                // Single-tile actions (from stardew_action tool)
                case "water":
                case "harvest":
                case "clear":
                case "hoe":
                    if (root.TryGetProperty("x", out var ax) && root.TryGetProperty("y", out var ay))
                    {
                        var location = Game1.player.currentLocation;
                        if (location != null)
                        {
                            var tile = new Vector2(ax.GetInt32(), ay.GetInt32());
                            bool ok = action switch
                            {
                                "water" => CompanionActions.WaterTile(location, tile, this.monitor),
                                "harvest" => CompanionActions.HarvestTile(location, tile, this.monitor),
                                "clear" => CompanionActions.ClearDebris(location, tile, this.monitor),
                                "hoe" => CompanionActions.HoeTile(location, tile, this.monitor),
                                _ => false
                            };
                            this.monitor.Log($"Action {action} at ({ax.GetInt32()},{ay.GetInt32()}): {(ok ? "success" : "failed")}", LogLevel.Info);
                        }
                    }
                    break;
            }
        }

        // ======================
        // DIRECT COMPANION COMMANDS (Player mode)
        // ======================

        private void ProcessCompanionCommand(string action, System.Text.Json.JsonElement root, string companionName, CompanionAI ai)
        {
            var companion = ai.Companion;
            bool success = false;
            string detail = "";

            try
            {
                switch (action)
                {
                    // --- Movement ---
                    case "move_to":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var npc = companion.Visual;
                        // PathFindController 对 NPC 完全不生效（实测一像素不动），
                        // 改用「每帧直接推进」的手动目标，由 Update() 里的 StepNpcToward 驱动。
                        npc.controller = null;
                        this.manualTargets[companion.Name] = new Vector2(tx, ty);
                        companion.SyncFromNpc();
                        success = true;
                        detail = $"Walking to ({tx},{ty})";
                        break;
                    }

                    case "warp_to":
                    {
                        string loc = root.GetProperty("location").GetString();
                        int wx = root.GetProperty("x").GetInt32();
                        int wy = root.GetProperty("y").GetInt32();
                        // 传送会覆盖位置，清掉手动移动目标免得它继续往旧目标走
                        this.manualTargets.Remove(companion.Name);
                        companion.WarpTo(loc, wx, wy);
                        success = true;
                        detail = $"Warped to {loc} ({wx},{wy})";
                        break;
                    }

                    case "face_direction":
                    {
                        int dir = root.GetProperty("direction").GetInt32(); // 0=up,1=right,2=down,3=left
                        if (dir < 0 || dir > 3)
                        {
                            detail = $"Invalid direction: {dir} (must be 0-3)";
                            break;
                        }
                        companion.Visual.FacingDirection = dir;
                        companion.Shadow.FacingDirection = dir;
                        SetStandingFrame(companion.Visual);
                        success = true;
                        detail = $"Facing {new[] { "up", "right", "down", "left" }[dir]}";
                        break;
                    }

                    // --- Tools ---
                    case "use_tool":
                    {
                        string toolName = root.GetProperty("tool").GetString();
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var tile = new Vector2(tx, ty);

                        Type toolType = toolName.ToLower() switch
                        {
                            "pickaxe" => typeof(Pickaxe),
                            "axe" => typeof(Axe),
                            "hoe" => typeof(Hoe),
                            "wateringcan" or "watering_can" => typeof(WateringCan),
                            "sword" or "weapon" => typeof(MeleeWeapon),
                            _ => null
                        };

                        if (toolType != null)
                        {
                            success = companion.UseToolAt(tile, toolType);
                            detail = success ? $"Used {toolName} at ({tx},{ty})" : $"Failed to use {toolName}";
                        }
                        else
                        {
                            detail = $"Unknown tool: {toolName}";
                        }
                        break;
                    }

                    case "attack":
                    {
                        success = companion.AttackNearbyMonsters();
                        detail = success ? "Attacked nearby monster" : "No monsters in range";
                        break;
                    }

                    // --- Interact ---
                    case "interact":
                    {
                        int tx = root.GetProperty("x").GetInt32();
                        int ty = root.GetProperty("y").GetInt32();
                        var tile = new Vector2(tx, ty);
                        var location = companion.Visual.currentLocation ?? Game1.player.currentLocation;

                        // Try harvest
                        if (location.terrainFeatures.TryGetValue(tile, out var feature)
                            && feature is StardewValley.TerrainFeatures.HoeDirt dirt
                            && dirt.crop != null && dirt.readyForHarvest())
                        {
                            success = CompanionActions.HarvestTile(location, tile, this.monitor);
                            detail = success ? $"Harvested at ({tx},{ty})" : "Harvest failed";
                        }
                        // Try pick up item
                        else if (location.objects.TryGetValue(tile, out var obj))
                        {
                            if (obj.Name != null && (obj.Name.Contains("Ladder") || obj.Name.Contains("Shaft")))
                            {
                                // Descend mine
                                if (location is StardewValley.Locations.MineShaft shaft)
                                {
                                    int nextLevel = shaft.mineLevel + 1;
                                    string nextName = "UndergroundMine" + nextLevel;
                                    var nextLoc = Game1.getLocationFromName(nextName)
                                        ?? StardewValley.Locations.MineShaft.GetMine(nextName);
                                    if (nextLoc != null)
                                    {
                                        companion.WarpTo(nextLoc.Name, 6, 6);
                                        success = true;
                                        detail = $"Descended to mine level {nextLevel}";
                                    }
                                    else
                                    {
                                        detail = $"Can't find mine level {nextLevel}";
                                    }
                                }
                            }
                            else if (obj is StardewValley.Objects.Chest chest)
                            {
                                // Return chest contents as the command result
                                var items = chest.Items.Where(i => i != null).Select(i => new
                                {
                                    name = i.DisplayName ?? i.Name,
                                    stack = i.Stack,
                                    qualifiedId = i.QualifiedItemId
                                }).ToList();
                                success = true;
                                detail = $"Chest at ({tx},{ty}): {items.Count} items";
                                this.commandResults[companionName] = new { action, success, detail, items };
                                this.monitor.Log($"{companionName}: {detail}", LogLevel.Info);
                                return; // early return 鈥?custom result
                            }
                            else
                            {
                                // Try to check/interact with the object using companion's shadow farmer
                                success = obj.checkForAction(companion.Shadow);
                                detail = success ? $"Interacted with {obj.DisplayName}" : $"Can't interact with {obj.DisplayName}";
                            }
                        }
                        else
                        {
                            detail = $"Nothing to interact with at ({tx},{ty})";
                        }
                        break;
                    }

                    // --- Fishing ---
                    case "cast_fishing_rod":
                    {
                        success = ai.StartFishing();
                        detail = success ? "Cast fishing rod" : "Failed to cast";
                        break;
                    }

                    // --- Combat toggle ---
                    case "set_auto_combat":
                    {
                        bool enabled = root.GetProperty("enabled").GetBoolean();
                        ai.AutoCombat = enabled;
                        success = true;
                        detail = $"Auto-combat {(enabled ? "enabled" : "disabled")}";
                        break;
                    }

                    // --- Inventory ---
                    case "eat_item":
                    {
                        int slot = root.TryGetProperty("slot", out var slotProp) ? slotProp.GetInt32() : -1;
                        var items = companion.Shadow.Items;
                        StardewValley.Object food = null;

                        if (slot >= 0 && slot < items.Count && items[slot] is StardewValley.Object o1 && o1.Edibility > 0)
                            food = o1;
                        else
                            food = items.FirstOrDefault(i => i is StardewValley.Object o && o.Edibility > 0) as StardewValley.Object;

                        if (food != null)
                        {
                            try
                            {
                                companion.Shadow.eatObject(food);
                                success = true;
                                detail = $"Ate {food.DisplayName}";
                            }
                            catch (Exception eatEx)
                            {
                                // eatObject may access UI elements on headless farmer
                                // Fall back to direct stamina/health restoration
                                companion.Shadow.Stamina = Math.Min(companion.Shadow.MaxStamina, companion.Shadow.Stamina + food.Edibility);
                                companion.Shadow.health = Math.Min(companion.Shadow.maxHealth, companion.Shadow.health + (int)(food.Edibility * 0.4f));

                                // Consume the food item (eatObject normally handles this)
                                food.Stack--;
                                if (food.Stack <= 0)
                                {
                                    int idx = items.IndexOf(food);
                                    if (idx >= 0) items[idx] = null;
                                }

                                success = true;
                                detail = $"Ate {food.DisplayName} (manual restore, eatObject failed: {eatEx.Message})";
                            }
                        }
                        else
                        {
                            detail = "No edible items in inventory";
                        }
                        break;
                    }

                    default:
                        detail = $"Unknown companion command: {action}";
                        this.monitor.Log($"{companionName}: {detail}", LogLevel.Warn);
                        break;
                }
            }
            catch (Exception ex)
            {
                detail = $"Command error: {ex.Message}";
                this.monitor.Log($"{companionName}: {action} failed: {ex.Message}", LogLevel.Error);
            }

            this.commandResults[companionName] = new { action, success, detail };
            this.monitor.Log($"{companionName}: {action} 鈥?{detail}", LogLevel.Info);
        }

        // ======================
        // GLOBAL HELPERS
        // ======================

        private void SetAllMode(CompanionMode mode)
        {
            foreach (var ai in this.companions.Values)
                ai.Mode = mode;
            this.monitor.Log($"All companions set to {mode}", LogLevel.Info);
        }

        private void WaterAll()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            int count = 0;
            foreach (var pair in location.terrainFeatures.Pairs)
            {
                if (pair.Value is StardewValley.TerrainFeatures.HoeDirt dirt
                    && dirt.crop != null && dirt.state.Value != 1)
                {
                    if (CompanionActions.WaterTile(location, pair.Key, this.monitor))
                        count++;
                }
            }
            this.monitor.Log($"Watered {count} tiles", LogLevel.Info);
        }

        private void HarvestAll()
        {
            var location = Game1.player.currentLocation;
            if (location == null) return;
            int count = 0;
            foreach (var pair in location.terrainFeatures.Pairs.ToList())
            {
                if (pair.Value is StardewValley.TerrainFeatures.HoeDirt dirt
                    && dirt.crop != null && dirt.readyForHarvest())
                {
                    if (CompanionActions.HarvestTile(location, pair.Key, this.monitor))
                        count++;
                }
            }
            this.monitor.Log($"Harvested {count} crops", LogLevel.Info);
        }
    }
}

