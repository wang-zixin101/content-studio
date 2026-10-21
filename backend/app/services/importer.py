"""批量导入解析：TXT / Markdown / CSV → 文本条目列表。

分段规则：
- TXT / Markdown：**仅以单独一行 `===POST===` 作为帖子分隔符**。
  帖内空行与多段结构原样保留；`---` 是合法的 Markdown 水平线与 YAML
  frontmatter 语法，因此**不再**作为分隔符，一律当正文保留。
- CSV：一条记录对应一篇内容。

CSV 正文列识别（关键）：
1. 表头命中 text/content/body/post/tweet/memo/note/文案/正文/内容/文本
   → 整份 CSV **固定**读取该列，不逐行猜。
2. 无可识别表头的单列 CSV → 一行一条。
3. 无可识别表头的多列 CSV → fallback：排除 URL、纯数字、日期等元数据后取最长候选。

解码顺序：utf-8-sig → utf-8 → gb18030（gbk 的超集）。
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, NamedTuple

# 仅匹配「单独一行 ===POST===」（允许两侧空白、大小写不敏感）
_SEPARATOR_RE = re.compile(r"^\s*=+\s*POST\s*=+\s*$", re.IGNORECASE)

MIN_LEN = 5

# CSV 正文列表头候选
_TEXT_HEADERS = {
    "text", "content", "body", "post", "tweet", "memo", "note",
    "文案", "正文", "内容", "文本",
}

# 明显属于元数据的单元格
_URL_RE = re.compile(r"^(https?://\S+|www\.\S+|\S+\.(?:com|cn|org|net|io|me|co|xyz|top)(?:/\S*)?)$", re.I)
_NUMERIC_RE = re.compile(r"^\d[\d,.\s]*$")
_DATE_RE = re.compile(
    r"^\d{4}[-/年.]\d{1,2}([-/月.]\d{1,2}日?)?([T ]\d{1,2}:\d{2}(:\d{2})?)?$"
    r"|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$"
)


def _clean_items(items: list[str]) -> list[str]:
    """去掉首尾空白并过滤过短/空条目；保留条目内部结构。"""
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if len(text) >= MIN_LEN:
            out.append(text)
    return out


def _looks_like_metadata(cell: str) -> bool:
    """URL / 纯数字 / 日期时间 —— 不可能是正文。"""
    text = (cell or "").strip()
    if not text:
        return True
    if _URL_RE.match(text):
        return True
    if _NUMERIC_RE.match(text):
        return True
    if _DATE_RE.match(text):
        return True
    return False


def _is_separator(line: str) -> bool:
    return bool(_SEPARATOR_RE.match(line))


def parse_text(content: str) -> list[str]:
    """TXT / Markdown：仅以单独一行 `===POST===` 分隔帖子。"""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")

    blocks: list[str] = []
    current: list[str] = []
    for line in normalized.split("\n"):
        if _is_separator(line):
            blocks.append("\n".join(current))
            current = []
        else:
            current.append(line)
    blocks.append("\n".join(current))

    return _clean_items(blocks)


def parse_csv(content: str) -> list[str]:
    """CSV：识别正文列并整份固定读取；无表头时按列数降级处理。

    对 BOM 免疫：即使调用方没走 _decode（例如直接把已解码字符串传进来），
    也不会因为表头带 \\ufeff 而识别失败、把表头行当成第一条正文。
    """
    content = content.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = [row for row in reader if any((c or "").strip() for c in row)]
    if not rows:
        return []

    header = [(c or "").strip().lower() for c in rows[0]]
    text_idx = next((i for i, name in enumerate(header) if name in _TEXT_HEADERS), None)

    # 1) 命中正文表头：整份 CSV 固定读该列（不得逐行重新猜）
    if text_idx is not None:
        items = [
            row[text_idx].strip()
            for row in rows[1:]
            if text_idx < len(row) and row[text_idx].strip()
        ]
        return _clean_items(items)

    # 2) 无可识别表头 + 单列：一行一条
    if len(rows[0]) == 1:
        return _clean_items([row[0] for row in rows])

    # 3) 无可识别表头 + 多列：排除明显元数据后取最长候选
    items: list[str] = []
    for row in rows:
        candidates = [
            c.strip()
            for c in row
            if c and c.strip() and not _looks_like_metadata(c.strip())
        ]
        if not candidates:
            continue
        items.append(max(candidates, key=len))
    return _clean_items(items)


def _decode(data: bytes) -> str:
    """优先 UTF-8 BOM，其次 UTF-8，最后 gb18030（gbk 超集）。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- JSON（Xporter）

class ImporterError(ValueError):
    """导入解析失败。消息直接面向用户，必须说明具体原因。"""


class ParsedItem(NamedTuple):
    """一条待导入内容：正文 + 可选的来源元数据。"""

    text: str
    metadata: dict[str, Any] = {}


# 顶层嵌套数组的候选键（只认明确已知的结构，不做递归猜测）
_POST_ARRAY_KEYS = ("posts", "tweets", "items", "data")

# 正文候选字段，按优先级。刻意**不包含** tweet_url / id / author / 日期 / 媒体 URL
_TEXT_FIELDS = ("text", "full_text", "tweet_text", "post_text")

# 显示名称的 metadata 承载键。
# 它**不是** contents 表的列（authors.display_name 才是落点），因此刻意
# 不出现在 repository._SOURCE_META_KEYS 里；由 set_content_source_meta 单独消费。
AUTHOR_DISPLAY_NAME_KEY = "author_display_name"

# 元数据字段映射：原始键 -> 入库键（这些绝不拼进 raw_text）
_META_FIELDS = (
    ("tweet_url", "source_url"),
    ("author_username", "author_username"),
    ("author_name", AUTHOR_DISPLAY_NAME_KEY),
    ("created_at", "posted_at"),
    ("type", "post_type"),
)

# ---------------------------------------------------------------- 扩展来源字段
#
# 任务 04-A 核查实测（10 份真实 Xporter 导出 / 4307 条帖子）：
#   conversation_id    100% 存在
#   language           100% 存在（zh 为主，另有 en/ja/pl/cs/es/lv/und）
#   reply_to_username  3/10 文件
#   hashtags           4/10 文件
#   urls               7/10 文件
#   media_urls         10/10 文件
# 旧导入器把这六个字段全部丢弃，于是「这条是不是回复」「这条是不是中文」
# 「原文引用了哪个链接」在数据层无法回答。
#
# 现在它们统一进 contents.source_meta（JSON），不建新表。

#: 标量型扩展字段：原始键 -> 入库键
_EXTRA_META_SCALARS = (
    ("conversation_id", "conversation_id"),
    ("language", "language"),
    ("reply_to_username", "reply_to_username"),
)

#: 列表型扩展字段：原始键 -> 入库键。元素必须是字符串，去空去重保序。
_EXTRA_META_LISTS = (
    ("hashtags", "hashtags"),
    ("urls", "urls"),
    ("media_urls", "media_urls"),
)

#: 单个列表字段最多保留的元素数（防个别文件塞进几千个 URL 撑爆 source_meta）
MAX_META_LIST_ITEMS = 40

# ---------------------------------------------------------------- 表现数据（Performance V1）
#
# 纪律：
#   1. 只是把 Xporter 文件里**已有**的公开计数原样保存，不做任何计算/打分/排名。
#   2. 0 与 NULL 严格区分：0 = 已知为 0；缺失 / 无法解析 / 负数 = NULL（未知）。
#      绝不把 "???" 静默变成 0。
#   3. 单个字段坏掉不影响整篇 content 导入：view_count 无效 → NULL，
#      favorite_count 照样保存。
#   4. 表现数据对 source 无要求：mine 与 benchmark 都可以有。

# metadata 里承载表现数据的保留键。
# 之所以放在 metadata 内：parse → 预览 → commit 这条链路本来就整份透传
# metadata，复用它不必再让前端多维护一条必须下标对齐的数组。
PERFORMANCE_KEY = "performance"

# Xporter 原始键 -> 入库键（计数类）
_PERFORMANCE_COUNT_FIELDS = (
    ("view_count", "view_count"),
    ("favorite_count", "favorite_count"),
    ("reply_count", "reply_count"),
    ("retweet_count", "retweet_count"),
    ("quote_count", "quote_count"),
    ("bookmark_count", "bookmark_count"),
)

# 只要出现其中任意一个键，就认为这条记录「带有表现数据」
_PERFORMANCE_SOURCE_KEYS = (
    "view_count", "favorite_count", "reply_count", "retweet_count",
    "quote_count", "bookmark_count", "is_pinned", "media_type",
)


def parse_count(value: Any) -> int | None:
    """表现计数字段：只接受**非负整数**（int / 整数 float / 纯数字字符串）。

    返回 None 表示「未知」，调用方必须原样存 NULL —— 不得改写成 0。
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value if value >= 0 else None

    if isinstance(value, float):
        # 12.0 → 12；12.5 / -1.0 → 无效
        if value < 0 or not value.is_integer():
            return None
        return int(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    return None


def parse_is_pinned(value: Any) -> bool:
    """置顶标记：Xporter 置顶帖 is_pinned=true，普通帖该字段通常直接不存在。

    缺失 / 无法识别 → False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def parse_media_type(value: Any) -> str | None:
    """媒体类型：原样保存可选字符串（photo / video …），不做枚举系统。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_performance(post: dict[str, Any]) -> dict[str, Any] | None:
    """从一条 Xporter 记录里抽取表现数据。

    没有任何表现字段 → 返回 None（不建 performance 记录）。
    有字段但值非法 → 对应字段为 None，记录照建（不牵连 content 导入）。
    """
    if not isinstance(post, dict):
        return None
    if not any(key in post for key in _PERFORMANCE_SOURCE_KEYS):
        return None

    data: dict[str, Any] = {}
    for src_key, dst_key in _PERFORMANCE_COUNT_FIELDS:
        data[dst_key] = parse_count(post.get(src_key))
    data["is_pinned"] = parse_is_pinned(post.get("is_pinned"))
    data["media_type"] = parse_media_type(post.get("media_type"))
    return data


def _find_posts(payload: Any) -> list[dict[str, Any]] | None:
    """定位帖子数组。找不到明确结构就返回 None，绝不把整份 JSON 当正文。"""
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict)]
    if isinstance(payload, dict):
        for key in _POST_ARRAY_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [p for p in value if isinstance(p, dict)]
    return None


def _pick_body(post: dict[str, Any]) -> str:
    """只从白名单正文字段取文本；取不到就返回空串。"""
    for field in _TEXT_FIELDS:
        value = post.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _string_list(value: Any) -> list[str]:
    """把 Xporter 的数组字段规整成字符串列表（去空、去重、保序、限量）。"""
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= MAX_META_LIST_ITEMS:
            break
    return out


def extract_source_extras(post: dict[str, Any]) -> dict[str, Any]:
    """抽取扩展来源字段。**只保存文件里真实存在的值**，不猜、不补默认。"""
    out: dict[str, Any] = {}
    for src_key, dst_key in _EXTRA_META_SCALARS:
        value = post.get(src_key)
        if isinstance(value, str) and value.strip():
            out[dst_key] = value.strip()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[dst_key] = str(value)
    for src_key, dst_key in _EXTRA_META_LISTS:
        values = _string_list(post.get(src_key))
        if values:
            out[dst_key] = values
    return out


def parse_json(content: str) -> list[ParsedItem]:
    """解析 Xporter 导出的帖子 JSON。

    三种失败情形必须给出不同且明确的错误，绝不悄悄退化成 parse_text：
    - 语法错误        -> JSON 文件无法解析
    - 找不到帖子数组  -> 未识别到 Xporter 帖子数据结构
    - 有帖子但无正文  -> 未找到有效帖子正文
    """
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as err:
        raise ImporterError("JSON 文件无法解析") from err

    posts = _find_posts(payload)
    if posts is None:
        raise ImporterError("未识别到 Xporter 帖子数据结构")

    items: list[ParsedItem] = []
    seen: set[str] = set()

    for post in posts:
        # 同一次解析内按 id 去重，避免同一帖子重复对象被导入多次
        raw_id = post.get("id")
        external_id = str(raw_id).strip() if raw_id is not None else ""
        if external_id:
            if external_id in seen:
                continue
            seen.add(external_id)

        body = _pick_body(post)
        if len(body) < MIN_LEN:
            continue

        metadata: dict[str, Any] = {}
        if external_id:
            metadata["external_id"] = external_id
        for src_key, dst_key in _META_FIELDS:
            value = post.get(src_key)
            if isinstance(value, str) and value.strip():
                metadata[dst_key] = value.strip()
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                metadata[dst_key] = value

        # 表现数据：只在 Xporter 确实带了这些字段时才出现
        performance = extract_performance(post)
        if performance is not None:
            metadata[PERFORMANCE_KEY] = performance

        # 扩展来源字段（语言 / 回复关系 / 链接 / 媒体 / 话题标签 / 会话 id）
        metadata.update(extract_source_extras(post))

        items.append(ParsedItem(text=body, metadata=metadata))

    if not items:
        raise ImporterError("未找到有效帖子正文")

    return items


def parse_upload(filename: str, data: bytes) -> list[ParsedItem]:
    """按扩展名分派解析，统一返回带元数据的条目。"""
    name = (filename or "").lower()
    content = _decode(data)

    if name.endswith(".json"):
        return parse_json(content)
    if name.endswith(".csv"):
        return [ParsedItem(t, {}) for t in parse_csv(content)]
    # .txt / .md / .markdown / 其他纯文本
    return [ParsedItem(t, {}) for t in parse_text(content)]
