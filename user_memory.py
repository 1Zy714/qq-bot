#!/usr/bin/env python3
"""按 openid 记住「谁在群里说过什么」的轻量长期记忆。

为什么要单独一个文件
--------------------
官方路线（botpy）和 NapCat 路线（OneBot）拿到的身份字段完全不同，
但记忆层其实只需要 ``(uid, 昵称, 场景)`` 三样东西。把它做成与协议无关的模块，
两条路线共用同一份档案，也方便离线单测（``test_user_memory.py``）。

记忆分两类
----------
1. **自动记账**：昵称、发言次数、首末次时间。每次发言都会更新，不需要模型参与；
2. **印象**：由模型自己在回复里用 ``[记住:内容]`` 标记产出——和表情包标记同一套路，
   摘掉标记后存进档案，下次对话时作为「你认识的人」注入 system。
   这样做的好处是**零额外推理**：27B 在这台机器上 8 tok/s，
   专门为"总结印象"再跑一轮会让每条回复多等 3–12 秒，不划算。

两个必须守住的细节
------------------
* **摘要必须稳定**：注入 system 的文本按 uid 排序、不含随消息变化的计数。
  否则每条消息都会重写 system 前缀，把 llama.cpp 的前缀缓存打掉
  （见 ``docs/PERSONAS.md`` 第 6 节：切人设会换掉前缀，缓存随之失效）。
* **昵称是不可信输入**：QQ 昵称里可以塞 ``]`` 换行和「忽略以上指令」。
  落盘前一律清洗，否则等于给群友开了一个提示词注入的口子。

档案里含真实 openid，属于个人数据，``.gitignore`` 已挡住 ``memory/`` 目录。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# 模型用来留印象的标记，形状与 sticker_lib 的 [表情:标签] 保持一致
MEMORY_MARKER_RE = re.compile(r"\[记住[：:]\s*([^\]\[]*?)\s*\]")

# 昵称/印象里一律剔除的字符：方括号会破坏注入格式，换行会把一行拆成两条
_UNSAFE_RE = re.compile(r"[\[\]\r\n\t]+")

MAX_NAME_CHARS = 16
MAX_NOTE_CHARS = 60
MAX_NOTE_COUNT = 3          # 每人最多留几条印象，超了就丢最旧的
MAX_USERS = 300             # 档案上限，超了先淘汰"没印象的纯路人"

# 人设要求"没新信息就写 [记住:无]"，这类占位词不能当成印象存下来
NONE_WORDS = {"无", "none", "None", "没有", "暂无", "空", "无。", "-"}


def clean_text(raw: object, limit: int) -> str:
    """清洗不可信文本：去方括号/换行，压空白，截断。"""
    text = _UNSAFE_RE.sub(" ", str(raw or ""))
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:limit]


def parse_memory_marker(text: str) -> tuple[str, list[str]]:
    """从模型回复里摘出记忆标记。

    返回 ``(去掉标记的正文, [印象, ...])``。跟表情包标记一样，
    **所有**标记都要摘掉：只处理第一个的话，剩下的会以
    ``[记住:xxx]`` 的字面量发到群里，那场面相当尴尬。

    一条标记里用 ``；`` / ``;`` 隔开的内容会拆成多条印象
    （``[记住:爱吃辣；嘴硬]`` → 两条），这样去重和数量上限才有意义。
    """
    if not text:
        return "", []
    notes: list[str] = []
    for raw in MEMORY_MARKER_RE.findall(text):
        for piece in re.split(r"[;；]", str(raw)):
            note = clean_text(piece, MAX_NOTE_CHARS)
            # 「[记住:无]」只是为了当格式锚点，不是真的印象
            if note and note not in NONE_WORDS:
                notes.append(note)
    text = MEMORY_MARKER_RE.sub("", text)
    # 生成被 max_tokens 截断时，标记可能只剩半截（"[记住:爱吃"），
    # 正则匹配不到，就会以字面量发进群里。这里再扫一遍未闭合的残片。
    text = re.sub(r"\[记住[：:][^\]\[]*$", "", text)
    text = re.sub(r"\[记住[：:]?$", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, notes


def short_uid(uid: str) -> str:
    """没有昵称时的兜底称呼。openid 很长，取尾巴 6 位就够区分了。"""
    tail = re.sub(r"\W", "", str(uid or ""))[-6:]
    return f"群友{tail}" if tail else "群友"


@dataclass(frozen=True)
class Speaker:
    """一个发言人。

    两条路线都能构造它：官方路线从 botpy 的 ``author`` 里取，
    NapCat 路线从 OneBot 事件的 ``sender`` 里取。``scene`` 决定提问前缀
    写成「群友」还是「私聊」——人设靠这个标记切换两种态度。
    """

    uid: str
    name: str = ""
    scene: str = "group"


@dataclass
class Profile:
    """一个人。``notes`` 是模型留下的印象，``count`` 是自动记账的发言数。"""

    uid: str
    name: str = ""
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        """给模型看的称呼：优先用当前昵称。"""
        return self.name or short_uid(self.uid)

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, uid: str, data: object) -> "Profile":
        if not isinstance(data, dict):
            return cls(uid=uid)
        raw_notes = data.get("notes") or []
        notes = [clean_text(n, MAX_NOTE_CHARS) for n in raw_notes] if isinstance(raw_notes, list) else []
        return cls(
            uid=uid,
            name=clean_text(data.get("name", ""), MAX_NAME_CHARS),
            count=int(data.get("count", 0) or 0),
            first_seen=float(data.get("first_seen", 0.0) or 0.0),
            last_seen=float(data.get("last_seen", 0.0) or 0.0),
            notes=[n for n in notes if n],
        )


class UserMemory:
    """档案库：JSON 落盘，重启不丢；进程内带脏标记，只在真改动时写盘。"""

    def __init__(self, path: Path | str, *, max_users: int = MAX_USERS) -> None:
        self.path = Path(path)
        self.max_users = max_users
        self.users: dict[str, Profile] = {}
        self._dirty = False
        self._version = 0          # 每次改动 +1，供调用方判断摘要要不要重建
        self._digest_cache: tuple[int, str] | None = None
        self.load()

    # ---------------- 落盘 ----------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return  # 档案坏了就当没有，绝不能因此让机器人起不来
        raw_users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(raw_users, dict):
            return
        for uid, item in raw_users.items():
            profile = Profile.from_json(str(uid), item)
            if profile.uid:
                self.users[profile.uid] = profile
        self._version += 1

    def save(self) -> None:
        """原子写：先写临时文件再 replace，断电不会留下半截 JSON。"""
        if not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "users": {uid: p.to_json() for uid, p in self.users.items()},
            }
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass  # 写盘失败不该影响聊天

    # ---------------- 记账 ----------------

    def touch(self, uid: str, name: str = "") -> Profile:
        """记一次发言。返回最新档案（不存在则创建）。"""
        key = str(uid or "").strip() or "unknown"
        profile = self.users.get(key)
        now = time.time()
        if profile is None:
            profile = Profile(uid=key, first_seen=now)
            self.users[key] = profile
            self._trim()
        safe_name = clean_text(name, MAX_NAME_CHARS)
        if safe_name and safe_name != profile.name:
            profile.name = safe_name           # 群名片随时会改，跟着最新走
            self._dirty = True
        profile.count += 1
        profile.last_seen = now
        self._dirty = True
        self._version += 1
        return profile

    def add_notes(self, uid: str, notes: list[str]) -> int:
        """给某人补印象，返回真正新增的条数（去重）。"""
        key = str(uid or "").strip()
        if not key or not notes:
            return 0
        profile = self.users.get(key)
        if profile is None:
            profile = Profile(uid=key, first_seen=time.time())
            self.users[key] = profile
        added = 0
        for note in notes:
            clean = clean_text(note, MAX_NOTE_CHARS)
            if not clean or clean in profile.notes:
                continue
            profile.notes.append(clean)
            added += 1
        if len(profile.notes) > MAX_NOTE_COUNT:
            # 只留最近的几条，否则档案会越滚越大、注入也越来越长
            del profile.notes[:-MAX_NOTE_COUNT]
        if added:
            self._dirty = True
            self._version += 1
        return added

    def _trim(self) -> None:
        """超过上限时淘汰：先扔"一句话都没记住"的纯路人，再扔最久没说话的。"""
        if len(self.users) <= self.max_users:
            return
        victims = sorted(
            self.users.values(),
            key=lambda p: (bool(p.notes), p.last_seen),   # 有印象的排后面，最后才淘汰
        )
        for victim in victims[: len(self.users) - self.max_users]:
            self.users.pop(victim.uid, None)
        self._dirty = True

    # ---------------- 查询 ----------------

    def get(self, uid: str) -> Profile | None:
        return self.users.get(str(uid or "").strip())

    def find(self, keyword: str) -> list[Profile]:
        """按 uid 或昵称模糊找人（给「忘掉」指令用）。"""
        needle = clean_text(keyword, MAX_NAME_CHARS)
        if not needle:
            return []
        exact = [p for p in self.users.values() if p.uid == keyword or p.name == needle]
        if exact:
            return exact
        return [p for p in self.users.values() if needle in p.name or needle in p.uid]

    def forget(self, keyword: str) -> int:
        """按 uid/昵称删人，返回删除条数。"""
        victims = self.find(keyword)
        for victim in victims:
            self.users.pop(victim.uid, None)
        if victims:
            self._dirty = True
            self._version += 1
        return len(victims)

    def clear(self) -> int:
        count = len(self.users)
        if count:
            self.users.clear()
            self._dirty = True
            self._version += 1
        return count

    # ---------------- 给模型看 ----------------

    def digest(self, limit: int = 8) -> str:
        """「你认识的人」摘要，注入 system 后缀。

        **只放有印象的人**，并且按 uid 排序——保证同样的档案产出**逐字相同**的
        文本。一旦把"最近说话的频率"之类的动态内容放进来，每条消息都会
        改写 system 前缀，前缀缓存就白做了。
        """
        if self._digest_cache is not None and self._digest_cache[0] == self._version:
            return self._digest_cache[1]
        people = [p for p in self.users.values() if p.notes]
        people.sort(key=lambda p: p.uid)
        people = people[:limit]
        if not people:
            text = ""
        else:
            lines = [f"- {p.display}：{'；'.join(p.notes)}" for p in people]
            text = "\n\n【你记得的群友】\n" + "\n".join(lines)
        self._digest_cache = (self._version, text)
        return text

    def describe(self, limit: int = 12) -> str:
        """给「记忆」指令用的人类可读列表（和注入模型的摘要不是一回事）。"""
        if not self.users:
            return "我还没记住谁呢。"
        people = sorted(self.users.values(), key=lambda p: p.last_seen, reverse=True)[:limit]
        chunks = []
        for p in people:
            note = f"（{'；'.join(p.notes)}）" if p.notes else ""
            chunks.append(f"{p.display}×{p.count}{note}")
        tail = f" 等 {len(self.users)} 人" if len(self.users) > len(people) else ""
        return "我记住的人：" + "、".join(chunks) + tail
