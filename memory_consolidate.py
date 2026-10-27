#!/usr/bin/env python3
"""手动整理机器人记忆：合并重复印象 + 衰减淘汰（纯规则，不调模型）。

机器人上线后也会在后台每天跑一次同样的逻辑（`memory_consolidate_enabled`）。
这个脚本用于人工检查/立即整理。

用法::

    python3 memory_consolidate.py                # 整理并保存
    python3 memory_consolidate.py --dry-run      # 只看会改什么
    python3 memory_consolidate.py --inactive-days 90
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from user_memory import UserMemory  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=str(HERE / "memory" / "users.json"))
    parser.add_argument("--inactive-days", type=float, default=180.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    memory = UserMemory(Path(args.path))
    merged = memory.consolidate()
    pruned = memory.prune(inactive_days=args.inactive_days)
    report = {
        "profiles": merged["profiles"],
        "notes_before": merged["before"],
        "notes_after": merged["after"],
        "notes_merged": merged["merged"],
        "users_removed": pruned["users_removed"],
        "notes_trimmed": pruned["notes_trimmed"],
        "dry_run": args.dry_run,
    }
    if not args.dry_run and (merged["merged"] or pruned["users_removed"] or pruned["notes_trimmed"]):
        memory.save()
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
