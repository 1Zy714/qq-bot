#!/usr/bin/env bash
# 聊天模型（Strata，Windows 侧）与生图模型（ComfyUI，WSL 侧）的显存互斥切换。
#
#   bash ~/qwen-image/bin/switch.sh image      # 要画图：停 Strata、起 ComfyUI
#   bash ~/qwen-image/bin/switch.sh chat       # 要聊天：停 ComfyUI、起 Strata
#   bash ~/qwen-image/bin/switch.sh status     # 看两边状态与显存
#   bash ~/qwen-image/bin/switch.sh stop-all   # 两边都停（彻底放开显存/内存）
#
# 为什么需要它：显卡只有 16.3 GB 显存。
#   Strata 常驻约 13.1 GB（另占约 39 GB 内存）；ComfyUI 出图峰值约 9.4 GB。
#   两边都必须"独占"才能跑，而它们的让位能力并不对称：
#     · ComfyUI 可以用 /free 释放，下次出图自己加载（原本就支持）；
#     · Strata 没有卸载 API，只能停掉整个服务——而且它的服务前端
#       (serve/server.py) 带 auto_load，只杀引擎(strata.exe)会在下次请求时
#       把模型重新拉回来抢显存，所以必须连前端一起停。
set -uo pipefail

CMD=/mnt/c/Windows/System32/cmd.exe
PS_BIN=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
STRATA_EXE="strata.exe"
STRATA_DIR_WIN='Y:\Strata'
STRATA_BAT_WIN='Y:\Strata\run-iq2_xs.bat'
STRATA_URL="http://127.0.0.1:8080"
COMFY_SERVE="$HOME/qwen-image/bin/serve.sh"
COMFY_URL="http://127.0.0.1:8188"
# 显存降到这个值以下，认为聊天模型已经让位
VRAM_FREE_THRESHOLD_MIB="${VRAM_FREE_THRESHOLD_MIB:-6000}"

say() { echo "$@"; }

vram_used_mib() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '
}

strata_running() { curl -sS -o /dev/null --max-time 3 "$STRATA_URL/v1/status" 2>/dev/null; }
comfy_running()  { curl -sS -o /dev/null --max-time 3 "$COMFY_URL/system_stats" 2>/dev/null; }

wait_vram_below() {
  local want="$1" i used
  for i in $(seq 1 30); do
    used="$(vram_used_mib)"
    if [ -n "$used" ] && [ "$used" -lt "$want" ]; then return 0; fi
    sleep 2
  done
  return 1
}

# 找出监听 8080 的 Windows 进程 —— 那是 Strata 的"服务前端"(serve/server.py)。
# 只杀引擎不够：前端带 auto_load，下次请求会把模型重新拉回来抢显存。
strata_holder_pid() {
  $CMD /c "netstat -ano" 2>/dev/null \
    | grep -a ":8080 " | grep -a "LISTENING" | awk '{print $NF}' | head -1 | tr -d '\r'
}

stop_strata() {
  local any=0
  strata_running && any=1
  $CMD /c "tasklist" 2>/dev/null | grep -qi "$STRATA_EXE" && any=1
  if [ "$any" = 0 ]; then
    say "[strata] 未在运行"
    return 0
  fi
  say "[strata] 正在停止（服务前端 + 引擎，释放显存与内存）..."
  local pid
  pid="$(strata_holder_pid)"
  if [ -n "$pid" ]; then
    say "[strata] 停止服务前端 PID=$pid（连同子进程）"
    $CMD /c "taskkill /F /T /PID $pid" >/dev/null 2>&1 || true
  fi
  $CMD /c "taskkill /F /IM $STRATA_EXE" >/dev/null 2>&1 || true
  if wait_vram_below "$VRAM_FREE_THRESHOLD_MIB"; then
    say "[strata] 已停止，显存已释放（当前 $(vram_used_mib) MiB 在用）"
  else
    say "[strata] 已发出停止信号（显存下降较慢，继续下一步）"
  fi
  if strata_running; then
    say "[strata] 警告：服务仍在应答，显存可能被 auto_load 再次抢占" >&2
    return 1
  fi
}

start_strata() {
  if strata_running; then say "[strata] 已在运行"; return 0; fi
  say "[strata] 正在启动（加载约 39 GB，约 1 分钟；会弹出一个窗口，别关它）..."
  # 坑：不能从 WSL 里用 `cmd /c start` —— WSL 启动 cmd 时当前目录是 UNC 路径
  # (\\wsl.localhost\...)，start 会报「系统找不到文件 \\」。
  # 改用 PowerShell 的 Start-Process 并显式指定工作目录。
  "$PS_BIN" -NoProfile -Command \
    "Start-Process -FilePath '$STRATA_BAT_WIN' -WorkingDirectory '$STRATA_DIR_WIN'" >/dev/null 2>&1
  local i
  for i in $(seq 1 100); do
    sleep 2
    if strata_running; then say "[strata] 已就绪（约 $((i * 2))s）: $STRATA_URL/v1"; return 0; fi
  done
  say "[strata] 启动超时（>200s），请检查 Windows 桌面上那个 Strata 窗口" >&2
  return 1
}

stop_comfy() {
  if ! pgrep -f '[m]ain.py --listen' >/dev/null 2>&1; then say "[comfy] 未在运行"; return 0; fi
  say "[comfy] 正在停止..."
  pkill -f '[m]ain.py --listen' 2>/dev/null || true
  sleep 3
  if pgrep -f '[m]ain.py --listen' >/dev/null 2>&1; then
    pkill -9 -f '[m]ain.py --listen' 2>/dev/null || true
    sleep 1
  fi
  say "[comfy] 已停止"
}

start_comfy() {
  if comfy_running; then say "[comfy] 已在运行"; return 0; fi
  bash "$COMFY_SERVE"
}

status() {
  if strata_running; then
    local model
    model="$(curl -sS --max-time 3 "$STRATA_URL/v1/status" 2>/dev/null \
      | grep -o '"model": *"[^"]*"' | head -1 | cut -d'"' -f4)"
    echo "[strata] 运行中（${model}）  $STRATA_URL/v1"
  else
    echo "[strata] 未运行"
  fi
  if comfy_running; then echo "[comfy]  运行中  $COMFY_URL"; else echo "[comfy]  未运行"; fi
  echo "显存: $(vram_used_mib) / $(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ') MiB"
}

case "${1:-status}" in
  image)    stop_strata; start_comfy ;;
  chat)     stop_comfy;  start_strata ;;
  status)   status ;;
  stop-all) stop_comfy;  stop_strata ;;
  *) echo "用法: bash $0 {image|chat|status|stop-all}" >&2; exit 2 ;;
esac
