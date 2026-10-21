"""V2 任务 02 端到端：用**临时数据库**造一份接近真实规模的语料。

- 正文取自真实 Xporter 样本 `hope yin 小曦 10.6 159条帖子.json`（159 条真实文本循环）
- 3 个作者 + 无作者内容
- 混合 unprocessed / failed / analyzed / cancelled / queued / processing

绝不触碰 data/content_studio.db。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND))

TMP = Path(os.environ.get("CS_E2E_DIR", "")) / "e2e.db"

from app import repository as repo  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402

SAMPLE = Path(r"C:\Users\18483\Desktop\x运营\hope yin 小曦 10.6 159条帖子.json")


def wipe(conn: sqlite3.Connection) -> None:
    for t in (
        "content_tags", "analyses", "tags", "rewrites", "style_profile",
        "analysis_runs", "batch_jobs", "import_batches", "content_performance",
        "contents", "authors",
    ):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sqlite_sequence")


def main() -> None:
    TMP.parent.mkdir(parents=True, exist_ok=True)
    init_db(TMP)

    posts = json.loads(SAMPLE.read_text(encoding="utf-8"))
    texts = [(p.get("text") or "").strip() or f"空正文 {i}" for i, p in enumerate(posts)]
    print(f"真实样本：{len(texts)} 条正文")

    authors = {"h0peyin": 2400, "wangchangfu88": 1900, "laolei": 1500}
    no_author = 400
    total = sum(authors.values()) + no_author

    with get_conn(TMP) as conn:
        wipe(conn)
        author_ids = {}
        for name in authors:
            author_ids[name] = repo.create_author(
                conn, username=name, display_name=name.upper()
            )

        rows = []
        idx = 0
        for name, n in authors.items():
            for i in range(n):
                rows.append((texts[idx % len(texts)], author_ids[name], f"e{idx}"))
                idx += 1
        for i in range(no_author):
            rows.append((texts[idx % len(texts)], None, f"e{idx}"))
            idx += 1

        conn.executemany(
            "INSERT INTO contents(source, raw_text, external_id, author_id, author_username,"
            " status, analysis_state, style_eligible, created_at, updated_at)"
            " VALUES('benchmark', ?, ?, ?, ?, 'pending', 'unprocessed', 0, '2026-01-01', '2026-01-01')",
            [
                (text, ext, aid, ("h0peyin" if aid else None))
                for text, aid, ext in rows
            ],
        )
        # 用户名同步（真实导入路径会同步两个字段）
        for name, aid in author_ids.items():
            conn.execute(
                "UPDATE contents SET author_username = ? WHERE author_id = ?", (name, aid)
            )

        # 状态混合：失败 7%，已分析 12%，已取消 3%，排队 1%，分析中 0.5%
        conn.execute("UPDATE contents SET analysis_state='failed'   WHERE id % 100 < 7")
        conn.execute("UPDATE contents SET analysis_state='analyzed' WHERE id % 100 >= 7  AND id % 100 < 19")
        conn.execute("UPDATE contents SET analysis_state='cancelled' WHERE id % 100 >= 19 AND id % 100 < 22")
        conn.execute("UPDATE contents SET analysis_state='queued'    WHERE id % 100 >= 22 AND id % 100 < 23")
        conn.execute("UPDATE contents SET analysis_state='processing' WHERE id % 100 >= 23 AND id % 100 < 24")

        stats = {}
        for r in conn.execute(
            "SELECT COALESCE(analysis_state,'unprocessed') st, COUNT(*) n FROM contents GROUP BY st"
        ):
            stats[r["st"]] = int(r["n"])
        authors_n = conn.execute(
            "SELECT COUNT(DISTINCT author_id) n FROM contents WHERE author_id IS NOT NULL"
        ).fetchone()["n"]
        no_author_n = conn.execute(
            "SELECT COUNT(*) n FROM contents WHERE author_id IS NULL"
        ).fetchone()["n"]

    print(f"数据库：{TMP}")
    print(f"总内容：{total}（作者 {authors_n} 位，无作者 {no_author_n} 条）")
    print(f"状态分布：{stats}")
    pending = stats.get("unprocessed", 0) + stats.get("failed", 0)
    all_exportable = pending + stats.get("analyzed", 0) + stats.get("cancelled", 0)
    print(f"仅待分析（unprocessed+failed）         = {pending}")
    print(f"全部可导出（+analyzed+cancelled）      = {all_exportable}")
    print("EXPECT_PENDING=%d" % pending)
    print("EXPECT_ALL=%d" % all_exportable)
    print("AUTHOR_IDS=%s" % ",".join(str(v) for v in author_ids.values()))


if __name__ == "__main__":
    main()
