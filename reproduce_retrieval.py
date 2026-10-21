"""检索误命中复现（只读）。

背景
----
《数据流验收结果.json》记录了一次围绕 content 1（"给你100万删掉推特 你愿意？"）
的检索，第五条命中是 content 15「女性外阴形态的俗称分类」，score=0.579。
单个误命中不足以证明整套检索需要重建 —— 先**只读复现**并拆出评分构成。

本脚本：
  1. 不改任何数据（sqlite 以 mode=ro 打开）；
  2. 不调用任何 AI；
  3. 逐条打印：语义 BM25 原分、原文 BM25 原分、相关性、原文辅助分、
     来源权重、结构化加权，以及**实际共享的 token** ——
     只有看到匹配词，才能判断「是弱相关词漏过阈值」还是别的成因。

用法：
    ./backend/.venv/Scripts/python.exe reproduce_retrieval.py <db_path> [query_content_id]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def open_ro(db_path: str) -> sqlite3.Connection:
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    db_path = sys.argv[1]
    query_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    from app import repository as repo
    from app.services import retriever as R

    conn = open_ro(db_path)

    row = conn.execute("SELECT raw_text FROM contents WHERE id = ?", (query_id,)).fetchone()
    if row is None:
        print(f"content {query_id} 不存在")
        return 1
    query_text = row["raw_text"]
    q_raw = R.tokenize(query_text)
    analysis = repo.get_analysis(conn, query_id)

    print("=" * 100)
    print(f"查询内容：content {query_id}")
    print(f"原文    ：{query_text!r}")
    print(f"topic   ：{analysis.get('topic')}")
    print(f"core    ：{(analysis.get('core_message') or '')[:120]}")

    print(f"\n判定主体语料（topic/subtopic/tags/keywords）：{R._semantic_primary(analysis)!r}")
    print(f"辅助语料（core_message，不参与阈值判定）：{R._core_message(analysis)!r}")
    print(f"完整语义语料（仅供展示，判定不再直接用）：{R._semantic_text(analysis)!r}")
    print(f"\n判定主体 token（{len(R.tokenize(R._semantic_primary(analysis)))}）："
          f"{R.tokenize(R._semantic_primary(analysis))}")
    print(f"原文 token（{len(q_raw)}）：{q_raw}")

    candidates = repo.mine_contents_with_analysis(conn)
    candidates = [c for c in candidates if c["id"] != query_id]
    print(f"\n候选池：{len(candidates)} 条「我的内容」（有分析）")
    if not candidates:
        print("候选池为空 —— 无法复现")
        return 1

    t_topic = (analysis.get("topic") or "").strip()
    t_type = (analysis.get("content_type") or "").strip()
    t_tags = set(analysis.get("tags") or [])

    print(f"\n阈值 MIN_RELEVANCE = {R.MIN_RELEVANCE}；原文辅助权重 = {R.W_RAW_ASSIST}")
    print(f"查询 topic = {t_topic!r}，content_type = {t_type!r}")
    print(f"判定主体字段 = {R._SEMANTIC_PRIMARY_FIELDS} + tags + keywords"
          f"；辅助通道 = {R._SEMANTIC_ASSIST_FIELD!r} + raw_text\n")

    # 权威结果：直接调生产实现，避免本脚本与生产逻辑漂移
    authoritative = R.find_similar(
        conn, analysis, k=5, exclude_id=query_id, target_text=query_text
    )
    authoritative_ids = [r["id"] for r in authoritative]

    header = (
        f"{'id':>5} {'prim_raw':>9} {'rel':>6} {'core_raw':>9} {'raw_raw':>8} "
        f"{'辅助':>6} {'来源':>5} {'total':>6}  共享 token（判定主体）"
    )
    print(header)
    print("-" * 130)

    primary_docs = [R.tokenize(R._semantic_primary(c)) for c in candidates]
    core_docs = [R.tokenize(R._core_message(c)) for c in candidates]
    raw_docs = [R.tokenize(R._raw_text(c)) for c in candidates]
    q_primary = R.tokenize(R._semantic_primary(analysis))
    q_core = R.tokenize(R._core_message(analysis))
    primary_scores = R._bm25(q_primary, primary_docs)
    core_scores = R._bm25(q_core, core_docs) if q_core else [0.0] * len(candidates)
    raw_scores = R._bm25(q_raw, raw_docs) if q_raw else [0.0] * len(candidates)

    rows = []
    for idx, cand in enumerate(candidates):
        prim_raw = primary_scores[idx]
        core_raw = core_scores[idx]
        raw_raw = raw_scores[idx]
        if R._has_primary_fields(cand):
            relevance = prim_raw / (prim_raw + R.SOFT_K)
            basis = "primary"
        else:
            relevance = raw_raw / (raw_raw + R.SOFT_K)
            basis = "raw-fallback"
        core_assist = core_raw / (core_raw + R.SOFT_K) if core_raw > 0 else 0.0
        raw_assist = raw_raw / (raw_raw + R.SOFT_K) if raw_raw > 0 else 0.0
        assist = R.W_RAW_ASSIST * (core_assist + raw_assist)
        prov = R.provenance_weight(cand.get("provenance"), cand.get("edit_ratio"))
        t_bonus = (
            R.BONUS_TOPIC
            if t_topic and t_topic == (cand.get("topic") or "").strip()
            else 0.0
        )
        ty_bonus = (
            R.BONUS_TYPE
            if t_type and t_type == (cand.get("content_type") or "").strip()
            else 0.0
        )
        total = min(relevance + assist + prov + t_bonus + ty_bonus, 1.0)
        rows.append(
            {
                "id": cand["id"], "topic": cand.get("topic"),
                "prim_raw": round(prim_raw, 3), "core_raw": round(core_raw, 3),
                "raw_raw": round(raw_raw, 3), "relevance": round(relevance, 4),
                "assist": round(assist, 4), "provenance_w": prov,
                "topic_bonus": t_bonus, "type_bonus": ty_bonus,
                "score": round(total, 3),
                "passed_threshold": relevance >= R.MIN_RELEVANCE,
                "in_result": cand["id"] in authoritative_ids,
                "basis": basis,
                "shared_primary": sorted(set(q_primary) & set(primary_docs[idx])),
                "shared_core": sorted(set(q_core) & set(core_docs[idx])),
                "shared_raw": sorted(set(q_raw) & set(raw_docs[idx])),
                "excerpt": (cand["raw_text"] or "")[:60],
            }
        )

    rows.sort(key=lambda r: r["score"], reverse=True)
    for r in rows:
        flag = "→结果" if r["in_result"] else ("过阈值" if r["passed_threshold"] else "×过滤")
        print(
            f"{r['id']:>5} {r['prim_raw']:>9.3f} {r['relevance']:>6.3f} "
            f"{r['core_raw']:>9.3f} {r['raw_raw']:>8.3f} {r['assist']:>6.3f} "
            f"{r['provenance_w']:>5.2f} {r['score']:>6.3f}  {flag}  "
            f"{r['shared_primary']}"
        )

    print(f"\n生产实现 find_similar 返回（k=5）：{[r['id'] for r in authoritative]}")
    for r in authoritative:
        print(f"  content {r['id']}  score={r['score']}  topic={r['topic']!r}")
    filtered = [r["id"] for r in rows if not r["passed_threshold"]]
    print(f"\n被阈值过滤（未进候选）：{sorted(filtered)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
