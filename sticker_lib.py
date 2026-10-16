#!/usr/bin/env python3
"""表情包库：自动收集群聊图片 + 选择发送。

设计要点（都是被现实逼出来的，不是想当然）：

1) **存储用内容哈希做文件名**。群聊里同一张表情包会被反复转发，用 ``sha1`` 命名
   天然去重，不会因为转发十次就存十份。

2) **索引与文件分离**。``index.json`` 只存元数据（哈希、扩展名、尺寸、时间、来源），
   文件按哈希落在 ``stickers/`` 下。这样重命名扩展名、迁移目录都不会丢关联。

3) **收集要过滤**。官方事件里 ``attachments`` 会推**所有**图片，包括群友发的照片、
   截图、表情包。全都收下来的话，库里会混进大量照片，「发个表情」就翻出人家自拍，
   那是社死而不是功能。所以默认按「小尺寸 + 非照片格式」收：gif 必收，
   png/jpg 只在尺寸小且像素不大时才收。

4) **发送只允许模型从已有库里选**。绝不让模型自己编关键词——它编一个库里没有的，
   用户就看不到任何反馈。做法是把可用的标签列表喂给模型，标记写作 ``[表情:标签]``。

官方平台限制（决定了上面的实现方式）：
  - 消息里不允许出现 URL（错误码 40054010）→ 只能 base64 直传，不能给链接
  - 被动回复同一 msg_id 最多 5 条，msg_seq 必须互不相同 → 文本与表情包各占一条
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# 收集过滤阈值：宁可少收，也不要把照片塞进表情库
# 2026-10-17 放宽：原来 2 MiB / 800px 太紧，很多正常静态表情包（尤其 1000px 上下的
# 梗图）都被当成"照片"拒收了，库一直长不大。gif 仍然无条件收。
MAX_STICKER_BYTES = 4 * 1024 * 1024      # 单张最大 4 MiB
MIN_STICKER_BYTES = 1024                 # 小于 1 KiB 的多半是缩略图或空白图
MAX_PIXELS_SIDE = 1280                   # png/jpg 任一边超过 1280px 才视为照片/截图，不收
# gif 与 webp 基本都是表情包（QQ/微信常见的动图格式），无条件收
ALWAYS_ACCEPT_TYPES = {"image/gif", "image/webp"}
PHOTO_TYPES = {"image/jpeg", "image/jpg", "image/png"}
DEFAULT_QUOTA_BYTES = 1024 * 1024 * 1024  # 库容量上限 1 GiB（放宽阈值后收得更多）

# 模型在回复里写的标记： [表情:开心] / ［表情：无语］ / [表情包:笑]（兼容全角与「表情包」写法）
TAG_MARKER_RE = re.compile(r"[\[［]\s*表情(?:包)?\s*[:：]\s*([^\]］\n]{1,24}?)\s*[\]］]")
# 兜底：模型可能只写 [表情] 不带标签，这时随机挑一张
BARE_MARKER_RE = re.compile(r"[\[［]\s*表情(?:包)?\s*[\]］]")


@dataclass
class Sticker:
    """索引里的一条记录。"""

    sha1: str
    ext: str
    content_type: str
    size: int
    width: int | None = None
    height: int | None = None
    tags: list[str] = field(default_factory=list)
    added_at: float = 0.0
    source: str = ""          # group / c2c，便于回溯来源

    @property
    def filename(self) -> str:
        return f"{self.sha1}{self.ext}"


class StickerLibrary:
    """表情包库：收集、索引、挑选、发送解析。"""

    def __init__(self, root: Path, quota_bytes: int = DEFAULT_QUOTA_BYTES) -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        self.quota_bytes = int(quota_bytes)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, Sticker] = {}
        self._load()

    # ---------------------------------------------------------------- 索引读写
    def _load(self) -> None:
        if not self.index_path.exists():
            return
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for entry in raw.get("stickers", []):
            try:
                st = Sticker(
                    sha1=str(entry["sha1"]),
                    ext=str(entry.get("ext", ".gif")),
                    content_type=str(entry.get("content_type", "")),
                    size=int(entry.get("size", 0)),
                    width=entry.get("width"),
                    height=entry.get("height"),
                    tags=list(entry.get("tags") or []),
                    added_at=float(entry.get("added_at", 0.0)),
                    source=str(entry.get("source", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if (self.files_dir / st.filename).exists():
                self._items[st.sha1] = st

    def save(self) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "quota_bytes": self.quota_bytes,
            "stickers": [
                {
                    "sha1": s.sha1, "ext": s.ext, "content_type": s.content_type,
                    "size": s.size, "width": s.width, "height": s.height,
                    "tags": s.tags, "added_at": s.added_at, "source": s.source,
                }
                for s in self._items.values()
            ],
        }
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_path)

    def reload(self) -> int:
        """从磁盘重读索引，返回当前张数。

        用途：机器人进程启动时会加载一次索引并常驻内存，而联网搜图（在同一个
        进程里）或外面用 stickers.py 打的标签都发生在之后。不重读的话，新标签
        永远要等重启才生效——而标签是模型选择表情的唯一依据。
        开销是读一个小 json，每次请求前调用无压力。
        """
        self._items.clear()
        self._load()
        return len(self._items)

    # ------------------------------------------------------------------ 统计信息
    def __len__(self) -> int:
        return len(self._items)

    def total_bytes(self) -> int:
        return sum(s.size for s in self._items.values())

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for s in self._items.values():
            tags.update(t for t in s.tags if t)
        return sorted(tags)

    def tag_summary(self, limit: int = 30) -> str:
        """给模型看的可选标签列表。"""
        tags = self.all_tags()
        if not tags:
            return ""
        shown = tags[:limit]
        suffix = "" if len(tags) <= limit else f" 等 {len(tags)} 个"
        return "、".join(shown) + suffix

    # -------------------------------------------------------------------- 收集
    @staticmethod
    def sniff_format(data: bytes) -> tuple[str, str]:
        """按文件头判断真实图片格式，返回 ``(content_type, ext)``。

        必须按字节判断，不能信 URL 后缀——很多图床的 ``xxx.jpg`` 实际返回 PNG
        （实测抓到过）。如果照 URL 后缀存成 .jpg，上传 QQ 富媒体时格式不符会被拒，
        而且报错信息不会告诉你是格式问题，很难查。
        """
        if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
            return "image/gif", ".gif"
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", ".jpg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp", ".webp"
        if data.startswith(b"BM"):
            return "image/bmp", ".bmp"
        return "", ""

    @staticmethod
    def _ext_from(content_type: str, filename: str = "") -> str:
        ct = (content_type or "").lower().strip()
        if "gif" in ct:
            return ".gif"
        if "png" in ct:
            return ".png"
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
        if "webp" in ct:
            return ".webp"
        suffix = Path(filename).suffix.lower()
        if suffix in {".gif", ".png", ".jpg", ".jpeg", ".webp"}:
            return ".jpg" if suffix == ".jpeg" else suffix
        return ""

    def should_collect(self, content_type: str, data_len: int,
                       width: int | None, height: int | None) -> bool:
        """判断一张收到的图片该不该进表情库。"""
        ct = (content_type or "").lower().strip()
        if not (ct.startswith("image/") or self._ext_from(ct)):
            return False
        if data_len < MIN_STICKER_BYTES or data_len > MAX_STICKER_BYTES:
            return False
        # 容量保护：满了就不再收新的（避免把磁盘吃光）
        if self.total_bytes() + data_len > self.quota_bytes:
            return False
        if ct in ALWAYS_ACCEPT_TYPES:
            return True
        if ct in PHOTO_TYPES:
            # png/jpg 当表情包用的情况也有，但只收小尺寸的
            if width and height and (width > MAX_PIXELS_SIDE or height > MAX_PIXELS_SIDE):
                return False
            return True
        return False

    def collect(self, data: bytes, content_type: str, filename: str = "",
                width: int | None = None, height: int | None = None,
                source: str = "") -> Sticker | None:
        """收录一张图片。重复内容直接返回已有记录。"""
        if not data:
            return None
        ext = self._ext_from(content_type, filename)
        if not ext:
            return None
        if not self.should_collect(content_type, len(data), width, height):
            return None

        sha1 = hashlib.sha1(data).hexdigest()
        existing = self._items.get(sha1)
        if existing is not None:
            return existing

        target = self.files_dir / f"{sha1}{ext}"
        try:
            target.write_bytes(data)
        except OSError:
            return None

        st = Sticker(
            sha1=sha1, ext=ext, content_type=content_type or "",
            size=len(data), width=width, height=height,
            added_at=time.time(), source=source,
        )
        self._items[sha1] = st
        self.save()
        return st

    # -------------------------------------------------------------------- 挑选
    def pick_random(self, rng: random.Random | None = None) -> Sticker | None:
        if not self._items:
            return None
        rng = rng or random
        return rng.choice(list(self._items.values()))

    def pick_by_tag(self, tag: str) -> Sticker | None:
        """按标签挑一张；标签为空时退回随机。

        模型不一定会写库里的标准标签（爱写「开心」而不是「笑」），所以先过一层
        同义词归一，再做精确/子串匹配；都不中才返回 None（调用方会跳过发图）。
        """
        want = (tag or "").strip().lower()
        if not want:
            return self.pick_random()
        want = TAG_SYNONYMS.get(want, want)
        matched = [
            s for s in self._items.values()
            if want in {t.lower() for t in s.tags}
        ]
        if not matched:
            # 退一步：标签做子串匹配，容忍模型写「开心」而库里有「开心_猫」
            matched = [
                s for s in self._items.values()
                if any(want in t.lower() or t.lower() in want for t in s.tags)
            ]
        if not matched:
            return None
        return random.choice(matched)

    def read_bytes(self, st: Sticker) -> bytes | None:
        try:
            return (self.files_dir / st.filename).read_bytes()
        except OSError:
            return None

    def add_tags(self, tags: Iterable[str], *, sha1: str | None = None,
                 index: int | None = None) -> int:
        """给表情打标签：指定 sha1，或按下标（1-based）指定一条。"""
        targets: list[Sticker] = []
        if sha1:
            st = self._items.get(sha1)
            if st:
                targets.append(st)
        elif index is not None:
            items = list(self._items.values())
            if 1 <= index <= len(items):
                targets.append(items[index - 1])
        else:
            targets = list(self._items.values())
        clean = {t.strip() for t in tags if t and t.strip()}
        if not clean:
            return 0
        n = 0
        for st in targets:
            before = set(st.tags)
            st.tags = sorted(before | clean)
            if set(st.tags) != before:
                n += 1
        if n:
            self.save()
        return n


def parse_sticker_marker(text: str) -> tuple[str, str | None, bool]:
    """从模型回复里摘出表情包标记。

    返回 ``(去掉标记的正文, 标签或 None, 是否要求发送)``。

    模型偶尔会写多个标记（``[表情:开心]...[表情:无语]``）。这时只认第一个做标签，
    但**所有**标记都必须摘掉——只处理第一个的话，剩下的会以 ``[表情:无语]``
    的字面量发给用户。
    """
    if not text:
        return "", None, False

    tags = TAG_MARKER_RE.findall(text)
    found = bool(tags)
    text = TAG_MARKER_RE.sub("", text)

    if BARE_MARKER_RE.search(text):
        found = True
        text = BARE_MARKER_RE.sub("", text)

    # 标记被摘掉后可能留下多余空格或空行，顺手收拾干净
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, (tags[0].strip() if tags else None), found


# 标签 → 简短含义。用途是让模型知道每个标签到底代表什么情绪，
# 而不是只看一个词瞎猜。只列库内真实存在的标签，避免指向空标签。
TAG_MEANINGS: dict[str, str] = {
    "笑": "乐",
    "夸赞": "夸你",
    "得意": "被夸骄傲",
    "亲昵": "亲亲抱抱",
    "无语": "吐槽嫌弃",
    "生气": "气鼓鼓",
    "疑问": "不懂",
    "吃惊": "被吓到",
    "哭": "委屈难过",
    "困": "困累",
    "摆烂": "躺平",
    "再见": "告别",
}

# 模型不一定写标准标签（爱写「开心」「高兴」「抱抱」），这里归一化到上面的标准词，
# 免得挑不到图就干脆不发了。键一律小写。
TAG_SYNONYMS: dict[str, str] = {
    "开心": "笑", "高兴": "笑", "好笑": "笑", "搞笑": "笑", "哈哈": "笑",
    "大笑": "笑", "乐": "笑", "笑死": "笑", "憨笑": "笑",
    "称赞": "夸赞", "表扬": "夸赞", "夸奖": "夸赞", "赞美": "夸赞",
    "骄傲": "得意", "自豪": "得意", "得瑟": "得意", "嘚瑟": "得意", "炫耀": "得意",
    "抱抱": "亲昵", "亲亲": "亲昵", "贴贴": "亲昵", "撒娇": "亲昵", "喜欢": "亲昵", "示好": "亲昵",
    "嫌弃": "无语", "吐槽": "无语", "翻白眼": "无语", "无奈": "无语", "服了": "无语", "汗": "无语",
    "愤怒": "生气", "火大": "生气", "气鼓鼓": "生气", "不爽": "生气", "怒": "生气", "恼火": "生气",
    "疑惑": "疑问", "困惑": "疑问", "不解": "疑问", "懵": "疑问", "问号": "疑问",
    "震惊": "吃惊", "惊讶": "吃惊", "惊呆": "吃惊", "吓到": "吃惊", "意外": "吃惊",
    "委屈": "哭", "难过": "哭", "伤心": "哭", "流泪": "哭", "呜呜": "哭",
    "疲惫": "困", "好累": "困", "累了": "困", "睡觉": "困", "打哈欠": "困", "困了": "困",
    "躺平": "摆烂", "放弃": "摆烂", "摸鱼": "摆烂", "开摆": "摆烂",
    "拜拜": "再见", "告别": "再见", "走了": "再见", "溜了": "再见", "告辞": "再见",
}


def build_sticker_hint(summary: str, enabled: bool = True, count: int = 0,
                       web_enabled: bool = False, tags: list[str] | None = None) -> str:
    """构造要追加到 system prompt 末尾的说明。

    最重要的一条是**语气匹配**：早期版本只写了"合适才加"，模型就会把它当成
    "可以随便配一张"，于是出现安慰的话配一张生气表情这种错配。所以这里必须
    把"表情情绪要跟这句话的语气一致"写明，并且给出每个标签的含义。

    分支对应真实状态，不能合并：
      - 库空 + 能联网：教它用 search_sticker 现找
      - 库空 + 不能联网：明确禁止写标记
      - 有标签：列出标签 + 含义，强调语气一致
    """
    if not enabled:
        return ""

    if count <= 0 and not summary:
        if web_enabled:
            return (
                "\n\n【表情包】本地表情包库还是空的。想配图时先调用 search_sticker "
                "工具（关键词 2~6 字，如「猫猫开心」），它会下载并收藏，"
                "然后你用 [表情:关键词] 把图发出去。"
                "大部分回复都可以配一张，只要情绪对得上。"
            )
        return (
            "\n\n【表情包】目前表情包库还是空的（会自动从群聊里收集），"
            "所以现在不要写 [表情:...] 标记。"
        )

    # 只列真有内容的标签：调用方应传入库内实际标签；没传就退回全表
    have = list(tags) if tags else list(TAG_MEANINGS)
    tag_defs = [f"{t}={TAG_MEANINGS[t]}" for t in have if t in TAG_MEANINGS]
    other = [t for t in have if t not in TAG_MEANINGS]
    tag_defs.extend(other)
    # 分 3 行排（每行约 5 条），行太长模型容易看漏
    per = max(1, (len(tag_defs) + 2) // 3)
    tag_lines = "".join(
        "  " + "　".join(tag_defs[i:i + per]) + "\n"
        for i in range(0, len(tag_defs), per)
    )

    # 精简版：原先 675 字的说明会拖慢每轮推理（本机模型 24% 在 CPU 上跑），
    # 这里压到 ~280 字，但**不能砍掉防错配规则**——那是调了很久才稳下来的。
    base = (
        "\n\n【表情包】回复末尾可加 [表情:标签] 配图，标签只能用：\n"
        + tag_lines +
        "按用户的情绪选：开心->笑；夸你->夸赞/得意；骂你->生气/无语；\n"
        "烦躁->无语/摆烂；撒娇求安慰->亲昵；你说不准->疑问；被吓到->吃惊；\n"
        "难过->哭；累了->困；道别->再见。\n"
        "别为了哄人就挑无关标签（被骂要用「生气」而不是「亲昵」）。\n"
        "**默认每条回复都配一张**（它只多占一条，不影响正文）；\n"
        "只有这句话确实没有情绪时才不加；标签必须用上面列的，别自编；别连用同一张。"
    )

    if web_enabled:
        base += (
            " 想更贴切、更新鲜，或上面标签都不合适时，可以调用 search_sticker "
            "现找一张（关键词 2~6 字），再用 [表情:关键词] 发出去。"
        )
    return base
