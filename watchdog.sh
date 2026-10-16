#!/usr/bin/env bash
# QQ 机器人健康守护 —— 单次检查，不健康就拉起；交给 systemd timer 或 cron 定期调用。
#
# 用法:
#   bash ~/qq-bot/watchdog.sh            检查一次（安静：仅异常时输出）
#   bash ~/qq-bot/watchdog.sh --verbose  总是输出判断过程
#   bash ~/qq-bot/watchdog.sh --status   只报告健康与否，绝不重启
#
# 健康判据（两条都满足才算健康）:
#   1. 进程存在（按 official_bot.py 的绝对路径匹配，不会误伤同名脚本）
#   2. 日志里最后一次「正在连接网关」之后出现过「机器人已上线」
#      这条是关键：2026-10-16 出现过"进程还在 ps 里、却已卡死在启动阶段"的活僵尸，
#      只看进程存在会被骗过去。
#
# 暂停开关：~/qq-bot/.watchdog-paused 存在时一律不动手。
#   `svc.sh stop official` 会创建它，`svc.sh start official` 会删除它 ——
#   也就是说，主人用 svc.sh 停掉的机器人，守护不会擅自拉起来。
set -uo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
LOG="$HERE/logs/official.log"
WDLOG="$HERE/logs/watchdog.log"
PAUSE="$HERE/.watchdog-paused"
TARGET="$HERE/official_bot.py"

MODE=normal
case "${1:-}" in
  --verbose) MODE=verbose ;;
  --status)  MODE=status ;;
esac

say() { [ "$MODE" = normal ] && return 0; echo "$@"; return 0; }
note() { printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$WDLOG"; }

pids_of_bot() { pgrep -f "$TARGET" 2>/dev/null | tr '\n' ' '; }

# ---- 暂停开关 ----
if [ -f "$PAUSE" ]; then
  note "跳过：存在 $PAUSE（主人主动停用中）"
  say "[watchdog] 已暂停（exist $PAUSE）"
  exit 0
fi

# ---- 健康判定 ----
pid="$(pids_of_bot | awk '{print $1}')"
healthy=1
reason=""

if [ -z "$pid" ]; then
  healthy=0
  reason="进程不存在"
else
  last_start="$(grep -n "正在连接网关" "$LOG" 2>/dev/null | tail -1 | cut -d: -f1)"
  if [ -z "$last_start" ]; then
    healthy=0
    reason="日志中找不到启动记录（PID $pid）"
  elif ! tail -n +"$last_start" "$LOG" 2>/dev/null | grep -q "机器人已上线"; then
    healthy=0
    reason="最后一次启动未完成、无「已上线」（PID $pid 疑似卡死）"
  fi
fi

if [ "$healthy" = 1 ]; then
  say "[watchdog] 健康：PID $pid"
  exit 0
fi

say "[watchdog] 不健康：$reason"
if [ "$MODE" = status ]; then
  exit 1
fi

# ---- 拉起 ----
note "重启：$reason"
for p in $(pids_of_bot); do
  kill "$p" 2>/dev/null || true
done
sleep 2
for p in $(pids_of_bot); do
  kill -9 "$p" 2>/dev/null || true
done
sleep 1

bash "$HERE/svc.sh" start official >> "$WDLOG" 2>&1
sleep 5

newpid="$(pids_of_bot | awk '{print $1}')"
if [ -n "$newpid" ]; then
  note "已拉起：PID $newpid"
  say "[watchdog] 已拉起 PID $newpid"
  exit 0
fi

note "拉起失败，详见 $LOG"
say "[watchdog] 拉起失败，详见 $LOG"
exit 1
