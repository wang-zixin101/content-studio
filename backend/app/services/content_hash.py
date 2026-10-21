"""内容身份哈希（Stage 12 新增）。

------------------------------------------------------------------
定义严格锁定，**不允许**任何"顺手归一化"
------------------------------------------------------------------
    sha256(raw_text.encode("utf-8"))

明确不做：

- 不 strip（首尾空白是不同的内容）
- 不做换行归一化（\\r\\n 与 \\n 是不同字节）
- 不做 Unicode 归一化（NFC / NFD 不合并）
- 不做转小写
- 不 trim 中间空白

理由：这个 hash 的唯一用途是「证明外部文件里那条正文，
和库里那个 content_id 指的是同一条内容」。任何归一化都会
让「文件与库里其实不一致」的情况被静默放过。
"""

from __future__ import annotations

import hashlib

PREFIX = "sha256:"


def content_hash(raw_text: str) -> str:
    """返回 `sha256:<hex>`。空串也会得到一个确定的 hash。"""
    payload = (raw_text or "").encode("utf-8")
    return PREFIX + hashlib.sha256(payload).hexdigest()


def content_hash_bytes(raw_text: str) -> bytes:
    """只要原始字节（需要自己比较时用）。"""
    return (raw_text or "").encode("utf-8")


def content_hash_matches(raw_text: str, expected: str) -> bool:
    """常量时间无关的等值比较；大小写不敏感地容错常见写法。

    只做「`sha256:` 前缀大小写」与「hex 大小写」的容错 ——
    这两种写法差异来自工具差异，不改变内容本身。
    """
    if not expected:
        return False
    got = content_hash(raw_text)
    return got.lower() == expected.strip().lower()
