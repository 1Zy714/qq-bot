# Linux 命令入门：用这个项目练手

> 目标：**不背命令表**，而是「我想干什么 → 用哪条命令」。
> 所有例子都来自本项目的真实操作，复制即用。

---

## 0. 三个必须记住的概念

1. **一切皆文件**：进程在 `/proc`、设备在 `/dev`、配置就是一串文本。
2. **命令 = 程序**：`ls` 也是个程序，`ps` 也是；所以能组合。
3. **管道 `|`**：前一条命令的**输出**，变成后一条命令的**输入**。

```bash
ps aux | grep python      # 列出所有进程，再筛出 python
```

---

## 1. 看进程：谁在吃 CPU / 内存

| 我想… | 命令 |
|---|---|
| 看所有进程 | `ps aux` |
| 按 CPU 排序看前 10 | `ps -eo pid,pcpu,pmem,cmd --sort=-pcpu \| head` |
| 按内存排序看前 10 | `ps -eo pid,pcpu,pmem,cmd --sort=-rss \| head` |
| 看某个程序在不在 | `pgrep -af official_bot.py` |
| 看系统负载 | `uptime` |
| 看内存 | `free -h` |

本项目实例：

```bash
# 机器人进程在不在、PID 多少
pgrep -af official_bot.py

# 关掉它（先确认 PID 确实是它！）
kill -TERM <PID>
```

> **坑**：`pkill -f 关键词` 会**匹配到执行命令的 shell 自己**（命令行里也有那个词），
> 可能把自己杀掉。安全写法：`pkill -f 'pattern[^]'` 或先用 `pgrep -af` 确认 PID 再 `kill`。

---

## 2. 看端口：服务到底起没起

```bash
ss -ltnp                 # 本机监听中的 TCP 端口（含进程）
ss -ltnp | grep 3080     # 只看某个端口
curl -s http://127.0.0.1:3080/ -o /dev/null -w '%{http_code}\n'   # 用 HTTP 状态码判断
```

本项目常见端口：

| 端口 | 服务 |
|---|---|
| 3080 | DSH Web（你现在看的界面） |
| 8080 | 本地模型 Strata |
| 7842 | NagiBridge（星露谷 HTTP 桥） |
| 6199 | 旧的 NapCat/OneBot 路线（已不用） |

> 只想看「服务活着吗」，`curl -o /dev/null -w '%{http_code}'` 比 ss 更接近用户视角。

---

## 3. 看日志：出问题时第一现场

| 我想… | 命令 |
|---|---|
| 看最后 20 行 | `tail -n 20 logs/official.log` |
| 实时跟踪 | `tail -f logs/official.log` |
| 搜关键词 | `grep -n '空正文' logs/official.log` |
| 搜多个文件 | `grep -rn 'mcp' docs/` |
| 看某段时间 | `grep '22:4' logs/official.log` |
| 统计出现次数 | `grep -c ERROR logs/official.log` |

本项目实例：

```bash
# 机器人最近一次启动是否正常
grep -E '已上线|\[mcp\]' logs/official.log | tail -5

# 找一个报错的完整堆栈（-A 后面几行）
grep -A 10 '本地模型调用失败' logs/official.log | tail -20
```

> `tail -f` 用 `Ctrl+C` 退出。日志太大时先 `ls -lh logs/` 看大小，别直接 `cat`。

---

## 4. 找文件、看大小

| 我想… | 命令 |
|---|---|
| 按名字找 | `find . -name '*.py' -not -path './.git/*'` |
| 看目录多大 | `du -sh .` / `du -sh memory/` |
| 看磁盘剩余 | `df -h` |
| 列出最近改过的文件 | `find . -mmin -60 -type f` |
| 看文件类型 | `file xxx` |

本项目实例：

```bash
# 项目里哪些文件今天改过（排查「谁动了什么」）
find . -mmin -1440 -type f -not -path './.git/*' | head

# 表情包库占多少空间
du -sh stickers/ 2>/dev/null
```

---

## 5. 文本处理：grep / sed / awk 的日常

```bash
grep -c 'nagi' logs/official.log           # 出现几次
grep -n 'def main' official_bot.py          # 行号 + 内容
cut -d: -f1 config.json | head              # 按冒号切第一列
```

本项目实例（看配置里有哪些顶层键，不打印值）：

```bash
node -e "console.log(Object.keys(require('./config.json')))"
```

> 密钥类文件不要 `cat` 到聊天里；用 `grep '键名'` 或 `-o` 只取需要的部分。

---

## 6. 环境变量与 PATH

```bash
export LOCAL_LLM_KEY=sk-local      # 本次登录有效
env | grep LOCAL_LLM_KEY           # 确认设上了
echo $PATH                        # 命令搜索路径
```

本项目实例：DSH 适配器要求 `apiKeyEnv` 能解析到非空值，所以启动本地模型后端前要：

```bash
LOCAL_LLM_KEY=sk-local pnpm dsh --profile local --no-open --port 3081
```

（前缀写法的变量只对这一条命令生效，不会污染整个终端。）

---

## 7. 权限与文件属性

```bash
ls -l script.sh          # 看权限（rwx）
chmod +x script.sh       # 加执行权限
ls -ld memory/           # 看目录本身
```

本项目实例：`tools/gate-nagi.sh` 必须可执行，否则 DSH 拉起 MCP 时会失败。

```bash
chmod +x tools/gate-nagi.sh
```

---

## 8. 后台运行与停止

```bash
nohup bash serve.sh > /tmp/serve.log 2>&1 &    # 后台跑，脱离终端
jobs                                            # 看当前 shell 的后台任务
kill -TERM <PID>                                # 温和停止
kill -9 <PID>                                   # 强杀（最后手段）
```

本项目实例：

```bash
bash svc.sh start official     # 项目自带的服务脚本，内部就是 nohup + PID 检查
bash svc.sh status
bash svc.sh stop official
```

> 自己用 `&` 起的进程，关终端可能被带走；项目脚本用 `setsid nohup` 就是为了脱离会话。

---

## 9. 一个完整的排障套路（本项目的真实流程）

```bash
# ① 服务在不在
bash svc.sh status
# ② 端口通不通
ss -ltnp | grep -E '3080|8080|7842'
# ③ 最近日志有没有报错
tail -n 50 logs/official.log
# ④ 资源是否被打满
free -h; nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
# ⑤ 谁在占（按 CPU/内存）
ps -eo pid,pcpu,pmem,cmd --sort=-pcpu | head
```

---

## 10. 练习

1. 用一条命令列出本项目所有 `.py` 文件并按大小排序。
2. 找出 `logs/official.log` 里最近 5 条含 `[qq]` 的日志。
3. 用 `pgrep` 确认机器人是否在跑；在跑就记录 PID，再 `kill -TERM` 停掉。
4. 用 `curl` 判断 3080、8080、7842 三个端口哪些活着。

<details>
<summary>参考答案</summary>

```bash
find . -name '*.py' -not -path './.git/*' -printf '%s %p\n' | sort -n
grep '\[qq\]' logs/official.log | tail -5
pgrep -af official_bot.py && kill -TERM <PID>
for p in 3080 8080 7842; do curl -s -o /dev/null -m 2 -w "$p %{http_code}\n" http://127.0.0.1:$p/; done
```
</details>

---

## 11. 速查表

```bash
ps aux | grep xxx      # 进程
ss -ltnp               # 端口
tail -f file           # 跟日志
grep -rn kw dir/       # 搜内容
find . -name '*.py'    # 找文件
du -sh dir             # 目录大小
df -h                  # 磁盘
chmod +x f             # 加执行
kill -TERM pid         # 停进程
curl -w '%{http_code}' # 服务探活
```
