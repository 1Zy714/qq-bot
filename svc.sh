#!/usr/bin/env bash
# QQ 机器人 + Ollama 统一启停脚本。
#
#   bash ~/qq-bot/svc.sh status              # 看全部状态（含模型显存）
#   bash ~/qq-bot/svc.sh start official      # QQ 开放平台官方机器人（自动读 qq.env）
#   bash ~/qq-bot/svc.sh start napcat        # OneBot 适配器（等 NapCat 反向连入 6199）
#   bash ~/qq-bot/svc.sh start ollama        # 本地模型服务（等价 bash ~/qwen/bin/serve.sh）
#   bash ~/qq-bot/svc.sh stop  all           # 停两个机器人（不含 ollama）
#   bash ~/qq-bot/svc.sh stop  ollama        # 停模型服务并释放显存
#   bash ~/qq-bot/svc.sh restart official
#   bash ~/qq-bot/svc.sh log   official      # 实时日志
#
# 两个刻意的设计：
# 1) 机器人只用绝对路径启动，避免同名脚本互相干扰；
# 2) 认进程时直接扫 /proc，要求命令行里出现**绝对路径**（脚本或 ollama 二进制）。
#    绝不按名字模糊匹配 —— 'bot.py' 会命中 'official_bot.py'，'ollama serve'
#    会命中正在执行本命令的 shell 自己，已经因此自杀三次。
set -uo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="${VENV:-$HOME/.venvs/qqbot}"
PY="$VENV/bin/python"
OLLAMA_BIN="$HOME/qwen/runtime/ollama/bin/ollama"
OLLAMA_LIB="$HOME/qwen/runtime/ollama/lib/ollama/llama-server"
OLLAMA_URL="http://127.0.0.1:11434"
STRATA_URL="${STRATA_URL:-http://127.0.0.1:8080}"

script_of() { case "$1" in napcat) echo "$HERE/bot.py" ;; official) echo "$HERE/official_bot.py" ;; *) return 1 ;; esac; }
log_of() { case "$1" in napcat) echo "$HERE/logs/bot.log" ;; official) echo "$HERE/logs/official.log" ;; *) return 1 ;; esac; }
env_of() { case "$1" in official) echo "$HERE/qq.env" ;; *) echo "" ;; esac; }

# 用法: pids_of <脚本绝对路径>
pids_of() {
  local script="$1" pid cmd exe dir
  for dir in /proc/[0-9]*; do
    pid="${dir#/proc/}"
    [ "$pid" = "$$" ] && continue
    [ -d "$dir" ] || continue
    [ -r "$dir/cmdline" ] || continue
    cmd="$(tr '\0' ' ' < "$dir/cmdline" 2>/dev/null)" || continue
    case "$cmd" in *"$script"*) ;; *) continue ;; esac
    exe="$(readlink -f "$dir/exe" 2>/dev/null || true)"
    case "$exe" in
      *python*) echo "$pid" ;;
      # readlink on /proc/PID/exe can fail (sandboxed / restricted /proc, and
      # some WSL setups). Fall back to the command line so status/stop still work.
      *) case "$cmd" in *python*) echo "$pid" ;; esac ;;
    esac
  done
}

# ollama 服务进程 + 它拉起的 llama-server 子进程。
# 判据优先用「可执行文件路径」：ollama 可能是用 `ollama serve` 这种相对名字启动的
# （cmdline 里没有绝对路径），而 exe 始终指向真实二进制。
# 但 exe 在受限 /proc（沙箱、部分 WSL 设置）下解析为空，所以补两条命令行回退，
# 且都以绝对路径为准，避免命中正在执行本脚本的 shell 自己（自杀事故的老坑）。
ollama_pids() {
  local pid dir exe cmd
  for dir in /proc/[0-9]*; do
    pid="${dir#/proc/}"
    [ "$pid" = "$$" ] && continue
    [ -d "$dir" ] || continue
    [ -r "$dir/cmdline" ] || continue
    exe="$(readlink -f "$dir/exe" 2>/dev/null || true)"
    cmd="$(tr '\0' ' ' < "$dir/cmdline" 2>/dev/null || true)"
    case "$exe" in
      *"/qwen/runtime/ollama/"*) echo "$pid" ;;
      *)
        case "$cmd" in
          # llama-server 子进程：命令行为绝对路径启动
          *"/qwen/runtime/ollama/"*) echo "$pid" ;;
          # `ollama serve` 父进程：命令行里没有路径，用「整条命令恰好是它」来锁死
          "ollama serve"|"ollama serve "*) echo "$pid" ;;
        esac
        ;;
    esac
  done
}

# 已用显存（GiB）：优先问 Strata（当前推理后端 8080），回退 Ollama 的 /api/ps
vram_gib() {
  local used_mib
  used_mib="$(curl -s --max-time 4 "$STRATA_URL/v1/status" 2>/dev/null \
    | grep -o '"used_mib":[0-9.]*' | head -1 | cut -d: -f2)"
  if [ -n "$used_mib" ]; then
    awk -v m="$used_mib" 'BEGIN{printf "%.1f", m/1024}'
    return
  fi
  curl -s --max-time 4 "$OLLAMA_URL/api/ps" 2>/dev/null \
    | grep -o '"size_vram":[0-9]*' | head -1 | cut -d: -f2 \
    | awk '{printf "%.1f", $1/1073741824}'
}

valid() { case "$1" in napcat|official) return 0 ;; *) echo "未知服务: '$1'（可选 napcat|official|ollama|all）" >&2; return 1 ;; esac; }

start_one() {
  local name="$1" script log envf existing
  if [ "$name" = official ]; then rm -f "$HERE/.watchdog-paused"; fi
  script="$(script_of "$name")"; log="$(log_of "$name")"; envf="$(env_of "$name")"
  existing="$(pids_of "$script" | tr '\n' ' ')"
  if [ -n "${existing// /}" ]; then echo "[$name] 已在运行: $existing"; return 0; fi
  if [ -n "$envf" ] && [ -f "$envf" ]; then set -a; . "$envf"; set +a; fi
  setsid nohup "$PY" -u "$script" >> "$log" 2>&1 < /dev/null &
  sleep 4
  existing="$(pids_of "$script" | tr '\n' ' ')"
  if [ -n "${existing// /}" ]; then
    echo "[$name] 已启动 PID $existing"
    tail -3 "$log"
  else
    echo "[$name] 启动失败，日志尾部:" >&2
    tail -15 "$log" >&2
    return 1
  fi
}

stop_one() {
  local name="$1" script pids
  script="$(script_of "$name")"
  if [ "$name" = official ]; then touch "$HERE/.watchdog-paused"; fi
  pids="$(pids_of "$script" | tr '\n' ' ')"
  if [ -z "${pids// /}" ]; then echo "[$name] 未在运行（守护已暂停）"; return 0; fi
  kill $pids 2>/dev/null || true
  sleep 2
  pids="$(pids_of "$script" | tr '\n' ' ')"
  if [ -z "${pids// /}" ]; then echo "[$name] 已停止（守护已暂停）"; else kill -9 $pids 2>/dev/null; echo "[$name] 已强制结束（守护已暂停）"; fi
}

start_ollama() {
  if [ -n "$(ollama_pids)" ]; then echo "[ollama] 已在运行"; return 0; fi
  bash "$HOME/qwen/bin/serve.sh"
}

stop_ollama() {
  local pids model
  pids="$(ollama_pids)"
  if [ -z "$pids" ]; then echo "[ollama] 未在运行"; return 0; fi
  # 先请求卸载模型（优雅释放显存），再停服务
  for model in $(curl -s --max-time 5 "$OLLAMA_URL/api/ps" 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4); do
    curl -s --max-time 20 "$OLLAMA_URL/api/generate" \
      -d "{\"model\":\"$model\",\"keep_alive\":0}" >/dev/null 2>&1 || true
  done
  sleep 2
  kill $pids 2>/dev/null || true
  sleep 3
  pids="$(ollama_pids)"
  if [ -z "$pids" ]; then echo "[ollama] 已停止（显存已释放）"; else kill -9 $pids 2>/dev/null; echo "[ollama] 已强制结束"; fi
}

status() {
  local name script pids ps vram sstatus smodel
  for name in napcat official; do
    script="$(script_of "$name")"
    pids="$(pids_of "$script" | tr '\n' ' ')"
    if [ -n "${pids// /}" ]; then echo "[$name] 运行中 PID $pids"; else echo "[$name] 未运行"; fi
  done
  # 推理后端：优先 Strata（8080），回退 Ollama（11434）
  sstatus="$(curl -s --max-time 4 "$STRATA_URL/v1/status" 2>/dev/null)"
  if [ -n "$sstatus" ]; then
    smodel="$(printf '%s' "$sstatus" | grep -o '"model": *"[^"]*"' | head -1 | cut -d'"' -f4)"
    vram="$(vram_gib)"
    echo "[strata] 运行中，模型 ${smodel}（显存 ${vram} GiB，API ${STRATA_URL}/v1）"
    return
  fi
  ps="$(curl -s --max-time 4 "$OLLAMA_URL/api/ps" 2>/dev/null)"
  if [ -z "$ps" ]; then
    echo "[strata] 未运行"
    echo "[ollama] 未运行"
    return
  fi
  vram="$(vram_gib)"
  if [ -n "$vram" ]; then echo "[ollama] 运行中，已加载模型（显存 ${vram} GiB）"
  else echo "[ollama] 运行中（未加载模型）"; fi
}

do_start() {
  case "$1" in
    ollama) start_ollama ;;
    all) start_one napcat; start_one official ;;
    *) valid "$1" && start_one "$1" ;;
  esac
}
do_stop() {
  case "$1" in
    ollama) stop_ollama ;;
    all) stop_one napcat; stop_one official ;;
    *) valid "$1" && stop_one "$1" ;;
  esac
}

case "${1:-status}" in
  start)   do_start "${2:-}" ;;
  stop)    do_stop "${2:-}" ;;
  restart) t="${2:-}"; do_stop "$t"; do_start "$t" ;;
  status)  status ;;
  log)     t="${2:-napcat}"; valid "$t" && tail -f "$(log_of "$t")" ;;
  *) echo "用法: bash $0 {start|stop|restart|status|log} [napcat|official|ollama|all]" >&2; exit 2 ;;
esac
