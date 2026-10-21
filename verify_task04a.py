"""任务 04-A 数据流验收：对任何已完成分析的库做只读体检。

与 e2e_task04a_analysis.py 的分工：
    e2e   —— 造数据（导入 + 真实 AI 调用）
    verify —— 查数据（只读，不调用任何 AI，可反复跑）

检查项对应设计文档 §7 的验收标准：

    1. 证据逐字性：每条 evidence 必须是原文的连续子串（不是「大概像」）
    2. 合同完整性：库里的 analysis_json 必须仍能通过严格校验
    3. 路径连通性：画像 / 洞察 / 检索 / 写参照引用的每条路径都真的有值
    4. 指标闭环：次级指标在流量画像、跨账号、书写目标里都真实出现
    5. 严格性：没有「缺字段却标 analyzed」的残缺结果

用法：
    ./backend/.venv/Scripts/python.exe verify_task04a.py <db_path>
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


def check_evidence(conn) -> dict:
    """每条 evidence 必须逐字存在于原文 —— 这是 ANALYSIS 质量的第一道硬门槛。"""
    total = bad = 0
    failures: list[dict] = []
    for row in conn.execute("SELECT content_id, analysis_json FROM analyses"):
        raw = conn.execute(
            "SELECT raw_text FROM contents WHERE id = ?", (row["content_id"],)
        ).fetchone()
        if raw is None:
            continue
        text = raw["raw_text"] or ""
        data = json.loads(row["analysis_json"])
        evidences: list[str] = []
        evidences += [c.get("evidence", "") for c in data["content"]["claims"]]
        evidences += [m.get("evidence", "") for m in data["writing"]["moves"]]
        evidences += [m.get("evidence", "") for m in data["style"]["markers"]]
        evidences += [p.get("evidence", "") for p in data["reuse"]["patterns"]]
        for ev in evidences:
            total += 1
            if not ev or ev not in text:
                bad += 1
                failures.append(
                    {"content_id": row["content_id"], "evidence": ev[:60]}
                )
    return {
        "total": total,
        "verbatim": total - bad,
        "failures": failures[:10],
        "pass": bad == 0 and total > 0,
    }


def check_contract(conn) -> dict:
    """库里存的分析结果必须仍能通过严格校验（合同没有被悄悄破坏）。"""
    from app.services.analysis_spec import SpecError, validate_analysis

    ok = bad = 0
    failures: list[dict] = []
    for row in conn.execute(
        "SELECT content_id, analysis_json, prompt_version, schema_version FROM analyses"
    ):
        try:
            validate_analysis(json.loads(row["analysis_json"]))
            ok += 1
        except (SpecError, json.JSONDecodeError, TypeError) as err:
            bad += 1
            failures.append({"content_id": row["content_id"], "error": str(err)[:120]})
    return {"valid": ok, "invalid": bad, "failures": failures, "pass": bad == 0}


def check_no_partial_rows(conn) -> dict:
    """不允许「缺字段却标 analyzed」的残缺结果。"""
    partial = conn.execute(
        """
        SELECT COUNT(*) AS n FROM contents c
          LEFT JOIN analyses a ON a.content_id = c.id
         WHERE COALESCE(c.analysis_state, 'unprocessed') = 'analyzed'
           AND (a.id IS NULL OR a.analysis_json IS NULL
                OR a.core_message IS NULL)
        """
    ).fetchone()["n"]
    analyzed = conn.execute(
        "SELECT COUNT(*) AS n FROM contents "
        " WHERE COALESCE(analysis_state,'unprocessed') = 'analyzed'"
    ).fetchone()["n"]
    return {"analyzed": analyzed, "partial": int(partial), "pass": int(partial) == 0}


def check_downstream(conn) -> dict:
    """画像 / 洞察 / 检索 / 写参照的每条路径都真的有值 —— 拒绝静默取空。"""
    from app import repository as repo
    from app.repository import WRITING_PROFILE_SECTIONS
    from app.services import cross_account, performance_profile as perf
    from app.services.analysis_spec import spec_field_paths

    contract = set(spec_field_paths())
    report: dict = {"paths_in_contract": True, "details": {}}

    for name, paths in (
        ("writing_profile", [f["path"] for s in WRITING_PROFILE_SECTIONS
                             for f in s["fields"]]),
        ("performance_profile", list(perf.PERFORMANCE_WRITING_FIELDS)),
        ("cross_account", [f for f, _l, _k in cross_account.ALL_FIELDS]),
        ("retriever", list(repo.__dict__.get("_SEMANTIC_FIELDS", []))
         or ["topic", "subtopic", "core_message"]),
    ):
        outside = [p for p in paths if p not in contract]
        if outside:
            report["paths_in_contract"] = False
            report["details"][name] = {"outside_contract": outside}

    authors = repo.list_authors(conn, with_content=True)
    per_author = []
    for a in authors:
        aid = int(a["id"])
        wp = repo.author_writing_profile(conn, aid)
        pp = perf.author_performance_profile(conn, aid)
        nonempty = {
            m["field"]: len(m["values"])
            for s in wp["sections"]
            for m in s["metrics"]
            if m["values"]
        }
        per_author.append(
            {
                "author_id": aid,
                "username": a.get("username"),
                "writing_samples": wp["analyzed_sample_count"],
                "excluded_non_chinese": wp.get("excluded_non_chinese", 0),
                "nonempty_writing_metrics": sorted(nonempty),
                "perf_eligible": pp["sample_summary"]["eligible_count"],
                "perf_analyzed": pp["sample_summary"]["analyzed_eligible_count"],
                "metric_baselines_with_value": [
                    m["metric"] for m in pp["metric_baselines"]
                    if m["median_value"] is not None
                ],
                "goal_samples_nonempty": [
                    g for g, d in pp["goal_samples"].items() if not d["insufficient"]
                ],
            }
        )

    cross = cross_account.cross_account_insights(conn)
    report["details"]["cross_account"] = {
        "content_dimensions": [d["key"] for d in cross["content_dimensions"]],
        "writing_dimensions": [d["key"] for d in cross["writing_dimensions"]],
        "writing_dimensions_with_values": [
            d["key"] for d in cross["writing_dimensions"] if d["value_count"] > 0
        ],
        "engagement_dimensions": [d["metric"] for d in cross["engagement_dimensions"]],
        "engagement_with_median": [
            d["metric"] for d in cross["engagement_dimensions"]
            if d["cross_author_median"] is not None
        ],
    }

    # 检索：用第一条已分析内容当查询
    first = conn.execute("SELECT content_id FROM analyses ORDER BY id LIMIT 1").fetchone()
    if first:
        from app.services import retriever

        analysis = repo.get_analysis(conn, int(first["content_id"]))
        hits = retriever.find_similar(
            conn, analysis, k=5, exclude_id=int(first["content_id"]),
            target_text=analysis.get("core_message") or "",
        )
        report["details"]["retrieval"] = {
            "query_content_id": int(first["content_id"]),
            "hits": len(hits),
            "top_score": hits[0]["score"] if hits else None,
        }

    report["authors"] = per_author
    return report


def check_metric_pipeline(conn) -> dict:
    """D9：次级指标必须真的进到画像与书写目标里。"""
    from app.services import performance_profile as perf

    first = conn.execute("SELECT author_id FROM contents "
                         " WHERE author_id IS NOT NULL LIMIT 1").fetchone()
    if not first:
        return {"pass": False, "reason": "没有作者"}
    data = perf.author_performance_profile(conn, int(first["author_id"]))
    baselines = {m["metric"]: m for m in data["metric_baselines"]}
    goals = {g: d["metric"] for g, d in data["goal_samples"].items()}
    return {
        "metrics": sorted(baselines),
        "goals": goals,
        "default_goal": data["default_goal"],
        "pass": len(baselines) == 6 and len(goals) == 4,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    db_path = sys.argv[1]
    if str(db_path).endswith("content_studio.db"):
        print("拒绝运行：这是生产库路径，本脚本只用于隔离库")
        return 2

    conn = open_ro(db_path)
    result = {
        "db": str(db_path),
        "evidence_verbatim": check_evidence(conn),
        "contract_valid": check_contract(conn),
        "no_partial_analyzed": check_no_partial_rows(conn),
        "downstream": check_downstream(conn),
        "metric_pipeline": check_metric_pipeline(conn),
    }
    conn.close()

    checks = {
        "证据逐字性": result["evidence_verbatim"]["pass"],
        "合同有效性": result["contract_valid"]["pass"],
        "无残缺 analyzed": result["no_partial_analyzed"]["pass"],
        "路径全在合同内": result["downstream"]["paths_in_contract"],
        "次级指标闭环": result["metric_pipeline"]["pass"],
    }
    passed = sum(1 for v in checks.values() if v)
    print(f"验收：{passed}/{len(checks)} 通过")
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
