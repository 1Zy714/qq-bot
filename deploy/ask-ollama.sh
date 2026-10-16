#!/usr/bin/env bash
# 向本地模型提一个问题，流式打印正文（自动关闭思考）。
#
# 用法:
#   bash ~/qwen/bin/ask.sh "用一句话介绍你自己"
#   echo "长提示词..." | bash ~/qwen/bin/ask.sh
#   SYS="你是个刻薄的评论员" bash ~/qwen/bin/ask.sh "点评一下今天"
#   SHOW_THINK=1 bash ~/qwen/bin/ask.sh "1+1 等于几"    # 顺便看推理过程
#   MODEL=qwen3.8-27b-local:latest NUM_PREDICT=256 bash ~/qwen/bin/ask.sh "..."
set -uo pipefail

MODEL="${MODEL:-qwen3.8-27b-local:latest}"
BASE="${OLLAMA_BASE:-http://127.0.0.1:11434}"
SYS="${SYS:-你是简洁的中文助手，回答不超过三句话，不要 Markdown。}"
NUM_PREDICT="${NUM_PREDICT:-256}"
TEMPERATURE="${TEMPERATURE:-0.6}"
SHOW_THINK="${SHOW_THINK:-0}"

if [ "$#" -gt 0 ]; then
  PROMPT="$*"
else
  PROMPT="$(cat)"
fi

if [ -z "${PROMPT//[[:space:]]/}" ]; then
  echo "用法: bash ~/qwen/bin/ask.sh \"你的问题\"（或从 stdin 读）" >&2
  exit 2
fi

BASE="$BASE" MODEL="$MODEL" SYS="$SYS" PROMPT="$PROMPT" \
  NUM_PREDICT="$NUM_PREDICT" TEMPERATURE="$TEMPERATURE" SHOW_THINK="$SHOW_THINK" \
  python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

base = os.environ["BASE"].rstrip("/")
show_think = os.environ["SHOW_THINK"] == "1"
payload = {
    "model": os.environ["MODEL"],
    "messages": [
        {"role": "system", "content": os.environ["SYS"]},
        {"role": "user", "content": os.environ["PROMPT"]},
    ],
    # 本机实测：原生 /api/chat 认这个字段（OpenAI 兼容端点则要用 reasoning_effort）。
    # 想看推理过程必须置 True —— think:false 会把 thinking 一起掐掉。
    "think": show_think,
    "stream": True,
    "options": {
        "num_predict": int(os.environ["NUM_PREDICT"]),
        "temperature": float(os.environ["TEMPERATURE"]),
    },
}
request = urllib.request.Request(
    base + "/api/chat",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
show = sys.stdout.write
try:
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            message = chunk.get("message") or {}
            if show_think and message.get("thinking"):
                show(message["thinking"])
            if message.get("content"):
                show(message["content"])
                sys.stdout.flush()
            if chunk.get("done"):
                stats = chunk
                break
except urllib.error.URLError as error:
    print(f"\n请求失败: {error}", file=sys.stderr)
    raise SystemExit(1)

print()
if "stats" in dir() and stats.get("eval_count"):
    seconds = stats.get("eval_duration", 0) / 1e9
    rate = stats["eval_count"] / seconds if seconds else 0
    print(
        f"[{stats['eval_count']} tok / {seconds:.1f}s = {rate:.1f} tok/s"
        f" | prompt {stats.get('prompt_eval_count', 0)} tok]",
        file=sys.stderr,
    )
PY
