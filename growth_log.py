#!/usr/bin/env python3
"""成长附录：一本**只准往后写**的本子。

普通记忆存的是「事实」（谁喜欢什么、谁住哪），存不了「我从哪件事上学到了什么」。
模型会忘记自己变过：昨天被纠正的一个看法，今天又犯。所以需要一本单独的、
只追加的成长笔记——想通了什么、被纠正了哪个看法、哪次判断错了。

**「只准追加」必须是机制，不能是口头约定**，三层保证：

1. 接口层：本模块只有 ``append`` 和读，没有任何改 / 删的函数。
2. 文件层：JSONL 逐行 ``open(..., "a")``，从不重写整个文件。
3. 链校验：每行存上一行的哈希，``hash = sha256(prev | ts | text)``。
   手工改任何一行历史，从那一行开始链就断了，注入时会标 ``⚠️链断``。

另外有三重限流（单条字数、每天条数、最小间隔）——只做一样，模型会刷屏
写流水账。以及一条硬规矩：**不写进度、不复述对话、没真变化就别写**。

用法（人工自检）::

    python growth_log.py --verify     # 校验整条链
    python growth_log.py --tail 10    # 看最近 10 条
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# 单条上限。成长笔记是「一句反思」，不是日记。
MAX_CHARS = 200
# 每天最多几条：模型天然爱写，不设上限一天能写满一本
MAX_PER_DAY = 10
# 两条之间的最小间隔（秒）：30 分钟
MIN_INTERVAL = 1800.0
# 注入几条
INJECT_COUNT = 6
# 注入的总字数预算（防止 6 条长笔记把 prompt 吃光）
INJECT_CHARS = 600

TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# [成长:...] / 【成长：...】，容忍半角全角冒号。内容里不许出现 ] 】，
# 否则会被判成标记提前结束——和 [记住:...] 的解析保持一致。
GROWTH_MARKER_RE = re.compile(r"[\[【]\s*成长\s*[:：]\s*([^\]】]{1,%d})[\]】]" % MAX_CHARS)


def parse_growth_marker(text: str) -> tuple[str, list[str]]:
    """从回复里摘出 ``[成长:...]`` 标记，返回 (去掉标记的正文, 记录列表)。

    标记必须在截断 / 分片**之前**摘掉，否则半截标记会以字面量发进群里。
    """
    found = [m.group(1).strip() for m in GROWTH_MARKER_RE.finditer(text or "")]
    found = [item for item in found if item]
    if not found:
        return text, []
    cleaned = GROWTH_MARKER_RE.sub("", text)
    return cleaned.strip(), found


def _sha(prev: str, ts: str, text: str) -> str:
    return hashlib.sha256(f"{prev}|{ts}|{text}".encode("utf-8")).hexdigest()


@dataclass
class Record:
    ts: str
    text: str
    prev: str
    hash: str


@dataclass
class GrowthLog:
    """只追加的成长笔记。文件坏了也不能影响主链路，故读失败一律当空。"""

    path: Path
    max_chars: int = MAX_CHARS
    max_per_day: int = MAX_PER_DAY
    min_interval: float = MIN_INTERVAL
    _cache: tuple[tuple[int, int], list[Record], int] | None = None  # (mtime_ns, size) -> (记录, 断点行号)

    # ---------- 读 + 校验 ----------

    def _load(self) -> tuple[list[Record], int]:
        """读全量并校验链；返回 (记录, 第一条断链的行号 1-based，0 表示完好)。

        校验顺手做了链的连续性：第 i 行的 prev 必须等于第 i-1 行的 hash。
        任何一行坏掉，后面的全部标记为不可信（断点之后的注入不带）。
        """
        try:
            stat = self.path.stat()
        except OSError:
            return [], 0
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache[0] == stamp:
            return self._cache[1], self._cache[2]

        records: list[Record] = []
        broken = 0
        try:
            raw_lines = self.path.read_text("utf-8").splitlines()
        except OSError:
            return [], 0
        for index, line in enumerate(raw_lines, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                rec = Record(
                    ts=str(obj["ts"]),
                    text=str(obj["text"]),
                    prev=str(obj.get("prev", "")),
                    hash=str(obj["hash"]),
                )
            except (ValueError, KeyError, TypeError):
                broken = broken or index
                break
            expect = _sha(rec.prev, rec.ts, rec.text)
            prev_hash = records[-1].hash if records else ""
            if rec.hash != expect or rec.prev != prev_hash:
                broken = index
                break
            records.append(rec)
        if broken:
            # 断点之后的内容不可信，整段不注入（不半信半疑地展示）
            records = []
        self._cache = (stamp, records, broken)
        return records, broken

    def records(self) -> list[Record]:
        return self._load()[0]

    def is_broken(self) -> int:
        return self._load()[1]

    # ---------- 写 ----------

    def append(self, text: str, now: float | None = None) -> tuple[bool, str]:
        """追加一条。返回 (是否写入, 原因)。原因会进日志，便于排障。"""
        body = re.sub(r"[\s\u200b\ufeff]+", " ", str(text or "")).strip()
        if not body:
            return False, "empty"
        if len(body) > self.max_chars:
            return False, f"too_long:{len(body)}>{self.max_chars}"

        records, broken = self._load()
        if broken:
            # 链断时**不往坏文件里继续追加**：那样只会把断点埋得更深。
            return False, f"chain_broken:line{broken}"

        moment = time.time() if now is None else now
        ts = time.strftime(TS_FORMAT, time.localtime(moment))
        today = ts[:10]

        if records:
            last = records[-1]
            if last.text == body:
                return False, "duplicate"
            try:
                last_ts = time.mktime(time.strptime(last.ts, TS_FORMAT))
            except ValueError:
                last_ts = 0.0
            if moment - last_ts < self.min_interval:
                return False, "too_soon"
        today_count = sum(1 for r in records if r.ts[:10] == today)
        if today_count >= self.max_per_day:
            return False, "daily_quota"

        prev = records[-1].hash if records else ""
        rec = Record(ts=ts, text=body, prev=prev, hash=_sha(prev, ts, body))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(
                    {"ts": rec.ts, "text": rec.text, "prev": rec.prev, "hash": rec.hash},
                    ensure_ascii=False,
                ) + "\n")
                handle.flush()
        except OSError as error:
            return False, f"io_error:{error}"

        # 缓存立刻失效，下一次 digest 会重新读盘校验
        self._cache = None
        return True, "written"

    # ---------- 注入 ----------

    def digest(self, count: int = INJECT_COUNT, budget: int = INJECT_CHARS) -> str:
        """最近几条 + 链状态，拼进 system 后缀（它只在新条目出现时变化）。"""
        records, broken = self._load()
        if broken:
            return (
                "\n\n【成长附录 · ⚠️链断】\n"
                f"第 {broken} 条历史被改过，附录已停止注入也不允许再写。"
                "请主人检查 memory/growth.jsonl。"
            )
        if not records:
            return ""
        selected = records[-count:]
        lines: list[str] = []
        used = 0
        for rec in reversed(selected):
            line = f"- {rec.ts[:16]} {rec.text}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)
        lines.reverse()
        return (
            "\n\n【成长附录 · 只准往后写】\n"
            + "\n".join(lines)
            + "\n（这些是你自己想通或被人纠正后记下的，别当约定念，也别复述；"
              "没真变化时用 [成长:一句话] 记录，不写流水账）"
        )

    def describe(self, count: int = 10) -> str:
        """给「成长记录」指令用的人类可读输出。"""
        records, broken = self._load()
        if broken:
            return f"成长附录第 {broken} 条被改过，链断了，我没法继续写。请主人检查文件。"
        if not records:
            return "成长附录还是空的，我还没记下什么真变化。"
        lines = [f"最近 {min(count, len(records))} 条成长记录："]
        for rec in records[-count:]:
            lines.append(f"· {rec.ts[:16]} {rec.text}")
        return "\n".join(lines)

    def verify(self) -> tuple[bool, int]:
        records, broken = self._load()
        return (not broken), len(records)


def main(argv: list[str]) -> int:
    """人工自检：--verify / --tail N。路径默认 memory/growth.jsonl。"""
    path = Path(__file__).resolve().parent / "memory" / "growth.jsonl"
    log = GrowthLog(path)
    if "--tail" in argv:
        index = argv.index("--tail")
        count = int(argv[index + 1]) if len(argv) > index + 1 else 10
        print(f"文件：{path}")
        print(log.describe(count))
        return 0
    ok, total = log.verify()
    print(f"文件：{path}")
    print(f"条数：{total}")
    if ok:
        print("链校验：✓ 完好")
        return 0
    print(f"链校验：✗ 第 {log.is_broken()} 条起断裂")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
