"""数据访问层：集中所有 SQL，上层不写裸 SQL。

所有函数接收一个已打开的 sqlite3.Connection。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

# 显示名称的 metadata 承载键由解析器（格式契约的所有者）定义，这里只做读取方。
from app.config import ANALYSIS_PROMPT_VERSION, ANALYSIS_SCHEMA_VERSION
# schema 指纹由契约模块计算（它才是字段树的所有者），repository 只负责落库。
from app.services.analysis_spec import ANALYSIS_SCHEMA_HASH, scalar_field_paths
from app.services.importer import AUTHOR_DISPLAY_NAME_KEY

# ---------------------------------------------------------------- 工具

def now_iso() -> str:
    """本地时区 ISO8601 字符串（精确到秒）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_to_content(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["raw_text"] or ""
    excerpt = raw.strip().replace("\n", " ")
    if len(excerpt) > 160:
        excerpt = excerpt[:160] + "…"
    tag_names = row["tag_names"] if "tag_names" in row.keys() else None
    tags = tag_names.split("||") if tag_names else []
    return {
        "id": row["id"],
        "source": row["source"],
        "provenance": row["provenance"] if "provenance" in row.keys() else None,
        "summary": row["summary"],
        "excerpt": excerpt,
        "status": row["status"],
        "analysis_state": (
            row["analysis_state"] if "analysis_state" in row.keys() else None
        ),
        "import_batch_id": (
            row["import_batch_id"] if "import_batch_id" in row.keys() else None
        ),
        # 来源元数据（治理维度：作者 / 原始 id / 链接 / 发布时间 / 类型）
        "external_id": row["external_id"] if "external_id" in row.keys() else None,
        "source_url": row["source_url"] if "source_url" in row.keys() else None,
        "author_username": (
            row["author_username"] if "author_username" in row.keys() else None
        ),
        "author_id": row["author_id"] if "author_id" in row.keys() else None,
        "posted_at": row["posted_at"] if "posted_at" in row.keys() else None,
        "post_type": row["post_type"] if "post_type" in row.keys() else None,
        "style_eligible": (
            bool(row["style_eligible"])
            if "style_eligible" in row.keys() and row["style_eligible"] is not None
            else False
        ),
        "analysis_error": row["analysis_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "tags": tags,
        "topic": row["topic"] if "topic" in row.keys() else None,
        "content_type": row["content_type"] if "content_type" in row.keys() else None,
        "length_chars": row["length_chars"] if "length_chars" in row.keys() else None,
    }


_CONTENT_SELECT = """
SELECT c.*,
       a.topic,
       a.content_type,
       a.length_chars,
       (SELECT GROUP_CONCAT(t.name, '||')
          FROM content_tags ct JOIN tags t ON t.id = ct.tag_id
         WHERE ct.content_id = c.id AND ct.kind = 'tag') AS tag_names
  FROM contents c
  LEFT JOIN analyses a ON a.content_id = c.id
"""


# ---------------------------------------------------------------- contents

def create_content(
    conn: sqlite3.Connection,
    source: str,
    raw_text: str,
    import_batch_id: str | None = None,
    *,
    style_eligible: bool = False,
) -> int:
    """新建内容。

    默认 analysis_state='unprocessed' —— 入库本身不触发任何 AI 调用。

    style_eligible 与 source 是两个独立维度：
    只有「我的内容」且用户允许时才为 True。
    benchmark 永远传 False（有效条件也永远要求 source='mine'，双保险）。
    """
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO contents(source, raw_text, status, analysis_state, "
        "                     import_batch_id, style_eligible, created_at, updated_at) "
        "VALUES(?, ?, 'pending', 'unprocessed', ?, ?, ?, ?)",
        (
            source,
            raw_text,
            import_batch_id,
            1 if (style_eligible and source == "mine") else 0,
            ts,
            ts,
        ),
    )
    return int(cur.lastrowid)


def get_content(conn: sqlite3.Connection, content_id: int) -> sqlite3.Row | None:
    return conn.execute(
        _CONTENT_SELECT + " WHERE c.id = ?", (content_id,)
    ).fetchone()


def get_content_raw(conn: sqlite3.Connection, content_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM contents WHERE id = ?", (content_id,)
    ).fetchone()


# status（兼容列）↔ analysis_state（流程真源）的完整映射。
#
# 任务 04-A（D8）：过去这两个字段是**双真源** —— set_status 会同时改两列，
# set_analysis_state 只改一列，于是同一个语义有两套写法，任何新代码只要
# 用了后者就会让两列漂移（画像读的是 analysis_state，列表页读的是 status）。
#
# 现在收敛为唯一入口 set_analysis_status()：
#   - 传入 analysis_state（真源）；
#   - status 由映射表**推导**，写库时一次写完，不存在中间态。
ANALYSIS_STATE_TO_STATUS: dict[str, str] = {
    "unprocessed": "pending",
    "queued": "pending",
    "processing": "analyzing",
    "analyzed": "analyzed",
    "failed": "failed",
    "cancelled": "pending",
}

STATUS_TO_ANALYSIS_STATE: dict[str, str] = {
    "pending": "unprocessed",
    "analyzing": "processing",
    "analyzed": "analyzed",
    "failed": "failed",
}

ANALYSIS_STATES = tuple(ANALYSIS_STATE_TO_STATUS)


def set_analysis_status(
    conn: sqlite3.Connection,
    content_id: int,
    analysis_state: str,
    error: str | None = None,
) -> None:
    """**唯一**的状态转换入口 —— 同时维护两列，不允许任何调用方分开写。

    analysis_state 是流程真源（worker 的守卫、画像样本口径都读它）；
    status 是保留给旧列表页与旧逻辑的兼容列，由真源推导。
    """
    if analysis_state not in ANALYSIS_STATE_TO_STATUS:
        raise ValueError(f"未知的 analysis_state：{analysis_state}")
    conn.execute(
        "UPDATE contents SET status = ?, analysis_state = ?, "
        "                     analysis_error = ?, updated_at = ? "
        " WHERE id = ?",
        (
            ANALYSIS_STATE_TO_STATUS[analysis_state],
            analysis_state,
            error,
            now_iso(),
            int(content_id),
        ),
    )


def set_status(
    conn: sqlite3.Connection,
    content_id: int,
    status: str,
    error: str | None = None,
) -> None:
    """兼容包装：按 status 推导 analysis_state 后走唯一入口。

    保留这个函数是为了不改动既有调用点，但它**不再**是只写一列的旁路。
    调用方若需要表达「排队/取消」这类 status 无法区分的状态，
    必须直接用 set_analysis_status。
    """
    mapped = STATUS_TO_ANALYSIS_STATE.get(status)
    if mapped is None:
        raise ValueError(
            f"未知的 status：{status}（请改用 set_analysis_status 明确状态）"
        )
    set_analysis_status(conn, content_id, mapped, error)


def set_analysis_state(
    conn: sqlite3.Connection,
    content_id: int,
    analysis_state: str,
    error: str | None = None,
) -> None:
    """兼容包装：走唯一入口（因此 status 会被一并更新，不再漂移）。"""
    set_analysis_status(conn, content_id, analysis_state, error)


def claim_for_analysis(conn: sqlite3.Connection, content_id: int) -> bool:
    """worker 的**原子认领**：只有仍该被分析的条目才能推进到 processing。

    背景（Stage 12.1）：内容一旦入队，asyncio.Queue 里那条 item 就无法安全删除。
    因此「用户把它转为外部分析」只能靠**状态交接**：
        queued → unprocessed
    而 worker 取到旧 item 时，必须在调用模型**之前**重新确认当前状态。

    这里用一条带条件的 UPDATE 把「读状态」和「写 processing」合成单条 CAS：

        WHERE analysis_state IN ('queued', 'processing')

    允许 `processing` 是刻意的 —— 进程重启后，`jobs` 会把上次被中断的
    `processing` 内容重新入队（此时已经没有在飞的请求），它们必须能继续。
    而 handoff 之后的 `unprocessed`、以及 analyzed / failed / cancelled，
    都会在这里返回 False，被 worker 直接跳过、不调用任何模型。

    与 handoff 的 CAS（只认 queued）在同一行上竞争时，只有一个能赢。
    """
    cur = conn.execute(
        "UPDATE contents SET status = 'analyzing', analysis_state = 'processing',"
        "                     analysis_error = NULL, updated_at = ?"
        " WHERE id = ?"
        "   AND COALESCE(analysis_state, 'unprocessed') IN ('queued', 'processing')",
        (now_iso(), int(content_id)),
    )
    return cur.rowcount == 1


def cas_queued_to_unprocessed(conn: sqlite3.Connection, content_id: int) -> bool:
    """把**仅处于 queued** 的内容交接为 unprocessed（Stage 12.1）。

    单条 CAS：`WHERE id=? AND analysis_state='queued'`。
    返回 False 表示竞争失败 —— 调用方必须重新读取当前状态再归类，
    绝不能无条件改写。

    同步 status='pending' 与 `jobs._revert_to_unprocessed` 完全一致：
    这样「转为外部分析」在状态上与「被取消后回到未分析」是同一种语义。
    """
    cur = conn.execute(
        "UPDATE contents SET analysis_state = 'unprocessed', status = 'pending',"
        "                     analysis_error = NULL, updated_at = ?"
        " WHERE id = ? AND analysis_state = 'queued'",
        (now_iso(), int(content_id)),
    )
    return cur.rowcount == 1


def count_contents_by_analysis_state(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    author_id: int | None = None,
    author_ids: list[int] | None = None,
    include_no_author: bool = False,
) -> dict[str, int]:
    """按 analysis_state 分组计数（外部分析页判断「待转出 / 正在分析」用）。

    一次 GROUP BY，避免前端为每个状态各发一次请求。

    author_ids 与 include_no_author 是 V2 任务 02 新增的**可选**参数：
    不传时行为与之前完全一致（单作者或全库）。
    """
    where: list[str] = []
    params: list[Any] = []
    if source:
        where.append("source = ?")
        params.append(source)
    ids = [int(a) for a in (author_ids or []) if a is not None]
    if author_id is not None:
        where.append("author_id = ?")
        params.append(int(author_id))
    elif ids:
        placeholders = ",".join("?" for _ in ids)
        if include_no_author:
            where.append(f"(author_id IN ({placeholders}) OR author_id IS NULL)")
        else:
            where.append(f"author_id IN ({placeholders})")
        params.extend(ids)
    sql = (
        "SELECT COALESCE(analysis_state, 'unprocessed') AS st, COUNT(*) AS n "
        "  FROM contents "
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " GROUP BY st"
    )
    rows = conn.execute(sql, params).fetchall()
    return {str(r["st"]): int(r["n"]) for r in rows}


def count_content_ids_for_run(conn: sqlite3.Connection, import_batch_id: str) -> dict:
    """统计某次导入批次各分析状态的数量。"""
    rows = conn.execute(
        "SELECT analysis_state, COUNT(*) n FROM contents "
        " WHERE import_batch_id = ? GROUP BY analysis_state",
        (import_batch_id,),
    ).fetchall()
    return {r["analysis_state"] or "unprocessed": int(r["n"]) for r in rows}


def list_content_ids_for_run(
    conn: sqlite3.Connection, import_batch_id: str, states: tuple[str, ...]
) -> list[int]:
    """取出某批次中处于指定分析状态的内容 id。"""
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"SELECT id FROM contents WHERE import_batch_id = ? "
        f"  AND COALESCE(analysis_state, 'unprocessed') IN ({placeholders}) "
        f" ORDER BY id ASC",
        (import_batch_id, *states),
    ).fetchall()
    return [int(r["id"]) for r in rows]


def list_content_ids_by_analysis_state(
    conn: sqlite3.Connection, states: tuple[str, ...]
) -> list[int]:
    """按分析状态全局取内容 id（服务重启恢复用）。

    刻意只按 analysis_state 判断：unprocessed 的内容（例如刚导入的）
    绝不会被恢复，因为导入本身不代表用户授权分析。
    """
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        f"SELECT id FROM contents "
        f" WHERE COALESCE(analysis_state, 'unprocessed') IN ({placeholders}) "
        f" ORDER BY id ASC",
        states,
    ).fetchall()
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------- 表现数据（Performance V1）

_PERFORMANCE_COUNT_COLUMNS = (
    "view_count", "favorite_count", "reply_count",
    "retweet_count", "quote_count", "bookmark_count",
)


def save_content_performance(
    conn: sqlite3.Connection,
    content_id: int,
    performance: dict[str, Any] | None,
    *,
    captured_at: str,
) -> bool:
    """保存一条内容的表现数据（1 content → 最多 1 record）。

    performance 为空 / 非 dict → 不写任何东西（返回 False），
    这样 TXT / MD / CSV / 手工输入的内容不会留下空壳记录。

    **重复导入的更新规则（任务 04-A）**：
    内容本身仍然 skip（不重复导入、不触发 AI），但表现数据会按采集时间更新：

      1. 新采集时间**早于**已有 captured_at → 整行不动（旧快照不得覆盖新快照）；
      2. 否则逐列合并：新值为 NULL → **保留旧值**（不覆盖已知计数）；
         新值非 NULL（含 0）→ 覆盖。0 表示「已知为 0」，有权覆盖旧的非零值。
      3. 不取最大值 —— 计数可能下降，取最大值会伪造一个从未发生过的峰值。

    返回是否真的写入/更新了数据。
    """
    if not isinstance(performance, dict) or not performance:
        return False

    existing = conn.execute(
        "SELECT * FROM content_performance WHERE content_id = ?", (content_id,)
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO content_performance("
            "    content_id, view_count, favorite_count, reply_count,"
            "    retweet_count, quote_count, bookmark_count,"
            "    is_pinned, media_type, captured_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(content_id),
                *(performance.get(col) for col in _PERFORMANCE_COUNT_COLUMNS),
                1 if performance.get("is_pinned") else 0,
                performance.get("media_type"),
                captured_at,
                captured_at,
            ),
        )
        return True

    # 旧快照不得覆盖新快照（字符串比较即可：两者都是同格式 ISO / 同一批次生成）
    old_captured = existing["captured_at"] or ""
    if old_captured and captured_at < old_captured:
        return False

    merged: dict[str, Any] = {}
    for col in _PERFORMANCE_COUNT_COLUMNS:
        new_value = performance.get(col)
        merged[col] = existing[col] if new_value is None else new_value

    new_pinned = performance.get("is_pinned")
    merged["is_pinned"] = (
        existing["is_pinned"] if new_pinned is None else (1 if new_pinned else 0)
    )
    new_media = performance.get("media_type")
    merged["media_type"] = existing["media_type"] if new_media is None else new_media

    if all(merged[col] == existing[col] for col in _PERFORMANCE_COUNT_COLUMNS) and (
        merged["is_pinned"] == existing["is_pinned"]
        and merged["media_type"] == existing["media_type"]
    ):
        # 数值没有变化：仍然推进采集时间（这就是「最近一次确认」的语义），
        # 但不制造一次无意义的整行写入。
        conn.execute(
            "UPDATE content_performance SET captured_at = ?, updated_at = ? "
            " WHERE content_id = ?",
            (captured_at, captured_at, int(content_id)),
        )
        return True

    conn.execute(
        "UPDATE content_performance SET "
        "  view_count = ?, favorite_count = ?, reply_count = ?,"
        "  retweet_count = ?, quote_count = ?, bookmark_count = ?,"
        "  is_pinned = ?, media_type = ?, captured_at = ?, updated_at = ? "
        " WHERE content_id = ?",
        (
            *(merged[col] for col in _PERFORMANCE_COUNT_COLUMNS),
            merged["is_pinned"],
            merged["media_type"],
            captured_at,
            captured_at,
            int(content_id),
        ),
    )
    return True


def get_content_performance(
    conn: sqlite3.Connection, content_id: int
) -> dict[str, Any] | None:
    """读取单条内容的表现数据；没有则 None。"""
    row = conn.execute(
        "SELECT * FROM content_performance WHERE content_id = ?", (content_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        **{col: row[col] for col in _PERFORMANCE_COUNT_COLUMNS},
        "is_pinned": bool(row["is_pinned"]),
        "media_type": row["media_type"],
        "captured_at": row["captured_at"],
    }


def delete_contents(conn: sqlite3.Connection, ids: list[int]) -> int:
    """按 id 批量删除内容（物理删除）。

    关联的 analyses / content_tags / content_performance 由外键
    ON DELETE CASCADE 清理。
    """
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(f"DELETE FROM contents WHERE id IN ({placeholders})", ids)
    return int(cur.rowcount or 0)


def delete_contents_by_import_batch(
    conn: sqlite3.Connection, import_batch_id: str
) -> int:
    """删除整批导入内容（物理删除）。关联数据由外键级联清理。"""
    cur = conn.execute(
        "DELETE FROM contents WHERE import_batch_id = ?", (import_batch_id,)
    )
    return int(cur.rowcount or 0)


# ---------------------------------------------------------------- import_batches

def create_import_batch(
    conn: sqlite3.Connection,
    *,
    import_batch_id: str,
    filename: str | None,
    source: str,
    item_count: int,
    input_count: int | None = None,
    inserted_count: int | None = None,
    duplicate_count: int = 0,
    skipped_count: int = 0,
    author_username: str | None = None,
) -> None:
    """记录一次导入操作及其结果统计。

    item_count 保留为"本次实际入库条数"（= inserted），
    并单独记录 input / duplicate / skipped，让用户知道差异从哪来。
    """
    conn.execute(
        "INSERT INTO import_batches(import_batch_id, filename, source, item_count, "
        "                           input_count, inserted_count, duplicate_count, "
        "                           skipped_count, author_username, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(import_batch_id) DO UPDATE SET "
        "  item_count = excluded.item_count, "
        "  inserted_count = excluded.inserted_count, "
        "  duplicate_count = excluded.duplicate_count, "
        "  skipped_count = excluded.skipped_count",
        (
            import_batch_id, filename, source, item_count,
            input_count if input_count is not None else item_count,
            inserted_count if inserted_count is not None else item_count,
            duplicate_count, skipped_count, author_username, now_iso(),
        ),
    )


def get_contents_batch_meta(
    conn: sqlite3.Connection, content_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """一次性取回若干 content 的元信息（Stage 12 preview / confirm 用）。

    返回 {content_id: {id, raw_text, analysis_state, has_analysis, source, author_id}}。
    找不到的 id **不会出现**在返回值里 —— 调用方据此判 content_not_found。

    沿用 find_existing_external_ids 的 400 分片范式，避免 SQLite 变量上限，
    并且**绝不 N+1**：无论多少条，查询次数都是 ceil(n/400)。
    """
    out: dict[int, dict[str, Any]] = {}
    unique = list(dict.fromkeys(int(i) for i in content_ids))
    chunk = 400
    for i in range(0, len(unique), chunk):
        part = unique[i : i + chunk]
        placeholders = ",".join("?" for _ in part)
        rows = conn.execute(
            "SELECT c.id, c.raw_text, c.analysis_state, c.source, c.author_id, "
            "       c.status, a.id AS analysis_id "
            "  FROM contents c "
            "  LEFT JOIN analyses a ON a.content_id = c.id "
            f" WHERE c.id IN ({placeholders})",
            part,
        ).fetchall()
        for r in rows:
            out[int(r["id"])] = {
                "id": int(r["id"]),
                "raw_text": r["raw_text"] or "",
                "analysis_state": r["analysis_state"] or "unprocessed",
                "has_analysis": r["analysis_id"] is not None,
                "source": r["source"],
                "author_id": r["author_id"],
                "status": r["status"],
            }
    return out


def find_existing_external_ids(
    conn: sqlite3.Connection, external_ids: list[str]
) -> set[str]:
    """在库中查找已存在的 external_id（跨批次去重依据）。

    分批查询以避免 SQLite 变量数量上限。
    """
    return set(
        map_existing_external_ids(conn, external_ids).keys()
    )


def map_existing_external_ids(
    conn: sqlite3.Connection, external_ids: list[str]
) -> dict[str, int]:
    """external_id → 已存在的 content_id。

    重复导入时需要它：内容本身仍然 skip（不重复导入、不触发 AI），
    但**表现数据要按采集时间更新**（design §5），因此必须知道旧行是哪一条。
    """
    found: dict[str, int] = {}
    unique = [eid for eid in dict.fromkeys(external_ids) if eid]
    chunk = 400
    for i in range(0, len(unique), chunk):
        part = unique[i : i + chunk]
        placeholders = ",".join("?" for _ in part)
        rows = conn.execute(
            f"SELECT external_id, id FROM contents "
            f" WHERE external_id IS NOT NULL AND external_id != '' "
            f"   AND external_id IN ({placeholders})",
            part,
        ).fetchall()
        for r in rows:
            found[str(r["external_id"])] = int(r["id"])
    return found


def update_contents_source(
    conn: sqlite3.Connection, ids: list[int], source: str
) -> int:
    """批量修改来源。content_id / external_id / analysis / import_batch_id 均不变。"""
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE contents SET source = ?, updated_at = ? WHERE id IN ({placeholders})",
        [source, now_iso(), *ids],
    )
    return int(cur.rowcount or 0)


def get_import_batch(conn: sqlite3.Connection, import_batch_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM import_batches WHERE import_batch_id = ?", (import_batch_id,)
    ).fetchone()


def list_import_batches(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM import_batches ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def delete_import_batch(conn: sqlite3.Connection, import_batch_id: str) -> None:
    conn.execute(
        "DELETE FROM import_batches WHERE import_batch_id = ?", (import_batch_id,)
    )


# ---------------------------------------------------------------- analysis_runs

def create_analysis_run(
    conn: sqlite3.Connection,
    *,
    kind: str,
    import_batch_id: str | None,
    total: int,
    model: str | None = None,
    batch_job_id: int | None = None,
) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO analysis_runs(kind, import_batch_id, status, total, done, failed, "
        "                          batch_job_id, model, created_at, updated_at) "
        "VALUES(?, ?, 'running', ?, 0, 0, ?, ?, ?, ?)",
        (kind, import_batch_id, total, batch_job_id, model, ts, ts),
    )
    return int(cur.lastrowid)


def update_analysis_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    allowed = {"status", "done", "failed", "batch_job_id", "error"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE analysis_runs SET {assignments} WHERE id = ?",
        [*updates.values(), run_id],
    )


def get_analysis_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM analysis_runs WHERE id = ?", (run_id,)
    ).fetchone()


def find_active_run(
    conn: sqlite3.Connection, import_batch_id: str, kind: str | None = None
) -> sqlite3.Row | None:
    """某批次是否已有进行中的分析运行（running / cancelling）。"""
    sql = (
        "SELECT * FROM analysis_runs WHERE import_batch_id = ? "
        "  AND status IN ('running', 'cancelling')"
    )
    params: list[Any] = [import_batch_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT 1"
    return conn.execute(sql, params).fetchone()


def is_run_cancelled(conn: sqlite3.Connection, run_id: int) -> bool:
    """该运行是否已被请求取消（worker 据此停止取新任务）。"""
    row = conn.execute(
        "SELECT status FROM analysis_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return False
    return row["status"] in ("cancelling", "cancelled")


def list_unfinished_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """服务重启时仍需处理的运行。

    已 cancelled 的运行不会被返回 —— 用户取消过的 run 不得被自动恢复。
    """
    return conn.execute(
        "SELECT * FROM analysis_runs WHERE status IN ('running', 'cancelling') "
        " ORDER BY id ASC"
    ).fetchall()


def delete_analysis_runs_for_batch(
    conn: sqlite3.Connection, import_batch_id: str
) -> int:
    """清理某批次的运行记录（内容删除后，run 已无意义）。"""
    cur = conn.execute(
        "DELETE FROM analysis_runs WHERE import_batch_id = ?", (import_batch_id,)
    )
    return int(cur.rowcount or 0)


def batch_analysis_breakdown(
    conn: sqlite3.Connection, import_batch_id: str
) -> dict[str, int]:
    """该批次内容按 analysis_state 的分布（缺失状态视为 unprocessed）。"""
    rows = conn.execute(
        """
        SELECT COALESCE(analysis_state, 'unprocessed') AS state, COUNT(*) AS n
          FROM contents WHERE import_batch_id = ?
         GROUP BY COALESCE(analysis_state, 'unprocessed')
        """,
        (import_batch_id,),
    ).fetchall()
    return {str(r["state"]): int(r["n"]) for r in rows}


# 未完成分析的状态集合（继续分析时只提交 unprocessed）
UNFINISHED_STATES = ("unprocessed", "queued", "processing", "cancelled", "failed")


def set_summary(conn: sqlite3.Connection, content_id: int, summary: str) -> None:
    conn.execute(
        "UPDATE contents SET summary = ?, updated_at = ? WHERE id = ?",
        (summary, now_iso(), content_id),
    )


def _content_filter_clause(
    *,
    source: str | None = None,
    q: str | None = None,
    tags: list[str] | None = None,
    topic: str | None = None,
    content_type: str | None = None,
    status: str | None = None,
    author_username: str | None = None,
    author_id: int | None = None,
    import_batch_id: str | None = None,
    style_eligible: bool | None = None,
) -> tuple[str, list[Any]]:
    """构造「当前可见内容」的筛选条件。

    列表与 facets **必须共用本函数**，否则两边口径会漂移，
    出现「右侧 0 条、左侧仍有一堆筛选项」的幽灵统计。

    约定表别名：c = contents，a = analyses。
    """
    where: list[str] = []
    params: list[Any] = []

    if source:
        where.append("c.source = ?")
        params.append(source)
    if style_eligible is not None:
        where.append("COALESCE(c.style_eligible, 0) = ?")
        params.append(1 if style_eligible else 0)
    if author_username:
        where.append("c.author_username = ?")
        params.append(normalize_author_username(author_username))
    if author_id is not None:
        # 作者的正式身份依据是 author_id：改 username 后归属与链接都不能失效
        where.append("c.author_id = ?")
        params.append(int(author_id))
    if import_batch_id:
        where.append("c.import_batch_id = ?")
        params.append(import_batch_id)
    if status:
        where.append("c.status = ?")
        params.append(status)
    if topic:
        where.append("a.topic = ?")
        params.append(topic)
    if content_type:
        where.append("a.content_type = ?")
        params.append(content_type)
    if q:
        like = f"%{q}%"
        where.append(
            "(c.raw_text LIKE ? OR c.summary LIKE ? OR EXISTS ("
            " SELECT 1 FROM content_tags ct JOIN tags t ON t.id = ct.tag_id"
            " WHERE ct.content_id = c.id AND ct.kind='tag' AND t.name LIKE ?))"
        )
        params.extend([like, like, like])
    if tags:
        for tag in tags:
            where.append(
                "EXISTS (SELECT 1 FROM content_tags ct JOIN tags t ON t.id = ct.tag_id "
                "WHERE ct.content_id = c.id AND ct.kind='tag' AND t.name = ?)"
            )
            params.append(tag)

    # 始终保留 WHERE 骨架，便于 facets 统一追加 AND 条件
    where_sql = " WHERE " + (" AND ".join(where) if where else "1=1")
    return where_sql, params


def list_contents(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    q: str | None = None,
    tags: list[str] | None = None,
    topic: str | None = None,
    content_type: str | None = None,
    status: str | None = None,
    author_username: str | None = None,
    author_id: int | None = None,
    import_batch_id: str | None = None,
    style_eligible: bool | None = None,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where_sql, params = _content_filter_clause(
        source=source, q=q, tags=tags, topic=topic,
        content_type=content_type, status=status,
        author_username=author_username, author_id=author_id,
        import_batch_id=import_batch_id, style_eligible=style_eligible,
    )

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM contents c "
        "LEFT JOIN analyses a ON a.content_id = c.id" + where_sql,
        params,
    ).fetchone()["n"]

    rows = conn.execute(
        _CONTENT_SELECT + where_sql + " ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return [_row_to_content(r) for r in rows], int(total)


def count_by_source(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source, COUNT(*) AS n FROM contents GROUP BY source"
    ).fetchall()
    out = {"mine": 0, "benchmark": 0}
    for r in rows:
        out[r["source"]] = int(r["n"])
    return out


def count_analyzed_mine(conn: sqlite3.Connection) -> int:
    """已成功分析的「我的内容」总数。

    注意：这是**内容统计**语义，不包含风格资格判断。
    风格学习请用 count_style_eligible_mine()。
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM contents "
        " WHERE source = 'mine' AND status = 'analyzed'"
    ).fetchone()
    return int(row["n"])


# 风格有效样本的统一条件（所有 STYLE / personalization 入口必须共用）
STYLE_ELIGIBLE_CONDITION = (
    "c.source = 'mine' AND c.status = 'analyzed' "
    "AND COALESCE(c.style_eligible, 0) = 1"
)


def count_style_eligible_mine(conn: sqlite3.Connection) -> int:
    """可参与个人风格学习的有效样本数。

    条件：source='mine' AND style_eligible=1 AND status='analyzed'
    —— 即「我的内容」里用户明确允许学习的那部分。
    """
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM contents c WHERE {STYLE_ELIGIBLE_CONDITION}"
    ).fetchone()
    return int(row["n"])


def update_style_eligible(
    conn: sqlite3.Connection, ids: list[int], eligible: bool
) -> int:
    """批量设置风格学习资格。

    **只影响 source='mine' 的内容** —— benchmark 永远不能被开启资格，
    这是防止「绕过 source 后门」的硬约束。
    不触碰 source / provenance / analysis / external_id / import_batch_id。
    """
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE contents SET style_eligible = ?, updated_at = ? "
        f" WHERE id IN ({placeholders}) AND source = 'mine'",
        [1 if eligible else 0, now_iso(), *ids],
    )
    return int(cur.rowcount or 0)


def list_style_eligible_ids(conn: sqlite3.Connection) -> list[int]:
    """当前全部有效风格样本的 content_id。"""
    rows = conn.execute(
        f"SELECT c.id FROM contents c WHERE {STYLE_ELIGIBLE_CONDITION} ORDER BY c.id ASC"
    ).fetchall()
    return [int(r["id"]) for r in rows]


def count_invalidated_samples(
    conn: sqlite3.Connection, sample_ids: list[int]
) -> int:
    """统计给定 id 中**已不再满足**风格有效条件的数量。

    用于判断当前 Profile 是否已经包含被排除的样本：
    只要 > 0，就说明这版档案被污染了，必须重算。
    """
    if not sample_ids:
        return 0
    chunk = 400
    invalid = 0
    for i in range(0, len(sample_ids), chunk):
        part = sample_ids[i : i + chunk]
        placeholders = ",".join("?" for _ in part)
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM contents c "
            f" WHERE c.id IN ({placeholders}) AND NOT ({STYLE_ELIGIBLE_CONDITION})",
            part,
        ).fetchone()
        invalid += int(row["n"])
        # 已被物理删除的样本也算失效
        found = conn.execute(
            f"SELECT COUNT(*) AS n FROM contents WHERE id IN ({placeholders})", part
        ).fetchone()
        invalid += len(part) - int(found["n"])
    return invalid


def mine_sample_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """已分析的「我的内容」样本统计 —— personalization 判定的唯一数据源。

    注意：数据来自 contents 表，而 AI 初稿永远不会写入 contents，
    因此 AI 生成内容天然不会污染这里的统计。
    """
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS sample_count,
            COUNT(DISTINCT NULLIF(TRIM(a.topic), '')) AS topic_count,
            COUNT(DISTINCT NULLIF(TRIM(a.content_type), '')) AS content_type_count,
            SUM(CASE WHEN c.provenance = 'human_original' THEN 1 ELSE 0 END) AS human_original_count,
            SUM(CASE WHEN c.provenance = 'imported_self'  THEN 1 ELSE 0 END) AS imported_self_count,
            SUM(CASE WHEN c.provenance = 'ai_assisted'    THEN 1 ELSE 0 END) AS ai_assisted_count
          FROM contents c
          JOIN analyses a ON a.content_id = c.id
         WHERE c.source = 'mine' AND c.status = 'analyzed'
           AND COALESCE(c.style_eligible, 0) = 1
        """
    ).fetchone()
    return {
        "sample_count": int(row["sample_count"] or 0),
        "topic_count": int(row["topic_count"] or 0),
        "content_type_count": int(row["content_type_count"] or 0),
        "human_original_count": int(row["human_original_count"] or 0),
        "imported_self_count": int(row["imported_self_count"] or 0),
        "ai_assisted_count": int(row["ai_assisted_count"] or 0),
    }


def set_provenance(conn: sqlite3.Connection, content_id: int, provenance: str) -> None:
    conn.execute(
        "UPDATE contents SET provenance = ?, updated_at = ? WHERE id = ?",
        (provenance, now_iso(), content_id),
    )


# 来源元数据允许写入的列（如 Xporter 导出）
_SOURCE_META_KEYS = (
    "external_id",
    "source_url",
    "author_username",
    "posted_at",
    "post_type",
)

# 收容进 contents.source_meta（JSON）的**扩展**来源字段。
#
# 任务 04-A 核查实测：真实 Xporter 导出里 conversation_id 与 language
# 是 100% 存在的，reply_to_username / hashtags / urls / media_urls 也大量出现，
# 而旧导入器把它们全部丢弃 —— 于是「这条是不是回复」「这条是不是中文」
# 「原文引用了哪个链接」在数据层无法回答。
#
# 这些字段目前只用于过滤、标注与查看，不做等值连接，所以放进一个 JSON 列，
# 不为它们建六套表。
SOURCE_META_EXTRA_KEYS = (
    "conversation_id",
    "language",
    "reply_to_username",
    "hashtags",
    "urls",
    "media_urls",
)


# ---------------------------------------------------------------- authors

def normalize_author_username(raw: str | None) -> str | None:
    """X 用户名统一规范：trim → 去开头 @ → 小写。

    @WangChangFu88 / WangChangFu88 / wangchangfu88 会被视作同一作者。
    """
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("@"):
        value = value[1:]
    value = value.strip().lower()
    return value or None


def _backfill_display_name(
    conn: sqlite3.Connection, author_id: int, current: Any, incoming: Any
) -> bool:
    """只在**现有显示名称为空**时补写，绝不覆盖已有的非空名称。

    display_name 是用户在作者页手工维护的字段；导入只是「顺手补空白」，
    不能因为文件里带了个名字就把用户改过的名字冲掉。
    返回是否真的写了。
    """
    wanted = incoming.strip() if isinstance(incoming, str) else ""
    if not wanted:
        return False
    if current is not None and str(current).strip():
        return False
    conn.execute(
        "UPDATE authors SET display_name = ?, updated_at = ? WHERE id = ?",
        (wanted, now_iso(), author_id),
    )
    return True


def resolve_author(
    conn: sqlite3.Connection,
    raw_username: str | None,
    *,
    display_name: str | None = None,
) -> int | None:
    """按 normalize 后的用户名查找作者，不存在则自动创建。返回 author_id。

    display_name 只在「新建」或「已有名称是空」时生效，已有非空名称一律保留。
    """
    username = normalize_author_username(raw_username)
    if not username:
        return None

    row = conn.execute(
        "SELECT id, display_name FROM authors WHERE platform = 'x' AND username = ?",
        (username,),
    ).fetchone()
    if row:
        author_id = int(row["id"])
        _backfill_display_name(conn, author_id, row["display_name"], display_name)
        return author_id

    ts = now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO authors(platform, username, display_name, notes, "
            "                    created_at, updated_at) "
            "VALUES('x', ?, ?, NULL, ?, ?)",
            (username, display_name, ts, ts),
        )
        return int(cur.lastrowid)
    except sqlite3.IntegrityError:
        # 并发插入撞唯一约束：复用已存在的那条
        row = conn.execute(
            "SELECT id, display_name FROM authors WHERE platform = 'x' AND username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        author_id = int(row["id"])
        _backfill_display_name(conn, author_id, row["display_name"], display_name)
        return author_id


def create_author(
    conn: sqlite3.Connection,
    *,
    username: str,
    display_name: str | None = None,
    notes: str | None = None,
) -> int:
    """手动新增作者（可以没有任何帖子）。"""
    normalized = normalize_author_username(username)
    if not normalized:
        raise ValueError("用户名不能为空")

    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO authors(platform, username, display_name, notes, "
        "                    created_at, updated_at) "
        "VALUES('x', ?, ?, ?, ?, ?)",
        (normalized, display_name or None, notes or None, ts, ts),
    )
    return int(cur.lastrowid)


def author_stats(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    """作者的帖子数与已分析数（实时聚合，不做同步字段）。"""
    rows = conn.execute(
        """
        SELECT author_id,
               COUNT(*) AS content_count,
               SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed_count
          FROM contents
         WHERE author_id IS NOT NULL
         GROUP BY author_id
        """
    ).fetchall()
    return {
        int(r["author_id"]): {
            "content_count": int(r["content_count"] or 0),
            "analyzed_count": int(r["analyzed_count"] or 0),
        }
        for r in rows
    }


def list_authors(
    conn: sqlite3.Connection, *, q: str | None = None,
    with_content: bool = False,
) -> list[dict[str, Any]]:
    """作者列表（含实时计数）。支持按 username / display_name 搜索。

    搜索词的 username 部分走与创建/更新一致的 normalization
    （trim → 去开头 @ → lower），因此 @WangChangFu88 能搜到 wangchangfu88。
    display_name 只做 trim + 大小写不敏感匹配，行为保持原样。

    with_content=True 时**只返回至少有一条内容的作者**。
    这是「内容库 → 作者模式」的口径：那一页的目的是浏览已有内容，
    不能让大量 0 内容的空作者占据浏览页。
    「作者管理页」仍然传 False，保留 0 内容作者的管理能力。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if q:
        raw = q.strip()
        # username 走与创建/更新一致的 normalization：trim → 去开头 @ → lower。
        # 所以 @WangChangFu88 / WangChangFu88 / wangchangfu88 都能搜到同一作者。
        # 只影响「搜索词」，绝不改写库里的正式 username。
        normalized = normalize_author_username(raw)
        username_q = normalized if normalized else raw.lower()
        clauses.append("(a.username LIKE ? OR IFNULL(a.display_name, '') LIKE ?)")
        # display_name 保持原样匹配（只 trim + 大小写不敏感），不做去 @ 处理
        params.extend([f"%{username_q}%", f"%{raw.lower()}%"])
    if with_content:
        clauses.append("EXISTS (SELECT 1 FROM contents c WHERE c.author_id = a.id)")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT a.* FROM authors a" + where +
        " ORDER BY (SELECT COUNT(*) FROM contents c WHERE c.author_id = a.id) DESC,"
        "          a.username ASC",
        params,
    ).fetchall()

    stats = author_stats(conn)
    out = []
    for r in rows:
        s = stats.get(int(r["id"]), {"content_count": 0, "analyzed_count": 0})
        out.append(
            {
                "id": int(r["id"]),
                "platform": r["platform"],
                "username": r["username"],
                "display_name": r["display_name"],
                "notes": r["notes"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                **s,
            }
        )
    return out


def get_author(conn: sqlite3.Connection, author_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if row is None:
        return None
    s = author_stats(conn).get(author_id, {"content_count": 0, "analyzed_count": 0})
    return {
        "id": int(row["id"]),
        "platform": row["platform"],
        "username": row["username"],
        "display_name": row["display_name"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        **s,
    }


def author_overview(conn: sqlite3.Connection, author_id: int) -> dict[str, Any]:
    """作者概览：**纯聚合现有 contents / analyses，0 AI 调用**。

    只能回答「这个作者通常写什么」——
    因为系统里没有 impressions / likes / reposts 等真实表现数据，
    所以这里**绝不**给出「什么最容易爆」之类的结论。
    写作画像 / 爆款分析属于后续阶段，本函数不越界。
    """
    base = conn.execute(
        """
        SELECT COUNT(*)                                             AS content_count,
               SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed_count,
               SUM(CASE WHEN source = 'mine'      THEN 1 ELSE 0 END) AS mine_count,
               SUM(CASE WHEN source = 'benchmark' THEN 1 ELSE 0 END) AS benchmark_count,
               MIN(created_at)                                      AS earliest_content_at,
               MAX(created_at)                                      AS latest_content_at
          FROM contents
         WHERE author_id = ?
        """,
        (author_id,),
    ).fetchone()

    content_count = int(base["content_count"] or 0)
    analyzed_count = int(base["analyzed_count"] or 0)

    # 主题 / 内容类型分布：只统计已分析内容（未分析的没有这两个字段）
    topic_rows = conn.execute(
        "SELECT a.topic AS name, COUNT(*) AS n "
        "  FROM contents c JOIN analyses a ON a.content_id = c.id "
        " WHERE c.author_id = ? AND IFNULL(a.topic, '') <> '' "
        " GROUP BY a.topic ORDER BY n DESC, a.topic ASC LIMIT 8",
        (author_id,),
    ).fetchall()
    type_rows = conn.execute(
        "SELECT a.content_type AS name, COUNT(*) AS n "
        "  FROM contents c JOIN analyses a ON a.content_id = c.id "
        " WHERE c.author_id = ? AND IFNULL(a.content_type, '') <> '' "
        " GROUP BY a.content_type ORDER BY n DESC, a.content_type ASC LIMIT 8",
        (author_id,),
    ).fetchall()

    # 常见长度：用 raw_text 实际字符数（永远可用），不用模型自报的 length_chars
    lengths = [
        int(r["n"])
        for r in conn.execute(
            "SELECT LENGTH(raw_text) AS n FROM contents WHERE author_id = ? ORDER BY n",
            (author_id,),
        ).fetchall()
    ]
    median_chars: int | None = None
    if lengths:
        mid = len(lengths) // 2
        median_chars = (
            lengths[mid]
            if len(lengths) % 2 == 1
            else (lengths[mid - 1] + lengths[mid]) // 2
        )

    return {
        "author_id": int(author_id),
        "content_count": content_count,
        "analyzed_count": analyzed_count,
        "unanalyzed_count": max(content_count - analyzed_count, 0),
        "mine_count": int(base["mine_count"] or 0),
        "benchmark_count": int(base["benchmark_count"] or 0),
        "earliest_content_at": base["earliest_content_at"],
        "latest_content_at": base["latest_content_at"],
        "avg_chars": int(sum(lengths) / len(lengths)) if lengths else None,
        "median_chars": median_chars,
        "topics": [{"name": r["name"], "count": int(r["n"])} for r in topic_rows],
        "content_types": [{"name": r["name"], "count": int(r["n"])} for r in type_rows],
    }


# ---------------------------------------------------------------- 作者写作画像（阶段 6）
#
# 纪律：
#   1. 唯一数据来源 = 该作者已分析帖子的 ANALYSIS 结果
#      （扁平索引列 + analysis_json 完整对象）。
#      **不再调用任何 AI、不重新分析原帖、不新建分析 schema。**
#   2. 只做确定性统计：计数 / 百分比 / 频次 / Top values。
#      不对自由文本做「二次解释」，不生成人格化结论，不打分。
#   3. 字段名**全部来自真实 ANALYSIS 合同**（services/analysis_spec.py），
#      不新造 opening_strategy / tone_profile 之类的同义字段。
#   4. 阶段 5 已经做过「写了什么」（帖子数 / 主题 / 类型 / 长度 / 时间），
#      这里的 content 组只做**跨帖分组**，不重复概览的计数。
#
# 路径语法：`a.b` 走嵌套对象；`a[].b` 表示数组逐元素取 b（见 analysis_path_values）。

WRITING_PROFILE_SECTIONS: list[dict[str, Any]] = [
    {
        "key": "content",
        "title": "内容定位",
        "fields": [
            {"path": "topic", "label": "主题", "kind": "categorical"},
            {"path": "subtopic", "label": "子主题", "kind": "categorical"},
            {"path": "content_type", "label": "内容类型", "kind": "categorical"},
        ],
    },
    {
        "key": "opening",
        "title": "开头 / 切入",
        "fields": [
            {"path": "writing.opening_type", "label": "开头方式", "kind": "categorical"},
        ],
    },
    {
        "key": "structure",
        "title": "结构 / 推进",
        "fields": [
            {"path": "writing.structure_type", "label": "整体结构",
             "kind": "categorical"},
            {"path": "writing.moves[].type", "label": "修辞 / 互动动作",
             "kind": "list"},
        ],
    },
    {
        "key": "ending",
        "title": "结尾",
        "fields": [
            {"path": "writing.ending_type", "label": "结尾方式", "kind": "categorical"},
        ],
    },
    {
        "key": "language",
        "title": "语言 / 形式",
        "fields": [
            {"path": "style.register", "label": "语体", "kind": "categorical"},
            {"path": "style.rhythm", "label": "句式节奏", "kind": "categorical"},
            {"path": "style.markers[].type", "label": "表达标记", "kind": "list"},
            {"path": "style.distinctive_phrases", "label": "显眼用语", "kind": "list"},
        ],
    },
    {
        "key": "stance",
        "title": "语气 / 立场",
        "fields": [
            {"path": "style.tone", "label": "语气", "kind": "categorical"},
            {"path": "style.directness", "label": "直接程度", "kind": "categorical"},
            {"path": "style.certainty", "label": "确定性", "kind": "categorical"},
        ],
    },
]

# 写作画像用到的**全部**路径（对象/数组节点不计）。
_WRITING_FIELD_INDEX: dict[str, dict[str, str]] = {
    f["path"]: f
    for section in WRITING_PROFILE_SECTIONS
    for f in section["fields"]
}

PROFILE_TOP_VALUES = 8          # 每个字段最多展示的取值数
PROFILE_EVIDENCE_PREVIEW = 3    # 聚合结果里默认附带的证据帖数

#: 扁平索引列 → 合同路径。列名恰好是路径的最后一段
#: （`writing.opening_type` 的列名就是 `opening_type`）。
#: 由 spec 的标量路径生成，字段改名时这里自动跟着变，不会漂移。
_FLAT_COLUMN_TO_CONTRACT_PATH: dict[str, str] = {
    path.split(".")[-1]: path for path in scalar_field_paths()
}


def author_analysis_samples(conn: sqlite3.Connection, author_id: int) -> list[sqlite3.Row]:
    """取出该作者「已分析且确有 ANALYSIS 结果」的帖子。

    口径：`analysis_state='analyzed'` **且** analyses 行存在 ——
    没有分析结果的帖子不能进入画像样本（否则会在画像里留下空桶）。

    `source_meta` 一并带出：语言过滤在 Python 侧做，
    这样「因为非中文被排除」的条数可以被**如实计数**，而不是在 SQL 里悄悄消失。
    """
    return conn.execute(
        "SELECT c.id AS content_id, c.source AS source, c.created_at AS created_at,"
        "       c.raw_text AS raw_text, c.source_meta AS source_meta,"
        "       a.analysis_json AS analysis_json,"
        "       a.topic AS topic, a.subtopic AS subtopic,"
        "       a.content_type AS content_type, a.core_message AS core_message,"
        "       a.opening_type AS opening_type, a.structure_type AS structure_type,"
        "       a.ending_type AS ending_type, a.tone AS tone,"
        "       a.register AS register, a.directness AS directness,"
        "       a.certainty AS certainty, a.rhythm AS rhythm "
        "  FROM contents c "
        "  JOIN analyses a ON a.content_id = c.id "
        " WHERE c.author_id = ? "
        "   AND COALESCE(c.analysis_state, 'unprocessed') = 'analyzed' "
        " ORDER BY c.created_at DESC, c.id DESC",
        (author_id,),
    ).fetchall()


def analysis_payload(row: sqlite3.Row) -> dict[str, Any]:
    """还原单篇 ANALYSIS 的完整结构：analysis_json + 扁平索引列兜底。

    扁平列**回填到合同路径**（`writing.opening_type`），而不是塞在顶层 ——
    顶层复制会造出 `opening_type` 与 `writing.opening_type` 两份同名数据，
    响应模型只声明其中一份，另一份就被静默丢弃，读的人永远搞不清真相在哪。

    回填的意义是**韧性**：`analysis_json` 缺失或损坏时，
    画像仍然能用索引列把标量聚合出来，而不是整篇变空。
    """
    payload: dict[str, Any] = {}
    raw = row["analysis_json"] if "analysis_json" in row.keys() else None
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload.update(parsed)
        except json.JSONDecodeError:
            pass

    for column, path in _FLAT_COLUMN_TO_CONTRACT_PATH.items():
        try:
            value = row[column]
        except IndexError:  # 该列不在本次 SELECT 中
            continue
        if value is None or not str(value).strip():
            continue
        if _read_contract_path(payload, path) not in (None, ""):
            continue                      # 合同 JSON 里已有值：以它为准
        _write_contract_path(payload, path, value)
    return payload


def _read_contract_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _write_contract_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = payload
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def analysis_path_values(payload: dict[str, Any], path: str, kind: str) -> list[str]:
    """按字段路径取值，并规整成字符串列表（同一篇内去重由调用方处理）。

    阶段 6（写作画像）与阶段 8（流量画像）**共用**这一份取值逻辑，
    保证两边用的字段口径永远一致。

    路径语法：
        `a.b`     → 逐层取字典键
        `a[].b`   → a 必须是数组，对每个元素取 b，结果**展平**
        `a[]`     → 数组本身逐元素展开

    数组支持是任务 04-A 新增的：新合同里 moves / markers / patterns 都是
    「带 evidence 的对象数组」，画像要统计的是它们的 `type` / `method`，
    没有数组下钻就只能整段当分类值，等于把结构信息浪费掉。
    """
    parts = [p for p in path.split(".") if p]
    current: Any = payload

    for idx, part in enumerate(parts):
        if part.endswith("[]"):
            key = part[:-2]
            node = current.get(key) if isinstance(current, dict) else None
            if not isinstance(node, list):
                return []
            rest = parts[idx + 1 :]
            if not rest:
                current = node
                break
            collected: list[Any] = []
            for item in node:
                if isinstance(item, dict):
                    collected.extend(_walk_dict(item, rest))
            current = collected
            break
        if not isinstance(current, dict):
            return []
        current = current.get(part)

    if isinstance(current, list):
        if kind == "list":
            return [str(x).strip() for x in current if str(x).strip()]
        # categorical 落到数组：展开成多个取值（例如 moves[].type 的 kind=list，
        # 但某些路径可能是 categorical 却指向数组）
        return [str(x).strip() for x in current if str(x).strip()]

    if kind == "list":
        if isinstance(current, str) and current.strip():
            return [p.strip() for p in re.split(r"[,，、;；\n]", current) if p.strip()]
        return []

    # categorical：只接受标量字符串 / 数字，绝不把对象或布尔当分类值
    if isinstance(current, bool) or current is None:
        return []
    if isinstance(current, (int, float)):
        return [str(current)]
    if isinstance(current, str) and current.strip():
        return [current.strip()]
    return []


def _walk_dict(node: dict[str, Any], parts: list[str]) -> list[Any]:
    """在数组元素里继续按剩余路径取值（只支持纯字典键，不做嵌套数组）。"""
    current: Any = node
    for part in parts:
        if not isinstance(current, dict):
            return []
        current = current.get(part)
    if isinstance(current, list):
        return list(current)
    return [current]


def _sample_meta(row: sqlite3.Row) -> dict[str, Any]:
    text = (row["raw_text"] or "").strip()
    return {
        "content_id": int(row["content_id"]),
        "source": row["source"],
        "created_at": row["created_at"],
        "excerpt": text[:80],
    }


def _build_metric_values(
    buckets: dict[str, list[int]],
    sample_count: int,
    meta_by_id: dict[int, dict[str, Any]],
    evidence_limit: int,
) -> dict[str, Any]:
    """把「值 → content_id 列表」变成可展示的分布 + 证据预览。

    百分比的分母是**画像样本数**（已分析帖数），与页面顶部「已分析样本 N / M」一致。
    """
    if not buckets:
        return {
            "values": [],
            "other_values_count": 0,
            "distinct_value_count": 0,
        }

    ordered = sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = ordered[:PROFILE_TOP_VALUES]

    values = []
    for value, ids in shown:
        evidence = [
            {**meta_by_id[cid], "value": value}
            for cid in ids[:evidence_limit]
            if cid in meta_by_id
        ]
        values.append(
            {
                "value": value,
                "count": len(ids),
                "pct": round(len(ids) / sample_count * 100, 1) if sample_count else 0.0,
                "evidence_total": len(ids),
                "evidence": evidence,
            }
        )

    return {
        "values": values,
        "other_values_count": len(ordered) - len(shown),
        "distinct_value_count": len(ordered),
    }


def author_profile_evidence(
    conn: sqlite3.Connection,
    author_id: int,
    field: str,
    value: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """某个聚合取值的**全量证据帖**。

    匹配是确定性的：该帖自己的 ANALYSIS V2 里必须真的含有这个值。
    绝不使用「看起来像」「随机挑几条」「模型觉得有代表性」。
    """
    field_def = _WRITING_FIELD_INDEX.get(field)
    if field_def is None:
        raise ValueError(f"未知的画像字段：{field}")

    target = (value or "").strip()
    rows = [
        r for r in author_analysis_samples(conn, author_id)
        if not is_non_chinese(r["source_meta"])
    ]

    matched: list[dict[str, Any]] = []
    for row in rows:
        values = analysis_path_values(analysis_payload(row), field, field_def["kind"])
        if target in values:
            matched.append({**_sample_meta(row), "value": target})

    total = len(matched)
    return matched[offset : offset + limit], total


def author_writing_profile(
    conn: sqlite3.Connection,
    author_id: int,
    *,
    evidence_limit: int = PROFILE_EVIDENCE_PREVIEW,
) -> dict[str, Any]:
    """作者写作画像：**对现有 ANALYSIS 结果做确定性聚合，0 AI 调用**。

    只统计「这个作者怎么写」，不重复阶段 5 已经给出的
    帖子数 / 主题 / 类型 / 长度 / 时间。
    每个取值都附带**真实匹配**的证据帖（该帖自己的分析确实含这个值）。

    语言口径（design §5）：**明确标注为非中文**的帖子默认排除，
    因为它会让「中文写作画像」的统计漂移；缺失语言或 und 的照常保留。
    被排除的条数写进 excluded_non_chinese，不静默吞掉。
    """
    all_rows = author_analysis_samples(conn, author_id)
    rows = [r for r in all_rows if not is_non_chinese(r["source_meta"])]
    excluded_non_chinese = len(all_rows) - len(rows)
    sample_count = len(rows)
    total_count = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM contents WHERE author_id = ?", (author_id,)
        ).fetchone()["n"]
    )
    coverage = round(sample_count / total_count, 4) if total_count else 0.0

    payloads = [analysis_payload(r) for r in rows]
    meta_by_id = {int(r["content_id"]): _sample_meta(r) for r in rows}

    sections: list[dict[str, Any]] = []
    for section in WRITING_PROFILE_SECTIONS:
        metrics: list[dict[str, Any]] = []
        for field in section["fields"]:
            buckets: dict[str, list[int]] = {}
            present_count = 0
            for row, payload in zip(rows, payloads):
                values = analysis_path_values(payload, field["path"], field["kind"])
                if not values:
                    continue
                present_count += 1
                content_id = int(row["content_id"])
                # 同一篇内同值只计一次，避免重复计数
                for value in dict.fromkeys(values):
                    buckets.setdefault(value, []).append(content_id)
            metrics.append(
                {
                    "field": field["path"],
                    "label": field["label"],
                    "kind": field["kind"],
                    "present_count": present_count,
                    **_build_metric_values(
                        buckets, sample_count, meta_by_id, evidence_limit
                    ),
                }
            )
        sections.append(
            {"key": section["key"], "title": section["title"], "metrics": metrics}
        )

    return {
        "author_id": int(author_id),
        "total_content_count": total_count,
        "analyzed_sample_count": sample_count,
        "coverage": coverage,
        # 因「明确非中文」被排除的条数（0 表示没有或全是中文/未知）
        "excluded_non_chinese": excluded_non_chinese,
        "sections": sections,
    }


# ---------------------------------------------------------------- 流量画像（阶段 8）
#
# 性能纪律：**一条 SQL 取全量，Python 侧聚合**。
# 绝不按 metric 逐个查库（2000 条 × 35 字段会变成几万次查询）。

def author_performance_samples(
    conn: sqlite3.Connection, author_id: int
) -> list[sqlite3.Row]:
    """取该作者全部帖子的「正文元信息 + 表现数据 + ANALYSIS」一次 JOIN。

    LEFT JOIN 是刻意的：没有 performance 或没有 analysis 的帖子也要出现，
    才能在 sample_summary 里如实统计被排除的原因。

    表现侧**一次取全部计数**（view/favorite/reply/retweet/quote/bookmark）：
    流量画像要按不同目标（曝光 / 讨论 / 传播 / 收藏）分别排序，
    逐目标再回表查询会让「逐帖 × 逐字段」变成几万次查询。
    """
    return conn.execute(
        "SELECT c.id            AS content_id,"
        "       c.source        AS source,"
        "       c.raw_text      AS raw_text,"
        "       c.posted_at     AS posted_at,"
        "       c.source_meta   AS source_meta,"
        "       COALESCE(c.analysis_state, 'unprocessed') AS analysis_state,"
        "       p.view_count    AS view_count,"
        "       p.favorite_count AS favorite_count,"
        "       p.reply_count   AS reply_count,"
        "       p.retweet_count AS retweet_count,"
        "       p.quote_count   AS quote_count,"
        "       p.bookmark_count AS bookmark_count,"
        "       p.is_pinned     AS is_pinned,"
        "       p.captured_at   AS captured_at,"
        "       a.analysis_json AS analysis_json,"
        "       a.topic         AS topic,"
        "       a.subtopic      AS subtopic,"
        "       a.content_type  AS content_type,"
        "       a.core_message  AS core_message,"
        "       a.structure_type AS structure_type,"
        "       a.opening_type  AS opening_type,"
        "       a.ending_type   AS ending_type,"
        "       a.tone          AS tone,"
        "       a.register      AS register,"
        "       a.directness    AS directness,"
        "       a.certainty     AS certainty,"
        "       a.rhythm        AS rhythm "
        "  FROM contents c "
        "  LEFT JOIN content_performance p ON p.content_id = c.id "
        "  LEFT JOIN analyses a ON a.content_id = c.id "
        " WHERE c.author_id = ? "
        " ORDER BY c.id ASC",
        (author_id,),
    ).fetchall()


def update_author(
    conn: sqlite3.Connection,
    author_id: int,
    *,
    username: str | None = None,
    display_name: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """编辑作者。

    若改 username，会**同步**该作者名下所有 contents.author_username，
    避免 authors.username 与 contents.author_username 长期漂移。
    """
    current = conn.execute("SELECT * FROM authors WHERE id = ?", (author_id,)).fetchone()
    if current is None:
        raise ValueError("作者不存在")

    new_username = current["username"]
    if username is not None:
        normalized = normalize_author_username(username)
        if not normalized:
            raise ValueError("用户名不能为空")
        new_username = normalized

    ts = now_iso()
    conn.execute(
        "UPDATE authors SET username = ?, display_name = ?, notes = ?, updated_at = ? "
        " WHERE id = ?",
        (
            new_username,
            display_name if display_name is not None else current["display_name"],
            notes if notes is not None else current["notes"],
            ts,
            author_id,
        ),
    )

    # 同步兼容字段，保持单一真实语义
    if new_username != current["username"]:
        conn.execute(
            "UPDATE contents SET author_username = ?, updated_at = ? WHERE author_id = ?",
            (new_username, ts, author_id),
        )

    return get_author(conn, author_id) or {}


def delete_author(conn: sqlite3.Connection, author_id: int) -> int:
    """删除作者。

    **只在没有任何关联内容时允许** —— 绝不 CASCADE 删除 contents。
    """
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM contents WHERE author_id = ?", (author_id,)
    ).fetchone()["n"]
    if int(count) > 0:
        raise ValueError(f"该作者仍有关联内容（{int(count)} 条），无法删除")
    cur = conn.execute("DELETE FROM authors WHERE id = ?", (author_id,))
    return int(cur.rowcount or 0)


def set_content_author(
    conn: sqlite3.Connection, ids: list[int], author_id: int | None
) -> int:
    """批量设置内容的作者（同时维护 author_id 与兼容字段 author_username）。

    不触碰 source / external_id / analysis / import_batch_id / style_eligible。
    """
    if not ids:
        return 0
    username = None
    if author_id is not None:
        row = conn.execute(
            "SELECT username FROM authors WHERE id = ?", (author_id,)
        ).fetchone()
        if row is None:
            raise ValueError("作者不存在")
        username = row["username"]

    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE contents SET author_id = ?, author_username = ?, updated_at = ? "
        f" WHERE id IN ({placeholders})",
        [author_id, username, now_iso(), *ids],
    )
    return int(cur.rowcount or 0)


def set_content_source_meta(
    conn: sqlite3.Connection, content_id: int, meta: dict[str, Any]
) -> None:
    """保存来源元数据。这些字段只用于溯源/去重，绝不拼进 raw_text。

    若 meta 带 author_username，会同时解析/创建正式作者，
    并同步写入 author_id + 规范化后的 author_username。

    扩展字段（conversation_id / language / reply_to_username / hashtags /
    urls / media_urls）写进 `contents.source_meta` JSON 列 —— 不建新表，
    不参与等值连接。已有 source_meta 时做**键级合并**，不整份覆盖。
    """
    updates = {
        key: meta[key]
        for key in _SOURCE_META_KEYS
        if meta.get(key) not in (None, "")
    }

    # 作者单独处理：normalize 后 resolve/create，并同步双字段。
    # author_name（Xporter 的显示名）只落到 authors.display_name，
    # 且仅在目标为空白时补写 —— 见 _backfill_display_name。
    raw_author = meta.get("author_username")
    if raw_author:
        author_id = resolve_author(
            conn,
            raw_author,
            display_name=meta.get(AUTHOR_DISPLAY_NAME_KEY),
        )
        if author_id:
            normalized = normalize_author_username(raw_author)
            updates["author_id"] = author_id
            updates["author_username"] = normalized

    extras = {
        key: meta[key]
        for key in SOURCE_META_EXTRA_KEYS
        if meta.get(key) not in (None, "", [], {})
    }
    if extras:
        current = conn.execute(
            "SELECT source_meta FROM contents WHERE id = ?", (int(content_id),)
        ).fetchone()
        merged: dict[str, Any] = {}
        if current is not None and current["source_meta"]:
            try:
                parsed = json.loads(current["source_meta"])
                if isinstance(parsed, dict):
                    merged.update(parsed)
            except (json.JSONDecodeError, TypeError):
                merged = {}
        merged.update(extras)
        updates["source_meta"] = json.dumps(merged, ensure_ascii=False)

    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE contents SET {assignments}, updated_at = ? WHERE id = ?",
        [*updates.values(), now_iso(), content_id],
    )


def merge_content_source_meta_extras(
    conn: sqlite3.Connection, content_id: int, meta: dict[str, Any]
) -> bool:
    """只合并**扩展**来源字段，不碰作者、不碰任何正式列。

    重复导入时必须用这个，而不是 set_content_source_meta：
    后者会按文件里的 author_username 重新 resolve 作者并覆盖 contents.author_id ——
    那就把「重复项不得改 author」这条规则破坏了。
    返回是否真的写入了内容。
    """
    extras = {
        key: meta[key]
        for key in SOURCE_META_EXTRA_KEYS
        if meta.get(key) not in (None, "", [], {})
    }
    if not extras:
        return False

    row = conn.execute(
        "SELECT source_meta FROM contents WHERE id = ?", (int(content_id),)
    ).fetchone()
    if row is None:
        return False

    merged = parse_source_meta(row["source_meta"])
    before = dict(merged)
    merged.update(extras)
    if merged == before:
        return False

    conn.execute(
        "UPDATE contents SET source_meta = ?, updated_at = ? WHERE id = ?",
        (json.dumps(merged, ensure_ascii=False), now_iso(), int(content_id)),
    )
    return True


def get_content_source_meta(
    conn: sqlite3.Connection, content_id: int
) -> dict[str, Any]:
    """读取扩展来源元数据；没有则返回空 dict（绝不返回 None）。"""
    row = conn.execute(
        "SELECT source_meta FROM contents WHERE id = ?", (int(content_id),)
    ).fetchone()
    if row is None or not row["source_meta"]:
        return {}
    return parse_source_meta(row["source_meta"])


def parse_source_meta(value: Any) -> dict[str, Any]:
    """把 source_meta 列（JSON 文本或 None）解析成 dict；坏数据返回空 dict。"""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


#: 语言字段里代表「中文」或「未判定」的取值。
#: und = undetermined：来源自己都说不知道，不能当成「非中文」排除掉。
_NEUTRAL_LANGUAGES = frozenset({"", "zh", "und"})


def language_of(source_meta: Any) -> str | None:
    """取语言标记（已小写、已去空白）；没有则 None。"""
    value = parse_source_meta(source_meta).get("language")
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def is_non_chinese(source_meta: Any) -> bool:
    """是否**明确**标注为非中文。

    只有「有语言标记、且既不是中文也不是未判定」才算明确非中文 ——
    缺失与 und 属于「不知道」，一律保留（design §5：未知语言照常保留但应标记）。
    """
    lang = language_of(source_meta)
    return lang is not None and lang not in _NEUTRAL_LANGUAGES


# ---------------------------------------------------------------- analyses

def save_analysis(
    conn: sqlite3.Connection,
    content_id: int,
    data: dict[str, Any],
    model: str,
    raw: dict[str, Any] | None = None,
) -> None:
    """保存分析结果。

    data 是**通过严格校验的完整 ANALYSIS 对象**（services/analysis_spec.py）。
    这里把它一分为二落库：

      - 扁平索引列：真正用于检索 / 分组 / 跨样本计数的标量；
      - analysis_json：完整规范 JSON，moves / markers / patterns / claims
        等带 evidence 的细节全部在这里，按路径读取。

    `raw` 参数保留是为了兼容既有调用签名；语义已统一 —— 传进来的就是
    要长期保存的规范结果，不再区分「模型原始返回」与「规整结果」。
    """
    payload = raw if raw is not None else data
    tags = data.get("tags") or []
    keywords = data.get("keywords") or []
    conn.execute(
        """
        INSERT INTO analyses(
            content_id, topic, subtopic, content_type, core_message,
            opening_type, structure_type, ending_type,
            tone, register, directness, certainty, rhythm,
            length_chars, tags, keywords, analysis_json,
            model, prompt_version, schema_version, schema_hash, created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(content_id) DO UPDATE SET
            topic=excluded.topic, subtopic=excluded.subtopic,
            content_type=excluded.content_type, core_message=excluded.core_message,
            opening_type=excluded.opening_type,
            structure_type=excluded.structure_type,
            ending_type=excluded.ending_type,
            tone=excluded.tone, register=excluded.register,
            directness=excluded.directness, certainty=excluded.certainty,
            rhythm=excluded.rhythm,
            length_chars=excluded.length_chars, tags=excluded.tags,
            keywords=excluded.keywords, analysis_json=excluded.analysis_json,
            model=excluded.model, prompt_version=excluded.prompt_version,
            schema_version=excluded.schema_version,
            schema_hash=excluded.schema_hash, created_at=excluded.created_at
        """,
        (
            content_id,
            data.get("topic"),
            data.get("subtopic"),
            data.get("content_type"),
            data.get("core_message"),
            (data.get("writing") or {}).get("opening_type"),
            (data.get("writing") or {}).get("structure_type"),
            (data.get("writing") or {}).get("ending_type"),
            (data.get("style") or {}).get("tone"),
            (data.get("style") or {}).get("register"),
            (data.get("style") or {}).get("directness"),
            (data.get("style") or {}).get("certainty"),
            (data.get("style") or {}).get("rhythm"),
            data.get("length_chars"),
            json.dumps(tags, ensure_ascii=False),
            json.dumps(keywords, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
            model,
            ANALYSIS_PROMPT_VERSION,
            ANALYSIS_SCHEMA_VERSION,
            ANALYSIS_SCHEMA_HASH,
            now_iso(),
        ),
    )


def get_analysis(conn: sqlite3.Connection, content_id: int) -> dict[str, Any] | None:
    """读取单篇分析结果。

    返回的是**完整规范对象 + 本地计算字段**：
    analysis_json 原样展开，再叠加扁平索引列（列值是校验后的稳定值）与
    `length_chars`（本地计算，不在模型合同里）。

    下游（WRITE 单稿 / 三候选 / 详情页）必须用这个函数拿完整对象 ——
    只取扁平列会丢掉 content.must_preserve 等关键约束。
    """
    row = conn.execute(
        "SELECT * FROM analyses WHERE content_id = ?", (content_id,)
    ).fetchone()
    if not row:
        return None

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(row["analysis_json"] or "{}")
        if isinstance(parsed, dict):
            payload.update(parsed)
    except (json.JSONDecodeError, TypeError):
        payload = {}

    # 扁平索引列**回填到合同路径**（而不是复制到顶层）：
    # analysis_json 损坏时画像仍能用索引列聚合，同时不制造同名重复键。
    for column, path in _FLAT_COLUMN_TO_CONTRACT_PATH.items():
        if _read_contract_path(payload, path) not in (None, ""):
            continue
        value = row[column]
        if value is not None and str(value).strip():
            _write_contract_path(payload, path, value)

    payload["length_chars"] = row["length_chars"]
    payload["tags"] = _loads(row["tags"])
    payload["keywords"] = _loads(row["keywords"])

    # 版本指纹：让调用方与 UI 能回答「这是哪版合同产出的」
    payload["model"] = row["model"]
    payload["prompt_version"] = row["prompt_version"]
    payload["schema_version"] = row["schema_version"]
    payload["schema_hash"] = row["schema_hash"]
    payload["created_at"] = row["created_at"]
    return payload


def _loads(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------- tags

def _ensure_tag(conn: sqlite3.Connection, name: str, kind: str) -> int:
    conn.execute(
        "INSERT INTO tags(name, kind, count) VALUES(?, ?, 0) "
        "ON CONFLICT(name) DO NOTHING",
        (name, kind),
    )
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def replace_content_tags(
    conn: sqlite3.Connection, content_id: int, items: list[tuple[str, str]]
) -> None:
    """items: [(name, kind)]，kind ∈ tag/topic/content_type/keyword。全量替换。"""
    conn.execute("DELETE FROM content_tags WHERE content_id = ?", (content_id,))
    seen: set[tuple[str, str]] = set()
    for name, kind in items:
        name = (name or "").strip()
        if not name or (name, kind) in seen:
            continue
        seen.add((name, kind))
        tag_id = _ensure_tag(conn, name, kind)
        conn.execute(
            "INSERT OR IGNORE INTO content_tags(content_id, tag_id, kind) VALUES(?,?,?)",
            (content_id, tag_id, kind),
        )
    _recount(conn)


# ---------------------------------------------------------------- 标签统计延迟
#
# 背景（V2 任务 02 实测）：`replace_content_tags` 每写一条内容就调用一次
# `_recount`，而 `_recount` 是**全量重算**（UPDATE 全 tags 表 + DELETE 空标签）。
# 单条成本随 content_tags 行数线性增长 → 整批写入变成 O(n²)：
#       1,000 条 151 ms → 10,000 条 5.84 s（去掉后仅 0.95 s）
#
# 修法：**只在批量路径**上把统计推迟到整批结束，最后统一算一次。
# 最终状态与「每条都算」完全一致 —— _recount 是全量重算而非增量，
# 中间态的 count 本来也没有任何读者。
#
# 为什么用 threading.local 的计数器而不是全局变量：
#   FastAPI 会把同步端点放进线程池，请求之间必须互不干扰。

_tag_recount_state = threading.local()


def _defer_depth() -> int:
    return int(getattr(_tag_recount_state, "depth", 0) or 0)


@contextmanager
def deferred_tag_recount() -> Iterator[None]:
    """区间内的 `replace_content_tags` 不再逐条重算标签统计。

    退出区间**不会**自动 flush —— 调用方必须在同一个事务里显式调用
    `flush_tag_recount(conn)`，否则标签计数会停在旧值。
    这样设计是为了让「回滚」天然安全：区间内抛异常 → 事务回滚 → 没有 flush，
    数据库与写入前完全一致。
    """
    _tag_recount_state.depth = _defer_depth() + 1
    try:
        yield
    finally:
        _tag_recount_state.depth = max(0, _defer_depth() - 1)


def flush_tag_recount(conn: sqlite3.Connection) -> None:
    """补做一次全量标签统计（批量写入结束时调用）。"""
    _recount_now(conn)


def _recount(conn: sqlite3.Connection) -> None:
    """`replace_content_tags` 的收尾统计。批量延迟区间内直接跳过。"""
    if _defer_depth() > 0:
        return
    _recount_now(conn)


def _recount_now(conn: sqlite3.Connection) -> None:
    conn.execute(
        "UPDATE tags SET count = ("
        "  SELECT COUNT(*) FROM content_tags ct WHERE ct.tag_id = tags.id)"
    )
    conn.execute("DELETE FROM tags WHERE count = 0 AND kind = 'tag'")


def list_facets(
    conn: sqlite3.Connection,
    *,
    source: str | None = None,
    q: str | None = None,
    tags: list[str] | None = None,
    topic: str | None = None,
    content_type: str | None = None,
    status: str | None = None,
    author_username: str | None = None,
    author_id: int | None = None,
    import_batch_id: str | None = None,
    style_eligible: bool | None = None,
) -> dict[str, Any]:
    """聚合左侧筛选项，口径与内容列表**完全一致**。

    关键约束（历史 bug 就出在这里）：

    1. 所有统计都必须以 contents 为主表并应用与列表相同的筛选条件。
       旧实现在 source 为空时**不 JOIN contents**，直接从 content_tags /
       analyses 统计，于是「内容已删、关联行残留」时会显示幽灵筛选项。
    2. 计数用 COUNT(DISTINCT c.id)，避免 JOIN 造成的重复计数。
    3. GROUP BY 天然只产出 count > 0 的项，因此 0 计数不会出现。
    4. 列表为空时，这里必然全部为空。
    """
    where_sql, params = _content_filter_clause(
        source=source, q=q, tags=tags, topic=topic,
        content_type=content_type, status=status,
        author_username=author_username, author_id=author_id,
        import_batch_id=import_batch_id, style_eligible=style_eligible,
    )

    # 标签：只统计落在「当前可见内容」上的标签
    tag_rows = conn.execute(
        "SELECT t.name AS name, COUNT(DISTINCT c.id) AS n "
        "  FROM contents c "
        "  LEFT JOIN analyses a ON a.content_id = c.id "
        "  JOIN content_tags ct ON ct.content_id = c.id "
        "  JOIN tags t ON t.id = ct.tag_id "
        f"{where_sql} AND ct.kind = 'tag' "
        " GROUP BY t.name ORDER BY n DESC, t.name ASC LIMIT 200",
        params,
    ).fetchall()

    topic_rows = conn.execute(
        "SELECT a.topic AS name, COUNT(DISTINCT c.id) AS n "
        "  FROM contents c "
        "  LEFT JOIN analyses a ON a.content_id = c.id "
        f"{where_sql} AND a.topic IS NOT NULL AND a.topic != '' "
        " GROUP BY a.topic ORDER BY n DESC LIMIT 100",
        params,
    ).fetchall()

    type_rows = conn.execute(
        "SELECT a.content_type AS name, COUNT(DISTINCT c.id) AS n "
        "  FROM contents c "
        "  LEFT JOIN analyses a ON a.content_id = c.id "
        f"{where_sql} AND a.content_type IS NOT NULL AND a.content_type != '' "
        " GROUP BY a.content_type ORDER BY n DESC LIMIT 100",
        params,
    ).fetchall()

    author_rows = conn.execute(
        "SELECT c.author_username AS name, COUNT(DISTINCT c.id) AS n "
        "  FROM contents c "
        "  LEFT JOIN analyses a ON a.content_id = c.id "
        f"{where_sql} AND c.author_username IS NOT NULL AND c.author_username != '' "
        " GROUP BY c.author_username ORDER BY n DESC LIMIT 100",
        params,
    ).fetchall()

    return {
        "tags": [{"name": r["name"], "count": r["n"]} for r in tag_rows],
        "topics": [{"name": r["name"], "count": r["n"]} for r in topic_rows],
        "content_types": [{"name": r["name"], "count": r["n"]} for r in type_rows],
        "authors": [{"name": r["name"], "count": r["n"]} for r in author_rows],
    }


# ---------------------------------------------------------------- 相似检索原料

def mine_contents_with_analysis(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """取出全部「我的内容」（final text）及其分析与来源信息，供相似度打分。

    注意：这里只读 contents 表（即用户确认过的 final text）。
    AI 初稿存放在 rewrites 表，永远不会出现在 contents 中，
    因此不可能被选为 reference example。
    """
    rows = conn.execute(
        "SELECT c.id, c.raw_text, c.provenance, "
        "       a.topic, a.subtopic, a.content_type, "
        "       a.tags, a.keywords, a.core_message, "
        "       r.edit_ratio "
        "  FROM contents c JOIN analyses a ON a.content_id = c.id "
        "  LEFT JOIN rewrites r ON r.saved_content_id = c.id "
        " WHERE c.source = 'mine' "
        "   AND COALESCE(c.analysis_state, 'unprocessed') = 'analyzed' "
        "   AND COALESCE(c.style_eligible, 0) = 1 "
        " ORDER BY c.created_at DESC"
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "raw_text": r["raw_text"],
                "provenance": r["provenance"],
                "edit_ratio": r["edit_ratio"],
                "topic": r["topic"],
                "subtopic": r["subtopic"],
                "content_type": r["content_type"],
                "tags": _loads(r["tags"]),
                "keywords": _loads(r["keywords"]),
                "core_message": r["core_message"],
            }
        )
    return out


def mine_contents_for_style(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """取出「我的内容」原文 + 分析 + **来源信息**，供风格档案计算。

    provenance / edit_ratio 必须逐条带出：风格档案需要判断每篇样本各自的
    证据强度（哪些是本人原作、哪些只是 AI 初稿几乎原样保存）。
    """
    rows = conn.execute(
        "SELECT c.id, c.raw_text, c.provenance, "
        "       a.topic, a.content_type, a.tone, "
        "       a.structure_type, a.opening_type, a.ending_type, "
        "       a.register, a.rhythm, a.directness, a.certainty, "
        "       a.length_chars, a.tags, a.keywords, "
        "       r.edit_ratio "
        "  FROM contents c JOIN analyses a ON a.content_id = c.id "
        "  LEFT JOIN rewrites r ON r.saved_content_id = c.id "
        " WHERE c.source = 'mine' "
        "   AND COALESCE(c.analysis_state, 'unprocessed') = 'analyzed' "
        "   AND COALESCE(c.style_eligible, 0) = 1 "
        " ORDER BY c.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- style_profile

def latest_style_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM style_profile ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    ids_raw = row["sample_ids"] if "sample_ids" in row.keys() else None
    try:
        sample_ids = json.loads(ids_raw) if ids_raw else []
    except (json.JSONDecodeError, TypeError):
        sample_ids = []
    return {
        "id": row["id"],
        "version": row["version"],
        "based_on_count": row["based_on_count"],
        "profile_json": _safe_json(row["profile_json"]),
        "summary_text": row["summary_text"],
        "sample_ids": [int(i) for i in sample_ids],
        "prompt_version": row["prompt_version"] if "prompt_version" in row.keys() else None,
        "profile_kind": row["profile_kind"] if "profile_kind" in row.keys() else None,
        "created_at": row["created_at"],
    }


def save_style_profile(
    conn: sqlite3.Connection,
    profile_json: dict[str, Any],
    summary_text: str,
    based_on_count: int,
    sample_ids: list[int] | None = None,
    prompt_version: str | None = None,
    profile_kind: str | None = None,
) -> dict[str, Any]:
    """保存一版风格档案。

    sample_ids 记录**本版实际使用**的 content_id（不是全部 eligible），
    这样才能准确回答「被关闭资格的内容是否真的污染着当前 Profile」。
    `prompt_version` 记录产出本版档案的 STYLE 提示词版本（04-A D6），
    让旧版档案可以被识别为旧版而不是冒充新版的产物。
    `profile_kind` 区分产出机制：legacy_min5 / initial_v20 / incremental
    （STYLE 增量学习架构 v3）—— 旧 5 篇门槛档案不得冒充新版 20 篇有效档案。
    """
    row = conn.execute("SELECT MAX(version) AS v FROM style_profile").fetchone()
    version = int(row["v"] or 0) + 1
    ts = now_iso()
    ids = list(sample_ids or [])
    cur = conn.execute(
        "INSERT INTO style_profile(version, based_on_count, profile_json, "
        "                          summary_text, sample_ids, prompt_version, "
        "                          profile_kind, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            version,
            based_on_count,
            json.dumps(profile_json, ensure_ascii=False),
            summary_text,
            json.dumps(ids, ensure_ascii=False),
            prompt_version,
            profile_kind,
            ts,
        ),
    )
    return {
        "id": int(cur.lastrowid),
        "version": version,
        "based_on_count": based_on_count,
        "profile_json": profile_json,
        "summary_text": summary_text,
        "sample_ids": ids,
        "prompt_version": prompt_version,
        "profile_kind": profile_kind,
        "created_at": ts,
    }


def _safe_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        out = json.loads(value)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------- rewrites

def create_rewrite(
    conn: sqlite3.Connection,
    *,
    benchmark_content_id: int | None,
    ai_draft: str,
    instruction: str | None,
    examples_used: list[int],
    style_profile_version: int | None,
    model: str,
    prompt_version: str | None = None,
    personalization_state: str | None = None,
) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO rewrites(benchmark_content_id, ai_draft, final_text, instruction, "
        "examples_used, style_profile_version, model, prompt_version, "
        "personalization_state, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            benchmark_content_id,
            ai_draft,
            ai_draft,  # final_text 初始等于初稿，用户编辑后覆盖
            instruction,
            json.dumps(examples_used, ensure_ascii=False),
            style_profile_version,
            model,
            prompt_version,
            personalization_state,
            ts,
            ts,
        ),
    )
    return int(cur.lastrowid)


def get_rewrite(conn: sqlite3.Connection, rewrite_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rewrites WHERE id = ?", (rewrite_id,)).fetchone()


def update_rewrite_final(
    conn: sqlite3.Connection,
    rewrite_id: int,
    final_text: str,
    edit_distance: int | None = None,
    edit_ratio: float | None = None,
) -> None:
    """保存用户终稿，并记录初稿→终稿的修改幅度。"""
    conn.execute(
        "UPDATE rewrites SET final_text = ?, "
        "       edit_distance = COALESCE(?, edit_distance), "
        "       edit_ratio = COALESCE(?, edit_ratio), "
        "       updated_at = ? "
        " WHERE id = ?",
        (final_text, edit_distance, edit_ratio, now_iso(), rewrite_id),
    )


def mark_rewrite_saved(
    conn: sqlite3.Connection, rewrite_id: int, saved_content_id: int
) -> None:
    conn.execute(
        "UPDATE rewrites SET is_saved_as_mine = 1, saved_content_id = ?, updated_at = ? "
        "WHERE id = ?",
        (saved_content_id, now_iso(), rewrite_id),
    )


def list_rewrites_for_content(
    conn: sqlite3.Connection, content_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rewrites WHERE benchmark_content_id = ? ORDER BY created_at DESC",
        (content_id,),
    ).fetchall()


def has_been_rewritten(conn: sqlite3.Connection, content_id: int) -> bool:
    """这条 benchmark content 是否已经有 rewrite 记录（阶段 10「未改写优先」）。

    ------------------------------------------------------------------
    unused 的**确切**语义
    ------------------------------------------------------------------
        unused = 没有任何 rewrite 记录引用该 benchmark_content_id
               = 「从未采用过改写候选」

    **不是**「尚未保存最终 content」：

      - 只生成候选、没点采用 → 仍然 unused（候选生成是无状态的，不写任何表）；
      - 点了「采用」→ 立刻建立 rewrite 记录 → 从此不再 unused，
        **即便用户之后既没改也没保存最终稿**。

    这是刻意设计：采用代表用户真的把这条素材用起来了，
    没必要再把它当作「没碰过的新鲜素材」反复推给用户。

    rewrites.benchmark_content_id 就是「本轮素材」的正式落点
    （API 入参里的 source_content_id 只是它的别名），且有索引，
    判断可靠、无需新 schema，也**不新增** used / material_status 字段或表。
    """
    row = conn.execute(
        "SELECT 1 FROM rewrites WHERE benchmark_content_id = ? LIMIT 1", (content_id,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------- 素材池（阶段 10）

# 素材池口径：**只看 benchmark + 正文非空**。
# mine 永远不进池；Authors.content_count 不能替代（它可能包含 mine）。
_MATERIAL_WHERE = "c.source = 'benchmark' AND TRIM(IFNULL(c.raw_text, '')) != ''"

_MATERIAL_FROM = (
    "FROM contents c "
    "LEFT JOIN authors a ON a.id = c.author_id "
    "LEFT JOIN content_performance p ON p.content_id = c.id "
)

_MATERIAL_SELECT = (
    "SELECT c.id AS content_id, c.raw_text, c.posted_at, c.author_id, "
    "       c.author_username, a.display_name AS author_display_name, "
    "       p.view_count, p.media_type "
)

# 「未被改写过」：rewrites.benchmark_content_id 就是本轮素材的正式落点
_UNUSED_CLAUSE = (
    " AND c.id NOT IN ("
    "   SELECT benchmark_content_id FROM rewrites"
    "   WHERE benchmark_content_id IS NOT NULL)"
)


def count_material_pools(conn: sqlite3.Connection) -> dict[str, Any]:
    """素材池计数：全部 / 空白作者 / 每个正式作者。

    「空白作者」= source=benchmark AND author_id IS NULL，
    **不是** authors 表里的一条记录，绝不为它创建假作者。
    """
    total = int(
        conn.execute(
            f"SELECT COUNT(*) AS n {_MATERIAL_FROM} WHERE {_MATERIAL_WHERE}"
        ).fetchone()["n"]
    )
    blank = int(
        conn.execute(
            f"SELECT COUNT(*) AS n {_MATERIAL_FROM} "
            f"WHERE {_MATERIAL_WHERE} AND c.author_id IS NULL"
        ).fetchone()["n"]
    )
    rows = conn.execute(
        "SELECT a.id AS author_id, a.username, a.display_name, COUNT(c.id) AS n "
        "FROM contents c JOIN authors a ON a.id = c.author_id "
        f"WHERE {_MATERIAL_WHERE} "
        "GROUP BY a.id, a.username, a.display_name "
        "ORDER BY n DESC, a.id"
    ).fetchall()
    authors = [
        {
            "author_id": int(r["author_id"]),
            "username": r["username"],
            "display_name": r["display_name"],
            "count": int(r["n"]),
        }
        for r in rows
    ]
    return {"all": total, "blank": blank, "authors": authors}


def random_material(
    conn: sqlite3.Connection,
    *,
    pool: str = "all",
    author_id: int | None = None,
    exclude_ids: list[int] | None = None,
    unused_first: bool = True,
) -> tuple[dict[str, Any] | None, bool]:
    """随机抽一条素材，返回 (material | None, fallback_used)。

    pool:
      all     → 全部 benchmark 素材
      blank   → author_id IS NULL（不建假作者）
      author  → author_id = 指定作者（必须传 author_id）

    unused_first=True 时先抽 unused 的素材；池内全部用过 → 自动降级允许重复
    （fallback_used=True），绝不阻塞用户。

    unused = 没有任何 rewrite 记录引用该 content_id = **从未采用过改写候选**，
    不是「尚未保存最终稿」（语义见 has_been_rewritten 的 docstring）。
    """
    clause = _MATERIAL_WHERE
    params: list[Any] = []

    if pool == "blank":
        clause += " AND c.author_id IS NULL"
    elif pool == "author":
        if author_id is None:
            raise ValueError("pool=author 必须提供 author_id")
        clause += " AND c.author_id = ?"
        params.append(int(author_id))
    elif pool != "all":
        raise ValueError(f"未知的素材池：{pool}")

    for cid in sorted({int(i) for i in (exclude_ids or []) if i}):
        clause += " AND c.id != ?"
        params.append(cid)

    def _pick(unused: bool) -> dict[str, Any] | None:
        row = conn.execute(
            f"{_MATERIAL_SELECT} {_MATERIAL_FROM} "
            f"WHERE {clause}{_UNUSED_CLAUSE if unused else ''} "
            "ORDER BY RANDOM() LIMIT 1",
            params,
        ).fetchone()
        return dict(row) if row else None

    if unused_first:
        found = _pick(True)
        if found:
            return found, False
        found = _pick(False)
        return (found, True) if found else (None, False)

    return _pick(False), False


# ---------------------------------------------------------------- 队列恢复

def list_unfinished(conn: sqlite3.Connection) -> list[int]:
    """未完成分析的内容 id（服务重启后需要重新入队）。"""
    rows = conn.execute(
        "SELECT id FROM contents "
        " WHERE status IN ('pending', 'analyzing') "
        " ORDER BY id ASC"
    ).fetchall()
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------- 修改样本

def list_edit_pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(AI 初稿, 用户终稿) 且两者确有差异的记录。

    这是「风格档案」中修改偏好的唯一合法来源：来自真实编辑行为，而非模型推测。
    """
    rows = conn.execute(
        "SELECT ai_draft, final_text FROM rewrites "
        " WHERE final_text IS NOT NULL "
        "   AND TRIM(final_text) != '' "
        "   AND TRIM(final_text) != TRIM(ai_draft) "
        " ORDER BY created_at DESC"
    ).fetchall()
    return [(r["ai_draft"] or "", r["final_text"] or "") for r in rows]


def count_rewrite_pairs(conn: sqlite3.Connection) -> int:
    """有效修改样本数量（有实际差异的初稿→终稿对）。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM rewrites "
        " WHERE final_text IS NOT NULL AND TRIM(final_text) != '' "
        "   AND TRIM(final_text) != TRIM(ai_draft)"
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------- batch_jobs

def create_batch_job(
    conn: sqlite3.Connection,
    *,
    model: str,
    batch_id: str | None,
    input_file_id: str | None,
    status: str,
    custom_map: dict[str, int],
    total: int,
    prompt_version: str | None = None,
    provider: str = "openai_compat",
) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO batch_jobs(provider, model, batch_id, input_file_id, status, "
        "custom_map, total, prompt_version, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            provider, model, batch_id, input_file_id, status,
            json.dumps(custom_map, ensure_ascii=False), total, prompt_version, ts, ts,
        ),
    )
    return int(cur.lastrowid)


def update_batch_job(conn: sqlite3.Connection, job_id: int, **fields: Any) -> None:
    allowed = {
        "batch_id", "input_file_id", "output_file_id", "status",
        "succeeded", "failed", "error",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE batch_jobs SET {assignments} WHERE id = ?",
        [*updates.values(), job_id],
    )


def get_batch_job(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM batch_jobs WHERE id = ?", (job_id,)).fetchone()


def list_unfinished_batches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """未完成的 Batch（服务重启后需要恢复查询，禁止重复提交）。"""
    return conn.execute(
        "SELECT * FROM batch_jobs "
        " WHERE status NOT IN ('completed', 'failed', 'expired', 'cancelled') "
        " ORDER BY id ASC"
    ).fetchall()


# ================================================================
# 「我的」V1：目标 / 合格浏览量快照 / 账号日指标
# ================================================================
# 全部 0 AI，且只服务 /mine —— 不参与内容分析、风格档案或任何既有流程。

# account_daily_metrics 的 13 个指标列（**数据层唯一真源**）。
# 顺序与 Account Overview CSV 的列顺序一致，便于逐列对齐。
MINE_METRIC_COLUMNS: tuple[str, ...] = (
    "impressions",
    "likes",
    "engagements",
    "bookmarks",
    "shares",
    "new_follows",
    "unfollows",
    "replies",
    "reposts",
    "profile_visits",
    "create_post",
    "video_views",
    "media_views",
)

# 每个指标的查询语句**在模块加载时就固化**，键是白名单里允许的 metric 名。
#
# 这是刻意的：用户输入只可能命中 dict 的键，绝不可能进入 SQL 文本。
# 因此不存在「拼接用户输入」这条路径 —— 哪怕将来新增指标，
# 也只能在这里硬编码，不会被 API 参数带着走。
_MINE_METRIC_QUERY_SQL: dict[str, str] = {
    metric: (
        "SELECT metric_date, %s AS value FROM account_daily_metrics "
        " WHERE %s IS NOT NULL ORDER BY metric_date ASC" % (metric, metric)
    )
    for metric in MINE_METRIC_COLUMNS
}


def mine_metric_supported(metric: str) -> bool:
    return metric in _MINE_METRIC_QUERY_SQL


def list_mine_metric(
    conn: sqlite3.Connection, metric: str
) -> list[dict[str, Any]]:
    """按日期升序返回某指标的非空序列。metric 必须已过白名单校验。

    NULL 被排除：NULL 表示「那天没采集到」，既不能画点也不能做差分。
    """
    rows = conn.execute(_MINE_METRIC_QUERY_SQL[metric]).fetchall()
    return [{"metric_date": r["metric_date"], "value": int(r["value"])} for r in rows]


# ---------------------------------------------------------------- 账号日指标写入

_MINE_UPSERT_SQL = (
    "INSERT INTO account_daily_metrics("
    "  metric_date, impressions, likes, engagements, bookmarks, shares,"
    "  new_follows, unfollows, replies, reposts, profile_visits,"
    "  create_post, video_views, media_views, updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(metric_date) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in MINE_METRIC_COLUMNS)
    + ", updated_at = excluded.updated_at"
)


def upsert_account_daily_metric(
    conn: sqlite3.Connection, row: dict[str, Any]
) -> bool:
    """写入一天。**整行覆盖**（不是逐列合并、不是累加）。

    返回 True = 覆盖了已有的一天；False = 新插入。
    """
    existed = conn.execute(
        "SELECT 1 FROM account_daily_metrics WHERE metric_date = ?",
        (row["metric_date"],),
    ).fetchone()
    conn.execute(
        _MINE_UPSERT_SQL,
        [row["metric_date"], *[row[c] for c in MINE_METRIC_COLUMNS], now_iso()],
    )
    return existed is not None


def count_account_daily_metrics(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS n FROM account_daily_metrics").fetchone()["n"]
    )


# ---------------------------------------------------------------- 合格浏览量快照

def _row_to_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "recorded_at": row["recorded_at"],
        "qualified_impressions": int(row["qualified_impressions"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_qualified_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """按 recorded_at 升序（目标进度与图表都依赖这个顺序）。"""
    rows = conn.execute(
        "SELECT * FROM qualified_impression_snapshots ORDER BY recorded_at ASC, id ASC"
    ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def insert_qualified_snapshot(
    conn: sqlite3.Connection, recorded_at: str, qualified_impressions: int
) -> dict[str, Any]:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO qualified_impression_snapshots("
        "  recorded_at, qualified_impressions, created_at, updated_at) "
        "VALUES(?,?,?,?)",
        (recorded_at, qualified_impressions, ts, ts),
    )
    return get_qualified_snapshot(conn, int(cur.lastrowid)) or {}


def get_qualified_snapshot(
    conn: sqlite3.Connection, snapshot_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM qualified_impression_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    return _row_to_snapshot(row) if row else None


def update_qualified_snapshot(
    conn: sqlite3.Connection,
    snapshot_id: int,
    recorded_at: str | None,
    qualified_impressions: int | None,
) -> dict[str, Any] | None:
    """只更新**显式传入**的字段（None = 不改）。"""
    updates: dict[str, Any] = {}
    if recorded_at is not None:
        updates["recorded_at"] = recorded_at
    if qualified_impressions is not None:
        updates["qualified_impressions"] = qualified_impressions
    if not updates:
        return get_qualified_snapshot(conn, snapshot_id)
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE qualified_impression_snapshots SET {assignments} WHERE id = ?",
        [*updates.values(), snapshot_id],
    )
    return get_qualified_snapshot(conn, snapshot_id)


def delete_qualified_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM qualified_impression_snapshots WHERE id = ?", (snapshot_id,)
    )
    return (cur.rowcount or 0) > 0


# ---------------------------------------------------------------- 创作者目标

def get_creator_goal(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM creator_goal WHERE id = 1").fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "start_date": row["start_date"],
        "baseline_qualified_impressions": int(row["baseline_qualified_impressions"]),
        "created_at": row["created_at"],
    }


def insert_creator_goal(
    conn: sqlite3.Connection, start_date: str, baseline: int
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO creator_goal(id, start_date, "
        "  baseline_qualified_impressions, created_at) "
        "VALUES(1,?,?,?)",
        (start_date, baseline, now_iso()),
    )
    del cur  # id 恒为 1
    return get_creator_goal(conn) or {}


def update_creator_goal(
    conn: sqlite3.Connection, start_date: str, baseline: int
) -> bool:
    """就地改写唯一目标（id=1）。返回 False 表示目标不存在。

    只动 start_date 与 baseline_qualified_impressions 两列：
      - **不**新建行 —— 单例语义不允许出现第二条目标；
      - **不**保留旧值 —— 这是「修正计划参数」，不是「版本历史」；
      - created_at 保持最初建立时间，方便事后知道这个目标存在了多久。

    TARGET / DURATION 是常量，不在这张表里，所以无法也不会被改到。
    """
    cur = conn.execute(
        "UPDATE creator_goal SET start_date = ?, "
        "  baseline_qualified_impressions = ? WHERE id = 1",
        (start_date, baseline),
    )
    return cur.rowcount > 0
