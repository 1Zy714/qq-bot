# QQ 机器人文档索引

> 最后更新：**2026-10-17**　共 12 篇正文档 + 若干外部文档
> 从这里开始找，不用一篇篇翻。

---

## 一、先读这三篇（上手 / 交接）

| 文档 | 讲什么 |
|---|---|
| **[OPS.md](OPS.md)** | ⭐ **运维与排障手册** —— 一分钟体检、服务启停、日志与**控制台检查**、**判定值对照表**、备份、升级流程 |
| **[DEPLOY.md](DEPLOY.md)** | 从零部署：环境、依赖、配置、首次启动 |
| **[AUTOCHAT.md](AUTOCHAT.md)** | 自动接话机制：概率 / 冷却 / 额度 / 静默 / 白名单 / 关键词 |

---

## 二、按主题查

### 核心机制

| 文档 | 讲什么 |
|---|---|
| [OFFICIAL.md](OFFICIAL.md) | QQ 官方开放平台路线（与 OneBot 路线的区别、intents、被动回复限制） |
| [AUTOCHAT.md](AUTOCHAT.md) | 自动接话的完整策略与调参 |
| [GROUP-REPLY.md](GROUP-REPLY.md) | 群消息回复规则（全量消息 / @ / 冷却） |
| [PERSONAS.md](PERSONAS.md) | 人设系统：`roles/*.md` 文件化、热更新、只留一套人设的现状 |
| [TOOLS.md](TOOLS.md) | 联网工具（新闻 / 热榜 / 搜索 / 天气 / 时间 / 表情） |
| [LEARNING.md](LEARNING.md) | 认人记忆与学习机制（1109 行，最详尽的一篇） |

### 图像相关

| 文档 | 讲什么 |
|---|---|
| [IMAGES.md](IMAGES.md) | 出图功能：触发、显存互斥、失败回退 |
| [QWEN-IMAGE-DEPLOY.md](QWEN-IMAGE-DEPLOY.md) | ComfyUI + Qwen-Image-2.1 部署全过程 |
| [MULTI-MODEL-GPU.md](MULTI-MODEL-GPU.md) | 16 GB 显卡如何让聊天模型和画图模型共存 |

### 规划与运维

| 文档 | 讲什么 |
|---|---|
| [OPS.md](OPS.md) | ⭐ 运维与排障（本文上方已列） |
| [PLAN.md](PLAN.md) | 路线规划与待办清单 |

---

## 三、使用与部署文档

| 文档 | 讲什么 |
|---|---|
| [USAGE.md](USAGE.md) | **使用说明**：启停三条命令、怎么改人设、行为参数表、故障排查表、文件地图 |
| [STRATA-DEPLOY.md](STRATA-DEPLOY.md) | **本地模型部署**：Qwen3.8-Flash-Next（125B）完整过程 + 10 条踩坑 |
| [STRATA-PROGRESS.md](STRATA-PROGRESS.md) | 部署当晚的暂停快照（留档） |
| [`../deploy/`](../deploy/README.md) | **部署脚本集**：显存互斥切换、模型 / 画图启动脚本、换机重建顺序 |

### 仓库外的相关文件

| 位置 | 文档 |
|---|---|
| `~/qwen-image/DEPLOY.md` | 生图部署（与 [QWEN-IMAGE-DEPLOY.md](QWEN-IMAGE-DEPLOY.md) 互补） |
| `~/README.md` | 家目录索引（所有目录用途 + 可清理项） |
| `~/notes/` | 与本机器人无关的笔记（DSH / 硬件 / 游戏卡顿等） |

---

## 四、排障速查（最常用的四条）

```bash
# ① 机器人 + 模型 + 显存，一眼看全
bash ~/qq-bot/svc.sh status

# ② 最近的接话判定（为什么不回某条消息，答案在这里）
grep -a "判定=" ~/qq-bot/logs/official.log | tail -8 | sed 's/.*\[qq\]//'

# ③ 模型是否正常（loaded + 显存占用）
curl -s http://127.0.0.1:8080/v1/status | python3 -c "
import json,sys; d=json.load(sys.stdin); m=d['machine']
print('loaded', d['loaded'], '| 显存 %.1f/%.1f GiB' % (m['gpu']['used_mib']/1024, m['gpu']['total_mib']/1024))"

# ④ 有没有报错
grep -aE "Traceback|ERROR" ~/qq-bot/logs/official.log | tail -5
```

**判定值对照表**见 [OPS.md 第 5 节](OPS.md#5-深入机器人为什么不回某条消息判定值对照)。

---

## 五、文档维护约定

1. **改功能 → 同步改文档**：哪篇讲这个功能就改哪篇，别另起新文件。
2. **新的故障经验 → 记进 `OPS.md`**：判定值表加一行、故障总表加一行。
   实战踩过的坑比理论值钱，尽量写"症状 → 查什么 → 怎么修"。
3. **每篇头部写「最后更新」日期**，便于判断是否过时。
4. **不放密钥**：`config.json` 里有 AppID/Secret，文档里只写路径不抄内容。
5. 写命令时用**可直接复制的完整命令**（含 `~` 展开、路径完整）。

---

## 六、版本沿革（避免读到过时内容）

| 时间 | 变化 |
|---|---|
| 2026-10-17 | 模型从 Ollama 27B 换成 **Strata + Qwen3.8-Flash-Next 125B**；人设改为 `roles/*.md` 文件化；新增去重/熔断；`OPS.md` 按新架构重写 |
| 2026-10-16 | Strata 部署完成；QQ 机器人迁移到本地 125B 模型 |
| 更早 | Ollama + qwen3.8-27b-local（已停用）；相关经验保留在 `OPS.md` 第 10 节 |
