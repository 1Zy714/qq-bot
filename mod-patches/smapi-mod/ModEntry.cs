using System;
using System.IO;
using System.Linq;
using System.Text.Json;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.GameData.Characters;

namespace StardewMCPBridge
{
    public class ModEntry : Mod
    {
        private string bridgePath;
        private string actionDir;
        private BotManager botManager;
        private Texture2D companion1Portrait;
        private Texture2D companion2Portrait;
        private Texture2D companion1Sprite;
        private Texture2D companion2Sprite;
        private Texture2D dafeiyuPortrait;
        private Texture2D dafeiyuSprite;

        public override void Entry(IModHelper helper)
        {
            this.botManager = new BotManager(this.Monitor, helper);
            this.bridgePath = Path.Combine(helper.DirectoryPath, "bridge_data.json");
            this.actionDir = Path.Combine(helper.DirectoryPath, "actions");

            helper.Events.GameLoop.GameLaunched += this.OnGameLaunched;
            helper.Events.GameLoop.UpdateTicked += this.OnUpdateTicked;
            helper.Events.GameLoop.DayStarted += this.OnDayStarted;
            helper.Events.GameLoop.DayEnding += this.OnDayEnding;
            helper.Events.GameLoop.TimeChanged += this.OnTimeChanged;
            helper.Events.GameLoop.ReturnedToTitle += this.OnReturnedToTitle;
            helper.Events.GameLoop.Saving += this.OnSaving;
            helper.Events.GameLoop.Saved += this.OnSaved;
            helper.Events.Content.AssetRequested += this.OnAssetRequested;

            this.Monitor.Log("Stardew MCP Bridge initialized. Content pipeline registered.", LogLevel.Debug);
        }

        private void OnGameLaunched(object sender, GameLaunchedEventArgs e)
        {
            this.companion1Portrait = this.Helper.ModContent.Load<Texture2D>("assets/Companion1_portrait.png");
            this.companion2Portrait = this.Helper.ModContent.Load<Texture2D>("assets/Companion2_portrait.png");
            this.companion1Sprite = this.Helper.ModContent.Load<Texture2D>("assets/Companion1_sprite.png");
            this.companion2Sprite = this.Helper.ModContent.Load<Texture2D>("assets/Companion2_sprite.png");
            // 大肥鱼（Dafeiyu）—— 由 AI 创建的自定义角色
            this.dafeiyuPortrait = this.Helper.ModContent.Load<Texture2D>("assets/Dafeiyu_portrait.png");
            this.dafeiyuSprite = this.Helper.ModContent.Load<Texture2D>("assets/Dafeiyu_sprite.png");
            this.Monitor.Log("Bridge online. Portraits and sprites loaded. Waiting for world.", LogLevel.Info);
        }

        private void OnAssetRequested(object sender, AssetRequestedEventArgs e)
        {
            // Inject portrait textures
            if (e.NameWithoutLocale.IsEquivalentTo("Portraits/Companion1"))
            {
                e.LoadFrom(() => this.companion1Portrait, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Portraits/Companion2"))
            {
                e.LoadFrom(() => this.companion2Portrait, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Portraits/Dafeiyu"))
            {
                e.LoadFrom(() => this.dafeiyuPortrait, AssetLoadPriority.Exclusive);
            }
            // Custom sprite sheets for walking animation
            else if (e.NameWithoutLocale.IsEquivalentTo("Characters/Companion1"))
            {
                e.LoadFrom(() => this.companion1Sprite, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Characters/Companion2"))
            {
                e.LoadFrom(() => this.companion2Sprite, AssetLoadPriority.Exclusive);
            }
            else if (e.NameWithoutLocale.IsEquivalentTo("Characters/Dafeiyu"))
            {
                e.LoadFrom(() => this.dafeiyuSprite, AssetLoadPriority.Exclusive);
            }
            // Inject NPC data so the game considers us valid
            else if (e.NameWithoutLocale.IsEquivalentTo("Data/Characters"))
            {
                e.Edit(asset =>
                {
                    var data = asset.AsDictionary<string, CharacterData>();

                    if (!data.Data.ContainsKey("Companion1"))
                    {
                        data.Data["Companion1"] = new CharacterData
                        {
                            DisplayName = "Companion1",
                            HomeRegion = "Town",
                        };
                    }

                    if (!data.Data.ContainsKey("Companion2"))
                    {
                        data.Data["Companion2"] = new CharacterData
                        {
                            DisplayName = "Companion2",
                            HomeRegion = "Town",
                        };
                    }

                    if (!data.Data.ContainsKey("Dafeiyu"))
                    {
                        data.Data["Dafeiyu"] = new CharacterData
                        {
                            DisplayName = "Dafeiyu",
                            HomeRegion = "Farm",
                        };
                    }
                });
            }
        }

        private void OnUpdateTicked(object sender, UpdateTickedEventArgs e)
        {
            if (!Context.IsWorldReady) return;

            // AI ticks every frame (60/sec) for responsive combat, pathfinding, stuck detection
            this.botManager.Update();

            // Bridge I/O every 30 ticks (~0.5s) to avoid thrashing disk
            if (e.IsMultipleOf(30))
            {
                this.SyncGameState();
                this.ProcessActions();
            }
        }

        private void OnTimeChanged(object sender, TimeChangedEventArgs e)
        {
            // Safety net: if it's 2:00 AM (forced pass-out time), signal bots ready
            if (e.NewTime >= 2600)
                this.botManager.SignalAllSleepReady();
        }

        private void OnDayEnding(object sender, DayEndingEventArgs e)
        {
            // Signal all bot farmers as sleep-ready so the game doesn't deadlock
            this.botManager.SignalAllSleepReady();
            this.Monitor.Log("Day ending: bot farmers signaled sleep ready", LogLevel.Debug);
        }

        private void OnDayStarted(object sender, DayStartedEventArgs e)
        {
            this.botManager.OnDayStarted();
            this.Monitor.Log("New day: companion stamina restored", LogLevel.Info);
        }

        private void OnSaving(object sender, SavingEventArgs e)
        {
            // 自定义 NPC 无法被存档序列化，保存前先摘掉
            this.botManager.DetachForSave();
        }

        private void OnSaved(object sender, SavedEventArgs e)
        {
            this.botManager.ReattachAfterSave();
        }

        private void OnReturnedToTitle(object sender, ReturnedToTitleEventArgs e)
        {
            this.botManager.Cleanup();
            this.Monitor.Log("Returned to title: companions cleaned up", LogLevel.Info);
        }

        private void SyncGameState()
        {
            try
            {
                var state = new
                {
                    time = Game1.timeOfDay,
                    day = Game1.dayOfMonth,
                    season = Game1.currentSeason,
                    weather = Game1.isLightning ? "storm" : Game1.isRaining ? "rain" : Game1.isSnowing ? "snow" : Game1.isDebrisWeather ? "windy" : "sunny",
                    location = Game1.currentLocation?.Name,
                    player = new
                    {
                        name = Game1.player.Name,
                        health = Game1.player.health,
                        stamina = Game1.player.Stamina,
                        money = Game1.player.Money,
                        position = new { x = Game1.player.Position.X, y = Game1.player.Position.Y }
                    },
                    companions = this.botManager.GetBotStatus(),
                    npcs = Game1.currentLocation?.characters.Select(c => new {
                        name = c.Name,
                        position = new { x = c.Position.X, y = c.Position.Y }
                    }).ToList(),
                    syncedAt = DateTime.UtcNow.ToString("o")
                };

                string json = JsonSerializer.Serialize(state, new JsonSerializerOptions { WriteIndented = true });
                // Atomic write: temp file then rename, so MCP never reads partial JSON
                string tmpPath = this.bridgePath + ".tmp";
                File.WriteAllText(tmpPath, json);
                File.Move(tmpPath, this.bridgePath, true);
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"Bridge Sync Error: {ex.Message}", LogLevel.Error);
            }
        }

        private void ProcessActions()
        {
            try
            {
                if (!Directory.Exists(this.actionDir))
                    return;

                // Drain the queue oldest-first. Each command is its own file named
                // <timestamp>-<seq>.json, so ordinal filename sort is chronological.
                string[] files = Directory.GetFiles(this.actionDir, "*.json");
                if (files.Length == 0)
                    return;
                Array.Sort(files, StringComparer.Ordinal);

                foreach (string file in files)
                {
                    string json;
                    try
                    {
                        json = File.ReadAllText(file);
                        // Delete immediately so each command is consumed exactly once,
                        // even if handling below throws.
                        File.Delete(file);
                    }
                    catch (Exception ex)
                    {
                        this.Monitor.Log($"Action read error ({Path.GetFileName(file)}): {ex.Message}", LogLevel.Error);
                        continue;
                    }

                    if (string.IsNullOrWhiteSpace(json))
                        continue;

                    try
                    {
                        this.HandleAction(json);
                    }
                    catch (Exception ex)
                    {
                        this.Monitor.Log($"Action handling error: {ex.Message}", LogLevel.Error);
                    }
                }
            }
            catch (Exception ex)
            {
                this.Monitor.Log($"Action Processing Error: {ex.Message}", LogLevel.Error);
            }
        }

        private void HandleAction(string json)
        {
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;

            if (!root.TryGetProperty("actionType", out var actionType))
                return;

            // Route to bot manager for companion actions
            this.botManager.ProcessAction(json);

            // Handle chat separately (not a companion action)
            if (actionType.GetString() == "chat")
            {
                if (root.TryGetProperty("metadata", out var meta) &&
                    meta.TryGetProperty("message", out var msg))
                {
                    string text = msg.GetString();
                    if (!string.IsNullOrEmpty(text))
                    {
                        Game1.chatBox?.addMessage(text, Microsoft.Xna.Framework.Color.Gold);
                        this.Monitor.Log($"Chat sent: {text}", LogLevel.Info);
                    }
                }
            }
        }
    }
}
