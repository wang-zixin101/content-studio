"""外部分析导入 V1（Stage 12）。

    导出任务 JSONL → 外部模型分析 → 导入结果 JSONL
        → 文件级校验 → 单条严格校验 → Preview（0 写入）
        → Confirm（整批一个事务）

------------------------------------------------------------------
为什么是独立模块而不是复用 content import
------------------------------------------------------------------
现有 `services/importer.py` + `import_batches` 的语义是「把外部**内容**搬进工作台」：
一条正文 = 一个 content、按 external_id 去重、可按批次撤销（会删内容）。
外部分析的语义是「把**分析结果**贴回已有 content」，方向相反，
塞进去会污染导入记录页与撤销语义。

因此本模块独立，只复用：
    - importer._decode 的 BOM / 编码兼容
    - batch.build_jsonl 的「列表 → JSONL bytes」范式
    - repository 的 400 分片批量查询范式

------------------------------------------------------------------
落库
------------------------------------------------------------------
**不新建第二套分析系统**：最终统一走
    analysis_spec.validate_analysis → persistence.persist_analysis_result
    → persistence.persist_analysis_result
与 realtime / Batch 完全同一套副作用。

------------------------------------------------------------------
0 AI
------------------------------------------------------------------
本模块**没有任何 provider 调用**。导出是纯 SQL + 拼接，
Preview 是纯读写比较，Confirm 是纯写入。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from itertools import chain
from typing import Any, Iterable, Iterator

from app import repository as repo
from app.config import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    EXTERNAL_ANALYSIS_FORMAT_VERSION,
    EXTERNAL_MODEL_MAX_LEN,
    EXTERNAL_MODEL_PREFIX,
    EXTERNAL_MODEL_UNKNOWN,
    EXTERNAL_RESULT_FORMAT,
    EXTERNAL_TASK_FORMAT,
    settings,
)
from app.db import get_conn
from app.services.analysis_spec import (
    ANALYSIS_SCHEMA_HASH,
    SpecError,
    validate_analysis,
)
from app.services.analyzer import compute_length_chars
from app.services.content_hash import content_hash, content_hash_matches
from app.services.importer import _decode  # 复用 BOM / gb18030 兼容
from app.services.persistence import persist_analysis_result
from app.services.errors import ServiceError

logger = logging.getLogger("content_studio.external_analysis")

class ConflictError(ServiceError):
    """Preview 与 Confirm 之间库状态发生变化 —— 整批不写（HTTP 409）。"""

    status_code = 409


# 单条分类
CLS_VALID = "valid"
CLS_INVALID_JSON = "invalid_json"
CLS_SCHEMA_ERROR = "schema_error"
CLS_NOT_FOUND = "content_not_found"
CLS_HASH_MISMATCH = "content_hash_mismatch"
CLS_ALREADY = "already_analyzed"
CLS_BUSY = "busy"
CLS_DUPLICATE = "duplicate_result"

BUSY_STATES = ("queued", "processing")

# 交接（handoff）分类
HO_TRANSFERRED = "transferred"
HO_NOT_QUEUED = "not_queued"
HO_PROCESSING = "processing"
HO_NOT_FOUND = "not_found"

# 默认导出状态：未分析 + 失败（失败通常代表上一轮没拿到结果）
DEFAULT_EXPORT_STATES = ("unprocessed", "failed")

# 可导出状态：queued / processing **不在其中**。
#
# 它们是内部分析「在飞」的任务：直接导出去分析，结果导回时 100% 命中
# busy（「正在分析中，即使勾选覆盖也不导入」），等于导出一批永远导不回来的任务。
# 正确路径是先走 handoff 把 queued 转回 unprocessed。
EXPORTABLE_STATES = ("unprocessed", "failed", "analyzed", "cancelled")

# 允许出现在筛选参数里的全部状态（含在飞状态，供 handoff 与旧调用方使用）
ALLOWED_EXPORT_STATES = ("unprocessed", "failed", "analyzed", "queued", "processing", "cancelled")

# 在飞状态：不可直接导出
BUSY_EXPORT_STATES = ("queued", "processing")

# 数量上限：**产品上已取消**。
#   limit = 0（默认）→ 不限量：有多少符合条件的帖子就导出多少条
#   正整数          → 仍然按该数截断（仅服务显式传参的旧客户端 / 脚本，UI 不再使用）
# 之所以保留参数而不是删掉：老链接 / 脚本带 limit=50 仍然可用，不会 500。
EXPORT_LIMITS = (20, 50, 100)  # 仅供旧客户端文档参考，**不再用于校验**
EXPORT_DEFAULT_LIMIT = 0
EXPORT_NO_LIMIT = 0

# 单条记录文本上限（防止超大文件把内存打爆）——只做保护，不做业务限制
MAX_TEXT_CHARS = 20_000

# 任务文件每行除正文外的固定开销（键名 + sha256 hash + 换行），用于体积预估
_TASK_ROW_OVERHEAD_BYTES = 150
_META_LINE_BYTES = 240


# ================================================================ 任务导出

def resolve_export_states(
    scope: str | None = None,
    states: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, ...]:
    """把「范围」或「显式状态列表」解析成最终导出状态。

    三种来源互斥且确定，绝不做猜测：

        scope="pending" → DEFAULT_EXPORT_STATES（仅待分析）
        scope="all"     → EXPORTABLE_STATES（全部可导出，仍排除在飞状态）
        states=[...]    → 逐项校验；命中在飞状态**明确报错**，绝不静默剔除
        都没给          → DEFAULT_EXPORT_STATES
    """
    if scope == "pending":
        return DEFAULT_EXPORT_STATES
    if scope == "all":
        return EXPORTABLE_STATES

    if states:
        cleaned = tuple(str(s).strip() for s in states if str(s).strip())
        if cleaned:
            bad = [s for s in cleaned if s not in ALLOWED_EXPORT_STATES]
            if bad:
                raise ServiceError(f"不支持的分析状态：{list(bad)}")
            busy = [s for s in cleaned if s in BUSY_EXPORT_STATES]
            if busy:
                raise ServiceError(
                    f"{' / '.join(busy)} 不能直接导出：请先在「转为外部分析」里把它们转回未分析"
                )
            return cleaned

    return DEFAULT_EXPORT_STATES


def _normalize_author_ids(author_ids: list[int] | tuple[int, ...] | None) -> list[int]:
    """去重 + 保序 + 强转 int。空列表 = 不按作者过滤（等同「全部作者」）。"""
    if not author_ids:
        return []
    out: list[int] = []
    for raw in author_ids:
        if raw is None:
            continue
        value = int(raw)
        if value not in out:
            out.append(value)
    return out


def _author_filter_sql(
    *,
    author_id: int | None,
    author_ids: list[int] | tuple[int, ...] | None,
    include_no_author: bool,
) -> tuple[list[str], list[Any]]:
    """作者过滤条件。

    - 不指定作者        → 不加任何条件（「全部作者」，天然包含无作者内容）
    - 指定作者          → author_id IN (...)，**不含**无作者内容
    - 指定作者 + 含无作者 → (author_id IN (...) OR author_id IS NULL)
    """
    if author_id is not None:
        return ["c.author_id = ?"], [int(author_id)]

    ids = _normalize_author_ids(author_ids)
    if not ids:
        return [], []
    placeholders = ",".join("?" for _ in ids)
    if include_no_author:
        return [f"(c.author_id IN ({placeholders}) OR c.author_id IS NULL)"], list(ids)
    return [f"c.author_id IN ({placeholders})"], list(ids)


def _limit_sql(limit: int | None) -> tuple[str, list[Any]]:
    """limit <= 0（或 None）→ 不加 LIMIT，即不限量。"""
    if limit is None:
        return "", []
    value = int(limit)
    if value <= 0:
        return "", []
    return " LIMIT ?", [value]


def select_export_targets(
    *,
    source: str | None = None,
    author_id: int | None = None,
    author_ids: list[int] | tuple[int, ...] | None = None,
    include_no_author: bool = False,
    states: tuple[str, ...] = DEFAULT_EXPORT_STATES,
    limit: int = EXPORT_DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """挑出待导出的内容（纯 SQL / 0 AI）。

    **不限量**：默认 limit=0 → SQL 不带 LIMIT，导出条数恒等于符合条件的真实条数。
    """
    bad = [s for s in states if s not in ALLOWED_EXPORT_STATES]
    if bad:
        raise ServiceError(f"不支持的分析状态：{list(bad)}")
    busy = [s for s in states if s in BUSY_EXPORT_STATES]
    if busy:
        raise ServiceError(f"{' / '.join(busy)} 不能直接导出")

    placeholders = ",".join("?" for _ in states)
    where = [f"COALESCE(c.analysis_state, 'unprocessed') IN ({placeholders})"]
    params: list[Any] = list(states)
    if source:
        where.append("c.source = ?")
        params.append(source)

    author_clause, author_params = _author_filter_sql(
        author_id=author_id, author_ids=author_ids, include_no_author=include_no_author
    )
    where.extend(author_clause)
    params.extend(author_params)

    limit_clause, limit_params = _limit_sql(limit)
    sql = (
        "SELECT c.id, c.external_id, c.raw_text "
        "  FROM contents c "
        f" WHERE {' AND '.join(where)} "
        " ORDER BY c.id ASC" + limit_clause
    )
    params.extend(limit_params)

    with get_conn(settings.db_path) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "content_id": int(r["id"]),
            "external_id": r["external_id"],
            "content_hash": content_hash(r["raw_text"] or ""),
            "text": r["raw_text"] or "",
        }
        for r in rows
    ]


def export_scope_summary(
    *,
    source: str | None = None,
    author_id: int | None = None,
    author_ids: list[int] | tuple[int, ...] | None = None,
    include_no_author: bool = False,
    states: tuple[str, ...] = DEFAULT_EXPORT_STATES,
) -> dict[str, Any]:
    """导出前的**真实**范围统计（一次查询，0 写入 / 0 AI）。

    返回：

        matching_count  符合当前筛选的真实条数（**不受任何上限影响**）
        author_count    这些条数涉及的不同作者数（不含无作者）
        no_author_count 其中无作者内容的条数
        text_bytes      正文 UTF-8 字节数（用于预估文件体积）
        estimated_bytes 预估任务文件字节数
        state_counts    同一作者/来源范围下**各状态**的条数（不受 states 限制）
        export_states   本次实际使用的状态
    """
    bad = [s for s in states if s not in ALLOWED_EXPORT_STATES]
    if bad:
        raise ServiceError(f"不支持的分析状态：{list(bad)}")

    placeholders = ",".join("?" for _ in states)
    where = [f"COALESCE(c.analysis_state, 'unprocessed') IN ({placeholders})"]
    params: list[Any] = list(states)
    if source:
        where.append("c.source = ?")
        params.append(source)

    author_clause, author_params = _author_filter_sql(
        author_id=author_id, author_ids=author_ids, include_no_author=include_no_author
    )
    where.extend(author_clause)
    params.extend(author_params)

    sql = (
        "SELECT COUNT(*) AS n, "
        "       COUNT(DISTINCT c.author_id) AS authors, "
        "       SUM(CASE WHEN c.author_id IS NULL THEN 1 ELSE 0 END) AS no_author, "
        "       COALESCE(SUM(LENGTH(CAST(COALESCE(c.raw_text, '') AS BLOB))), 0) AS text_bytes "
        "  FROM contents c "
        f" WHERE {' AND '.join(where)}"
    )

    with get_conn(settings.db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        state_counts = repo.count_contents_by_analysis_state(
            conn,
            source=source,
            author_id=author_id,
            author_ids=_normalize_author_ids(author_ids),
            include_no_author=include_no_author,
        )

    matching = int(row["n"] or 0)
    text_bytes = int(row["text_bytes"] or 0)
    return {
        "matching_count": matching,
        "author_count": int(row["authors"] or 0),
        "no_author_count": int(row["no_author"] or 0),
        "text_bytes": text_bytes,
        "estimated_bytes": (
            0 if matching == 0
            else text_bytes + matching * _TASK_ROW_OVERHEAD_BYTES + _META_LINE_BYTES
        ),
        "state_counts": state_counts,
        "export_states": list(states),
    }


def state_counts(
    *,
    source: str | None = None,
    author_id: int | None = None,
    author_ids: list[int] | tuple[int, ...] | None = None,
    include_no_author: bool = False,
) -> dict[str, int]:
    """当前筛选下各 analysis_state 的计数（一次 GROUP BY，0 写入）。"""
    with get_conn(settings.db_path) as conn:
        return repo.count_contents_by_analysis_state(
            conn,
            source=source,
            author_id=author_id,
            author_ids=_normalize_author_ids(author_ids),
            include_no_author=include_no_author,
        )


def _meta_block(count: int) -> dict[str, Any]:
    return {
        "format": EXTERNAL_TASK_FORMAT,
        "format_version": EXTERNAL_ANALYSIS_FORMAT_VERSION,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "schema_hash": ANALYSIS_SCHEMA_HASH,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "count": count,
    }


def build_task_jsonl(targets: list[dict[str, Any]]) -> bytes:
    """任务 JSONL：第一行 _meta，之后一行一个任务。UTF-8。"""
    lines = [json.dumps({"_meta": _meta_block(len(targets))}, ensure_ascii=False)]
    for t in targets:
        lines.append(
            json.dumps(
                {
                    "content_id": t["content_id"],
                    "external_id": t["external_id"],
                    "content_hash": t["content_hash"],
                    "text": t["text"],
                },
                ensure_ascii=False,
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def task_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    return f"analysis_tasks_{stamp}.jsonl"


# ================================================================ 解析

class FileLevelError(Exception):
    """文件级错误：整份文件不允许 Confirm。"""


def _strip_code_fence(text: str) -> str:
    """容忍整份文件外面包了一对 ``` / ```json 围栏。"""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.split("\n")
    if len(lines) < 2:
        return text
    head = lines[0].strip().lstrip("`").strip().lower()
    if head not in ("", "json", "jsonl"):
        return text
    if lines[-1].strip() != "```":
        return text
    return "\n".join(lines[1:-1])


def parse_result_file(data: bytes) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """解析结果文件。

    返回 (meta, records, parse_errors)：
      meta         第一行 _meta（JSON 格式可以从顶层取）
      records      [{content_id, content_hash, analysis, line}]
      parse_errors [{line, reason, message}]  —— 单行损坏只记录，不让整文件失败
    """
    if not data:
        raise FileLevelError("文件为空")

    try:
        text = _decode(data)
    except Exception as err:  # 编码彻底失败
        raise FileLevelError(f"无法解码文件：{err}") from err

    text = _strip_code_fence(text)
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        raise FileLevelError("文件没有可用内容")

    # ---- 形式判定（判定规则必须确定，不做猜测）----
    #   1) 整份文件能一次性 json.loads 成数组 → JSON array 形式
    #   2) 能一次性 json.loads 成对象，且带 items / _meta → JSON 文档形式
    #   3) 整体不是合法 JSON，但**首行带 items 键** → 意图是 JSON 文档，损坏即文件级错误
    #   4) 其余 → JSONL（逐行，单行损坏只记 invalid_json）
    if stripped.startswith("["):
        return _parse_json_form(stripped)

    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            first_line = next(
                (ln for ln in text.split("\n") if ln.strip()), ""
            )
            if '"items"' in first_line or "'items'" in first_line:
                return _parse_json_form(stripped)
            return _parse_jsonl_form(text)
        if isinstance(parsed, (list, dict)):
            return _parse_json_form(stripped)

    return _parse_jsonl_form(text)


def _sniff_encoding(prefix: bytes) -> str:
    """只看开头几个字节判断编码（BOM）。无 BOM 默认 UTF-8。"""
    if prefix.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if prefix[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return "utf-8"


def _decode_line(raw: bytes, encoding: str) -> str:
    """单行解码：指定编码 → UTF-8 → gb18030 → replace。

    逐行兜底是安全的：按 `\\n` 切分不会切断多字节序列
    （UTF-8 续字节 ≥ 0x80；GB18030 尾字节不含 0x0A）。
    """
    if encoding != "utf-8":
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("gb18030")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def _iter_text_lines(fp: Any, chunk_size: int = 1 << 16) -> Iterator[str]:
    """从二进制流逐行产出文本 —— **整份文件永不进内存**。

    内存占用恒为 O(单行长度 + chunk)，与文件条数无关。
    """
    encoding = "utf-8"
    buf = b""
    sniffed = False
    while True:
        chunk = fp.read(chunk_size)
        if not chunk:
            break
        if not sniffed:
            encoding = _sniff_encoding(chunk[:4])
            sniffed = True
        buf += chunk
        parts = buf.split(b"\n")
        buf = parts.pop()  # 最后一段可能未完整，留到下一轮
        for part in parts:
            yield _decode_line(part.rstrip(b"\r"), encoding)
    if buf:
        yield _decode_line(buf.rstrip(b"\r"), encoding)


def parse_result_stream(fp: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """流式解析结果文件（与 `parse_result_file` 同协议、同分类规则）。

    为什么需要它：整份文件先 `read()` 再 `split("\\n")` 会让峰值内存达到
    「文件字节 + 解码后字符串 + 行列表」≈ 3 倍文件大小；200 MB 的结果文件
    会直接把进程顶到 600 MB+。改成逐行解析后，内存与条数无关。

    形式判定与 `parse_result_file` **完全一致**（先看首行）：
      1) 首行以 `[` 开头                → JSON 数组形式（必须整读）
      2) 首行是带 items / _meta 的对象  → JSON 文档形式（必须整读）
      3) 其余                           → JSONL（逐行）
    """
    lines = _iter_text_lines(fp)
    first_line = ""
    for candidate in lines:
        if candidate.strip():
            first_line = candidate.strip()
            break
    if not first_line:
        raise FileLevelError("文件没有可用内容")

    stripped = first_line.lstrip("\ufeff").strip()

    # 1) / 2) 整份 JSON：只能整读（这种形式本身就不是为超大文件设计的）
    if stripped.startswith("["):
        try:
            fp.seek(0)
        except (AttributeError, OSError):  # pragma: no cover - 不可 seek 的流
            pass
        else:
            return parse_result_file(fp.read())

    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            if '"items"' in stripped or "'items'" in stripped:
                try:
                    fp.seek(0)
                except (AttributeError, OSError):  # pragma: no cover
                    pass
                else:
                    return parse_result_file(fp.read())
        else:
            if isinstance(parsed, dict) and "items" in parsed:
                try:
                    fp.seek(0)
                except (AttributeError, OSError):  # pragma: no cover
                    pass
                else:
                    return parse_result_file(fp.read())

    return _parse_jsonl_lines(chain([first_line], lines))


def _parse_jsonl_lines(lines: Iterable[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    meta: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue  # 空行跳过
        if line.startswith("```"):
            continue  # 行内围栏残留
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as err:
            errors.append(
                {"line": idx, "reason": CLS_INVALID_JSON, "message": f"第 {idx} 行 JSON 解析失败：{err.msg}"}
            )
            continue

        if isinstance(obj, dict) and "_meta" in obj and len(obj) == 1:
            if not meta:
                meta = obj["_meta"] if isinstance(obj["_meta"], dict) else {}
            continue

        records.append(_as_record(obj, idx))

    return meta, records, errors


def _parse_jsonl_form(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """保留原入口（整份文本 → JSONL 解析），供 `parse_result_file` 与测试使用。"""
    return _parse_jsonl_lines(
        text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    )


def _parse_json_form(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        raise FileLevelError(f"JSON 解析失败：{err.msg}（第 {err.lineno} 行）") from err

    meta: dict[str, Any] = {}
    items: Any = payload

    if isinstance(payload, dict):
        if not isinstance(payload.get("_meta"), dict):
            raise FileLevelError("JSON 对象缺少 _meta")
        if not isinstance(payload.get("items"), list):
            raise FileLevelError("JSON 对象必须包含 items 数组")
        meta = payload["_meta"]
        items = payload["items"]
    elif not isinstance(payload, list):
        raise FileLevelError("JSON 顶层必须是数组，或 {_meta, items} 对象")

    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for idx, obj in enumerate(items, start=1):
        if not isinstance(obj, dict):
            errors.append(
                {"line": idx, "reason": CLS_INVALID_JSON, "message": f"第 {idx} 项不是对象"}
            )
            continue
        records.append(_as_record(obj, idx))

    return meta, records, errors


def _as_record(obj: Any, line: int) -> dict[str, Any]:
    """把一行转成统一记录；结构不对时不抛错，交给后续分类。"""
    if not isinstance(obj, dict):
        return {"line": line, "content_id": None, "content_hash": None,
                "analysis": None, "shape_error": "该行不是 JSON 对象"}
    return {
        "line": line,
        "content_id": obj.get("content_id"),
        "content_hash": obj.get("content_hash"),
        "analysis": obj.get("analysis"),
        "shape_error": None,
    }


# ================================================================ 文件级校验

def validate_meta(meta: dict[str, Any], *, expected_format: str | None = None) -> list[str]:
    """校验 _meta。返回 **warning** 列表；文件级错误直接抛 FileLevelError。"""
    if not meta:
        raise FileLevelError("缺少 _meta 元信息")

    fmt = meta.get("format")
    wanted = expected_format or EXTERNAL_RESULT_FORMAT
    if fmt != wanted:
        raise FileLevelError(f"文件格式不匹配：期望 {wanted}，实际 {fmt}")

    fv = meta.get("format_version")
    if fv != EXTERNAL_ANALYSIS_FORMAT_VERSION:
        raise FileLevelError(
            f"format_version 不匹配：期望 {EXTERNAL_ANALYSIS_FORMAT_VERSION}，实际 {fv}"
        )

    sv = meta.get("analysis_schema_version")
    if sv != ANALYSIS_SCHEMA_VERSION:
        raise FileLevelError(
            f"analysis_schema_version 不匹配：期望 {ANALYSIS_SCHEMA_VERSION}，实际 {sv}"
        )

    sh = meta.get("schema_hash")
    if sh != ANALYSIS_SCHEMA_HASH:
        raise FileLevelError(f"schema_hash 不匹配：期望 {ANALYSIS_SCHEMA_HASH}，实际 {sh}")

    warnings: list[str] = []
    pv = meta.get("prompt_version")
    if pv != ANALYSIS_PROMPT_VERSION:
        # Prompt 文案版本与字段结构是两件事：只提示，不拒收
        warnings.append(
            f"prompt_version 为 {pv}（当前 {ANALYSIS_PROMPT_VERSION}）—— "
            "Prompt 措辞不同不影响导入，只要字段结构一致。"
        )
    return warnings


# ================================================================ Preview

def _classify_record(
    rec: dict[str, Any],
    metas: dict[int, dict[str, Any]],
    duplicate_ids: set[int],
) -> dict[str, Any]:
    cid = rec.get("content_id")

    if rec["shape_error"]:
        return {"classification": CLS_INVALID_JSON, "message": rec["shape_error"]}

    if isinstance(cid, bool) or not isinstance(cid, int):
        return {"classification": CLS_INVALID_JSON, "message": "content_id 缺失或不是整数"}

    if cid in duplicate_ids:
        return {
            "classification": CLS_DUPLICATE,
            "message": "同一文件里同一个 content_id 出现多次，全部不导入",
        }

    analysis = rec.get("analysis")
    try:
        validate_analysis(analysis)
    except SpecError as err:
        return {
            "classification": CLS_SCHEMA_ERROR,
            "field_path": err.path,
            "message": err.message,
        }

    row = metas.get(cid)
    if row is None:
        return {"classification": CLS_NOT_FOUND, "message": f"内容 {cid} 不存在"}

    if not content_hash_matches(row["raw_text"], rec.get("content_hash") or ""):
        return {
            "classification": CLS_HASH_MISMATCH,
            "message": "正文与导出时不一致（content_hash 不匹配）",
        }

    if row["analysis_state"] in BUSY_STATES:
        return {
            "classification": CLS_BUSY,
            "message": f"正在分析中（{row['analysis_state']}），即使勾选覆盖也不导入",
        }

    if row["has_analysis"] or row["analysis_state"] == "analyzed":
        return {
            "classification": CLS_ALREADY,
            "message": "已有分析结果，勾选「覆盖已有分析」才会替换",
        }

    return {"classification": CLS_VALID, "message": ""}


def preview_result_file(
    data: bytes | None = None,
    *,
    overwrite: bool = False,
    stream: Any = None,
) -> dict[str, Any]:
    """解析 + 校验 + 分类。**0 DB 写入 / 0 AI**。

    `stream` 给大文件用（逐行解析，整份文件不进内存）；
    不传则走原来的 `data: bytes` 路径，行为完全不变。
    """
    if stream is not None:
        meta, records, errors = parse_result_stream(stream)
    else:
        meta, records, errors = parse_result_file(data)
    warnings = validate_meta(meta)

    ids = [r["content_id"] for r in records if isinstance(r.get("content_id"), int)
           and not isinstance(r.get("content_id"), bool)]

    # 一次性批量查（分片），绝无 N+1
    with get_conn(settings.db_path) as conn:
        metas = repo.get_contents_batch_meta(conn, ids)

    counts: dict[int, int] = {}
    for cid in ids:
        counts[cid] = counts.get(cid, 0) + 1
    duplicate_ids = {cid for cid, n in counts.items() if n > 1}

    classified: list[dict[str, Any]] = []
    valid_records: list[dict[str, Any]] = []
    for rec in records:
        result = _classify_record(rec, metas, duplicate_ids)
        classification = result["classification"]
        if classification == CLS_ALREADY and overwrite:
            classification = CLS_VALID
            result["message"] = "将覆盖已有分析结果"
        if classification == CLS_VALID:
            valid_records.append(rec)
        classified.append(
            {
                "line": rec["line"],
                "content_id": rec.get("content_id"),
                "classification": classification,
                "field_path": result.get("field_path"),
                "message": result.get("message", ""),
            }
        )

    summary = {key: 0 for key in (
        CLS_VALID, CLS_INVALID_JSON, CLS_SCHEMA_ERROR, CLS_NOT_FOUND,
        CLS_HASH_MISMATCH, CLS_ALREADY, CLS_BUSY, CLS_DUPLICATE,
    )}
    for item in classified:
        summary[item["classification"]] = summary.get(item["classification"], 0) + 1
    summary[CLS_INVALID_JSON] += len(errors)

    for err in errors:
        classified.append(
            {
                "line": err["line"],
                "content_id": None,
                "classification": CLS_INVALID_JSON,
                "field_path": None,
                "message": err["message"],
            }
        )
    classified.sort(key=lambda x: x["line"])

    return {
        "meta": meta,
        "warnings": warnings,
        "counts": summary,
        "total": len(records) + len(errors),
        "valid_count": summary[CLS_VALID],
        "items": classified,
        # 给 Confirm 用的有效行（含 analysis 本体），API 层不会回传给前端
        "_valid_records": valid_records,
        # 无状态一致性指纹：前端 Preview 后原样带回
        "confirm_token": confirm_token(valid_records, overwrite),
        "overwrite": overwrite,
    }


# ================================================================ 队列 → 外部分析交接

def queued_handoff_candidates(
    *,
    source: str | None = None,
    author_id: int | None = None,
    author_ids: list[int] | tuple[int, ...] | None = None,
    include_no_author: bool = False,
    limit: int = EXPORT_DEFAULT_LIMIT,
) -> list[int]:
    """当前筛选下处于 queued 的内容 id。

    limit 语义与导出一致：0（默认）= 不限量，正整数 = 显式截断。
    """
    where = ["analysis_state = 'queued'"]
    params: list[Any] = []
    if source:
        where.append("source = ?")
        params.append(source)

    author_clause, author_params = _author_filter_sql(
        author_id=author_id, author_ids=author_ids, include_no_author=include_no_author
    )
    where.extend(author_clause)
    params.extend(author_params)

    limit_clause, limit_params = _limit_sql(limit)
    params.extend(limit_params)

    with get_conn(settings.db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM contents "
            f" WHERE {' AND '.join(where)} ORDER BY id ASC" + limit_clause,
            params,
        ).fetchall()
    return [int(r["id"]) for r in rows]


def handoff_to_external(content_ids: list[int]) -> dict[str, Any]:
    """把**仅处于 queued** 的内容交接给外部分析（Stage 12.1）。

    为什么需要它：用户之所以用外部分析，常常正是因为内部分析太慢，
    但内容一保存就被内部队列抢走（queued/processing），外部分析反而拿不到
    最需要它接管的内容。

    安全边界：

    - 只有 `queued` 可转；`processing` 明确拒绝 —— **不强杀已发出的模型请求**
    - 不做 asyncio.Queue 的物理删除（无法安全删除单个 item，且会破坏 task_done 计数）
    - 交接只是状态转移，旧 queue item 由 worker 的 pre-flight 守卫负责跳过
    - 全程单事务 + 单条 CAS（`WHERE analysis_state='queued'`），
      与 worker 的认领 CAS 在同一行上只能有一个赢家

    返回分类：transferred / not_queued / processing / not_found
    """
    if not content_ids:
        raise ServiceError("content_ids 不能为空")
    ids = list(dict.fromkeys(int(i) for i in content_ids))

    result: dict[str, Any] = {
        HO_TRANSFERRED: [], HO_NOT_QUEUED: [], HO_PROCESSING: [], HO_NOT_FOUND: [],
    }

    with get_conn(settings.db_path) as conn:
        metas = repo.get_contents_batch_meta(conn, ids)
        for cid in ids:
            row = metas.get(cid)
            if row is None:
                result[HO_NOT_FOUND].append(cid)
                continue

            state = row["analysis_state"]
            if state == "processing":
                result[HO_PROCESSING].append(cid)
                continue
            if state != "queued":
                result[HO_NOT_QUEUED].append(cid)
                continue

            # CAS：与 worker 的认领竞争，只有一个能赢
            if repo.cas_queued_to_unprocessed(conn, cid):
                result[HO_TRANSFERRED].append(cid)
                continue

            # CAS 失败 → 必须重新读取真实状态再归类，绝不无条件改写
            with get_conn(settings.db_path) as probe:
                current = repo.get_contents_batch_meta(probe, [cid]).get(cid)
            now_state = current["analysis_state"] if current else None
            if now_state == "processing":
                result[HO_PROCESSING].append(cid)
            elif now_state is None:
                result[HO_NOT_FOUND].append(cid)
            else:
                result[HO_NOT_QUEUED].append(cid)

    logger.info(
        "队列交接：transferred=%s processing=%s not_queued=%s not_found=%s",
        len(result[HO_TRANSFERRED]), len(result[HO_PROCESSING]),
        len(result[HO_NOT_QUEUED]), len(result[HO_NOT_FOUND]),
    )
    result["transferred_count"] = len(result[HO_TRANSFERRED])
    return result


# ================================================================ Confirm

def confirm_token(valid_records: list[dict[str, Any]], overwrite: bool) -> str:
    """Preview 结果的一致性指纹（无状态、不落库）。

    内容 = 排序后的 (content_id, content_hash) + overwrite 开关。
    Confirm 会用**当前**的有效集合重新算一次并比对：
    只要有任何一条从 valid 变成不合法（状态变化 / 正文变化 / 已有分析），
    指纹必然不同 → 整批拒绝，要求重新预检。

    这不是安全边界，而是「所见即所写」的一致性检查；
    真正的安全仍来自事务内的逐条复验。
    """
    parts = sorted(
        f"{r.get('content_id')}:{r.get('content_hash') or ''}" for r in valid_records
    )
    payload = ("ow=1" if overwrite else "ow=0") + "|" + "|".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _external_model_name(meta: dict[str, Any]) -> str:
    raw = meta.get("model")
    if not isinstance(raw, str) or not raw.strip():
        return EXTERNAL_MODEL_UNKNOWN
    name = raw.strip()[:EXTERNAL_MODEL_MAX_LEN]
    return f"{EXTERNAL_MODEL_PREFIX}{name}"


def confirm_import(
    data: bytes | None = None,
    *,
    overwrite: bool = False,
    token: str | None = None,
    stream: Any = None,
) -> dict[str, Any]:
    """确认导入：整批**一个事务**，全有或全无。

    绝不相信 Preview —— 在事务内重新读库、重新校验哈希与状态，
    任何一条从 valid 变成不合法 → 整体 rollback。

    `stream` 给大文件用（同一份流会被读取两次：preview 与 confirm，
    调用方负责在开始前 seek(0)）。
    """
    if stream is not None:
        try:
            stream.seek(0)
        except (AttributeError, OSError):  # pragma: no cover
            pass
    preview = preview_result_file(data, overwrite=overwrite, stream=stream)
    valid = preview["_valid_records"]

    # 正在分析中的内容：即使 overwrite 也禁止（不自动取消内部任务）
    busy = preview["counts"].get(CLS_BUSY, 0)
    if busy:
        raise ConflictError(f"有 {busy} 条内容正在分析中，请等待完成后再导入")

    if not valid:
        return {"imported": 0, "skipped": preview["total"], "reason": "没有可导入的记录"}

    # Preview 与 Confirm 的一致性检查：只要有一次 Preview 后状态变化，指纹就对不上
    if token and token != preview["confirm_token"]:
        raise ConflictError("内容状态已变化，请重新预检")

    model = _external_model_name(preview["meta"])
    ids = [r["content_id"] for r in valid]

    with get_conn(settings.db_path) as conn:
        # 1) 事务内重新批量读
        metas = repo.get_contents_batch_meta(conn, ids)

        # 2) 逐条重新校验：存在性 / hash / 状态 / 已有分析
        stale: list[str] = []
        for rec in valid:
            cid = rec["content_id"]
            row = metas.get(cid)
            if row is None:
                stale.append(f"{cid}: 内容已不存在")
                continue
            if not content_hash_matches(row["raw_text"], rec.get("content_hash") or ""):
                stale.append(f"{cid}: 正文已变化")
                continue
            if row["analysis_state"] in BUSY_STATES:
                stale.append(f"{cid}: 正在分析中")
                continue
            if (row["has_analysis"] or row["analysis_state"] == "analyzed") and not overwrite:
                stale.append(f"{cid}: 已有分析结果")

        if stale:
            raise ConflictError(
                "内容状态已变化，请重新预检：" + "；".join(stale[:5])
            )

        # 3) 再次 strict validate（文件内容不会变，但保持对称，防止调用方直传）
        for rec in valid:
            try:
                validate_analysis(rec["analysis"])
            except SpecError as err:
                raise ConflictError(
                    f"内容 {rec['content_id']} 的分析结果不再符合 schema（{err.path}: {err.message}）"
                )

        # 4) 全部仍合法 → 落库（同一个事务）
        #
        #    标签统计延迟到整批结束：replace_content_tags 每条都会跑一次
        #    _recount（全表 UPDATE + DELETE），单条成本随 content_tags 线性增长，
        #    整批就是 O(n²)：实测 10,000 条 5.8 s → 0.95 s。
        #    最终状态与「每条都统计」完全一致（_recount 是全量重算，不是增量）。
        with repo.deferred_tag_recount():
            for rec in valid:
                cid = rec["content_id"]
                persist_analysis_result(
                    conn,
                    cid,
                    rec["analysis"],
                    raw=rec["analysis"],
                    model=model,
                    raw_text=metas[cid]["raw_text"],
                )
                conn.execute(
                    "UPDATE analyses SET length_chars = ? WHERE content_id = ?",
                    (compute_length_chars(metas[cid]["raw_text"]), int(cid)),
                )
            repo.flush_tag_recount(conn)

    # 5) 提交成功后通知一次（与内部分析完成同一入口）
    #    一次通知就会根据 DB 全量 staleness 判断，不需要按条调用。
    from app import jobs as jobs_module

    jobs_module.notify_analysis_completed()

    logger.info(
        "外部分析导入完成：%s 条，model=%s，overwrite=%s", len(valid), model, overwrite
    )
    return {
        "imported": len(valid),
        "skipped": preview["total"] - len(valid),
        "model": model,
        "overwrite": overwrite,
        "counts": preview["counts"],
        "warnings": preview["warnings"],
    }


