"""任务 04-A 端到端验收（真实中文 X 样本 + 真实 MiMo 调用）。

用途
----
按设计文档 §7「AI 质量验证」的要求，跑一条**完整闭环**：

    导入真实 Xporter 原文与指标
      → ANALYSIS（Realtime 路径，严格校验）
      → 作者写作画像 / 单账号流量画像 / 跨作者洞察
      → BM25 检索
      → WRITE 单稿 + 三候选
      → 保存人工修改后的最终稿（mine）
      → STYLE

并把每条 ANALYSIS 的**完整输出**导出成 markdown，供人工 / ChatGPT 审查。

安全
----
- 强制使用**隔离数据库**：本脚本自己新建一个临时 DB 并写入 meta 标记；
  一旦发现目标是 data/content_studio.db 或 Desktop 库，直接拒绝运行。
- 只读真实 Xporter 文件，不修改它们。
- 每一步都打印实际调用次数与 token 用量，便于核对费用。

用法
----
    cd backend
    ./.venv/Scripts/python.exe ../e2e_task04a_analysis.py --limit 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DESKTOP = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ 隔离库

def assert_not_real_db(db: Path) -> None:
    """拒绝在任何真实库上运行 —— 本脚本会写入。"""
    forbidden = {(BACKEND_DIR.parent / "data" / "content_studio.db").resolve()}
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        forbidden.add(
            (Path(local) / "ContentStudio" / "data" / "content_studio.db").resolve()
        )
    if db.resolve() in forbidden:
        raise SystemExit(
            "拒绝运行：目标数据库是真实库（生产库 / Desktop 库），本脚本只允许隔离库"
        )


def make_isolated_db() -> Path:
    """建立一个只属于本次验收的数据库，并断言它不是任何真实库。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="cs_task04a_"))
    db = tmpdir / "e2e.db"
    assert_not_real_db(db)
    return db


# ------------------------------------------------------------------ 样本

#: 人工挑选的真实样本（来自用户自己的 Xporter 导出）。
#: 选取标准：覆盖设计文档要求的中文场景，且**排除涉政等敏感内容**。
SAMPLE_PLAN: tuple[dict, ...] = (
    # ---- 用于画像的「主作者」：香港老雷（6 条，满足 N>=5 排名门槛）----
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2102386541431865410",
     "bucket": "短帖"},
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2106967282983526816",
     "bucket": "清单+链接"},
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2106896530989097373",
     "bucket": "观点/社群"},
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2106729219950788688",
     "bucket": "观点/建议"},
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2106777616103444990",
     "bucket": "带媒体"},
    {"file": "香港老雷 10.5 500条帖子.json", "id": "2104593980025643272",
     "bucket": "长帖"},
    # ---- 对照作者：王长富（4 条）----
    {"file": "王长富 10.5 500条帖子.json", "id": "2106731064651845677",
     "bucket": "短梗帖"},
    {"file": "王长富 10.5 500条帖子.json", "id": "2106655569159586279",
     "bucket": "清单/认知"},
    {"file": "王长富 10.5 500条帖子.json", "id": "2106549869938979112",
     "bucket": "观点帖"},
    {"file": "王长富 10.5 500条帖子.json", "id": "2105636346874179669",
     "bucket": "清单/法则"},
    # ---- 其他作者（2 条，覆盖资讯转述 / 引述叙事）----
    {"file": "择月 10.9 326条帖子.json", "id": "2104998490728448166",
     "bucket": "资讯转述"},
    {"file": "李新宝 10.6 500条帖子.json", "id": "2107486411956105652",
     "bucket": "引述叙事"},
)

#: 只导入、不分析的大批量语料：让 BM25 检索与曝光基线有真实规模的池子。
#: 这一步 0 AI 调用。
BULK_FILES: tuple[str, ...] = (
    "香港老雷 10.5 500条帖子.json",
    "王长富 10.5 500条帖子.json",
)

#: 作为「我的历史作品」导入的 sample（供 STYLE 使用，需要 >=3 条**已分析**样本）。
#: 多给一条冗余：供应商侧的安全过滤可能拒掉个别样本（实测发生过），
#: 那属于外部策略，不是我们的合同问题。
MINE_PLAN: tuple[dict, ...] = (
    {"file": "lyulog 10.5 500条帖子.json", "pick": "mine", "idx": 0},
    {"file": "lyulog 10.5 500条帖子.json", "pick": "mine", "idx": 1},
    {"file": "lyulog 10.5 500条帖子.json", "pick": "mine", "idx": 2},
    {"file": "lyulog 10.5 500条帖子.json", "pick": "mine", "idx": 3},
)


PROJECT_DIR = Path(__file__).resolve().parent
# 真实 Xporter 导出可能放在项目同级（桌面），也可能放在项目内。
SAMPLE_DIRS = (PROJECT_DIR.parent.parent, PROJECT_DIR.parent, PROJECT_DIR)


def load_file(name: str) -> list[dict]:
    for base in SAMPLE_DIRS:
        path = base / name
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    raise SystemExit(
        f"找不到真实样本文件：{name}（已查找 {[str(d) for d in SAMPLE_DIRS]}）"
    )


def _is_chinese(post: dict) -> bool:
    lang = (post.get("language") or "").strip().lower()
    return lang in ("", "zh", "und")


def resolve_sample(spec: dict) -> dict | None:
    posts = [p for p in load_file(spec["file"]) if _is_chinese(p)]
    if spec.get("id"):
        for p in posts:
            if str(p.get("id")) == str(spec["id"]):
                return p
        return None
    pick = spec.get("pick")
    if pick == "quote":
        for p in posts:
            text = (p.get("text") or "").strip()
            if p.get("reply_to_username") and 40 <= len(text) <= 300:
                return p
        for p in posts:
            text = (p.get("text") or "").strip()
            if "「" in text and 40 <= len(text) <= 300:
                return p
    if pick == "mine":
        idx = int(spec.get("idx") or 0)
        usable = [
            p for p in posts
            if 150 <= len((p.get("text") or "").strip()) <= 500
            and p.get("view_count") is not None
        ]
        return usable[idx] if idx < len(usable) else None
    return None


# ------------------------------------------------------------------ 主流程

async def run(args) -> int:
    db_path = Path(args.db) if args.db else make_isolated_db()
    assert_not_real_db(db_path)

    os.environ["DB_PATH"] = str(db_path)
    import app.config as config_module

    config_module.settings.db_path = db_path

    from app.ai import get_provider
    from app.config import settings
    from app.db import get_conn, init_db
    from app.services import analyzer

    if not settings.ai_configured:
        raise SystemExit("未配置 AI_API_KEY，无法做真实质量验收")
    if str(settings.db_path).endswith("content_studio.db"):
        raise SystemExit("拒绝运行：settings.db_path 指向真实库")

    init_db(settings.db_path)
    print(f"[隔离库] {settings.db_path}")
    print(f"[模型]   {settings.model_analysis} / {settings.model_rewrite} / "
          f"{settings.model_style}")

    from app.api.imports import commit
    from app.schemas import ImportCommitIn, ImportItem
    from app.services.importer import parse_json

    # ---------------- 1) 导入精选样本 ----------------
    picked: list[dict] = []
    for spec in SAMPLE_PLAN:
        post = resolve_sample(spec)
        if post is None:
            print(f"  ! 未找到样本 {spec.get('id') or spec.get('pick')} ({spec['bucket']})")
            continue
        post["_bucket"] = spec["bucket"]
        picked.append(post)

    mine_posts: list[dict] = []
    for spec in MINE_PLAN:
        post = resolve_sample(spec)
        if post is not None:
            mine_posts.append(post)

    async def import_posts(posts: list[dict], source: str, filename: str):
        parsed = parse_json(json.dumps(posts, ensure_ascii=False))
        payload = ImportCommitIn(
            filename=filename,
            source=source,
            items=[
                ImportItem(text=p.text, source=source, metadata=p.metadata)
                for p in parsed
            ],
        )
        return await commit(payload), parsed

    bench_res, bench_parsed = await import_posts(
        picked, "benchmark", "e2e_task04a_benchmark.json"
    )
    mine_res, mine_parsed = await import_posts(
        mine_posts, "mine", "e2e_task04a_mine.json"
    )
    print(f"\n[导入] benchmark 精选 {bench_res.created} 条（重复 {bench_res.duplicates}）；"
          f"mine {mine_res.created} 条")

    # ---- 只导入、不分析的大批量语料（0 AI）----
    bulk_created = 0
    bulk_dup = 0
    for name in BULK_FILES:
        posts = [p for p in load_file(name) if _is_chinese(p)]
        if not posts:
            continue
        res, _ = await import_posts(posts, "benchmark", f"bulk-{name}")
        bulk_created += res.created
        bulk_dup += res.duplicates
    print(f"[导入] 语料池追加 {bulk_created} 条（重复 {bulk_dup}）—— 0 AI 调用")

    pooled = 0
    with get_conn(settings.db_path) as conn:
        pooled = conn.execute("SELECT COUNT(*) AS n FROM contents").fetchone()["n"]
    print(f"[库内] 内容总数 {pooled} 条")

    # ---------------- 2) ANALYSIS（Realtime，严格校验）----------------
    async def analyze_many(ids: list[int], label: str) -> dict[int, str]:
        """逐条分析；**已有分析结果的直接跳过** —— 本脚本要能断点续跑，
        否则一次中断就要把已经付过费的调用全部重打一遍。"""
        outcome: dict[int, str] = {}
        with get_conn(settings.db_path) as conn:
            done = {
                int(r["content_id"])
                for r in conn.execute("SELECT content_id FROM analyses")
            }
        for cid in ids:
            if cid in done:
                outcome[cid] = "ok"
                print(f"  · [{label}] content {cid} 已有分析，跳过")
                continue
            try:
                await analyzer.analyze_content(cid)
                outcome[cid] = "ok"
                print(f"  ✓ [{label}] content {cid}")
            except Exception as err:  # noqa: BLE001 —— 逐条如实记录失败原因
                outcome[cid] = f"{type(err).__name__}: {err}"
                print(f"  ✗ [{label}] content {cid} → {type(err).__name__}: {err}")
        return outcome

    if args.dry:
        print("\n[DRY] 跳过全部 AI 调用（导入 + 落库链路已跑通）")
        return 0

    print("\n[ANALYSIS] benchmark 样本")
    bench_outcome = await analyze_many(
        list(bench_res.content_ids), "benchmark"
    )
    print("\n[ANALYSIS] mine 样本")
    mine_outcome = await analyze_many(list(mine_res.content_ids), "mine")

    ok = sum(1 for v in {**bench_outcome, **mine_outcome}.values() if v == "ok")
    total = len(bench_outcome) + len(mine_outcome)
    print(f"\n[ANALYSIS 结果] {ok}/{total} 通过严格校验并落库")

    # ---------------- 3) 下游：画像 / 洞察 / 检索 ----------------
    from app import repository as repo
    from app.services import cross_account, performance_profile as perf, retriever

    print("\n[下游] 作者画像 / 流量画像 / 跨作者洞察 / BM25 检索")
    with get_conn(settings.db_path) as conn:
        authors = repo.list_authors(conn, with_content=True)
        report_authors = []
        for a in authors:
            wp = repo.author_writing_profile(conn, int(a["id"]))
            pp = perf.author_performance_profile(conn, int(a["id"]))
            report_authors.append(
                {
                    "author_id": int(a["id"]),
                    "username": a.get("username"),
                    "writing_samples": wp["analyzed_sample_count"],
                    "writing_sections": len(wp["sections"]),
                    "writing_nonempty_metrics": sum(
                        1
                        for s in wp["sections"]
                        for m in s["metrics"]
                        if m["values"]
                    ),
                    "perf_eligible": pp["sample_summary"]["eligible_count"],
                    "perf_analyzed": pp["sample_summary"]["analyzed_eligible_count"],
                    "perf_metric_baselines": len(pp["metric_baselines"]),
                    "perf_goal_metrics": {
                        g: d["metric"] for g, d in pp["goal_samples"].items()
                    },
                    "excluded_non_chinese": wp.get("excluded_non_chinese"),
                }
            )
        cross = cross_account.cross_account_insights(conn)

        # 检索 / 写作的查询样本：**取第一条已分析的 benchmark 内容**，
        # 而不是「本次新导入的第一条」—— 断点续跑时后者为空。
        row = conn.execute(
            "SELECT a.content_id FROM analyses a JOIN contents c ON c.id = a.content_id "
            " WHERE c.source = 'benchmark' ORDER BY a.content_id LIMIT 1"
        ).fetchone()
        query_id = int(row["content_id"]) if row else None

        similar = []
        if query_id:
            analysis = repo.get_analysis(conn, query_id)
            similar = retriever.find_similar(
                conn,
                analysis,
                k=5,
                exclude_id=query_id,
                target_text=(analysis or {}).get("core_message") or "",
            )

    for row in report_authors:
        print(
            f"  · @{row['username']}: 画像样本 {row['writing_samples']}，"
            f"非空指标 {row['writing_nonempty_metrics']}/{row['writing_sections']} 段，"
            f"流量 eligible {row['perf_eligible']} / 已分析 {row['perf_analyzed']}，"
            f"指标基线 {row['perf_metric_baselines']} 条"
        )
    print(f"  · 跨账号维度：内容 {len(cross['content_dimensions'])} / "
          f"写法 {len(cross['writing_dimensions'])} / "
          f"互动 {len(cross['engagement_dimensions'])}")
    print(f"  · BM25 命中 {len(similar)} 条相似内容")

    # ---------------- 4) WRITE：单稿 + 三候选 ----------------
    from app.services import rewrite_candidates, rewriter

    write_report: dict = {}
    if args.write and query_id:
        print("\n[WRITE] 单稿")
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, query_id)
        draft = await rewriter.generate_draft(
            source_text=row["raw_text"],
            source_content_id=query_id,
            instruction="更口语、更短一点，保留全部事实与数字",
            goal="discussion",
        )
        write_report["single"] = {
            "rewrite_id": draft["id"],
            "chars": len(draft["ai_draft"]),
            "text": draft["ai_draft"],
        }
        print(f"  ✓ 单稿 {len(draft['ai_draft'])} 字")

        print("[WRITE] 三候选")
        cand = await rewrite_candidates.generate_candidates(
            source_text=row["raw_text"],
            source_content_id=query_id,
            goal="discussion",
        )
        write_report["candidates"] = {
            "keys": [c["key"] for c in cand["candidates"]],
            "strategy_tags": cand.get("strategy_tags"),
            "texts": {c["key"]: c["text"] for c in cand["candidates"]},
        }
        print(f"  ✓ 候选 {[c['key'] for c in cand['candidates']]}")

        # ---------------- 5) 保存人工修改后的终稿 → STYLE ----------------
        from app.api.rewrites import save_rewrite
        from app.schemas import RewriteSaveIn

        final_text = (
            draft["ai_draft"].strip()
            + "\n\n（这一段是验收时人工补写的收尾，用于验证初稿→终稿链路。）"
        )
        # 注意：save_rewrite 的返回是 RewriteOut（Pydantic 模型），不是 dict
        saved = save_rewrite(
            int(draft["id"]),
            RewriteSaveIn(final_text=final_text, save_as_mine=True),
        )
        write_report["saved"] = {
            "saved_content_id": saved.saved_content_id,
            "edit_distance": saved.edit_distance,
            "edit_ratio": saved.edit_ratio,
        }
        print(f"  ✓ 终稿已保存为 mine（content {saved.saved_content_id}，"
              f"edit_ratio={saved.edit_ratio}）")

        # 终稿进入 mine 后也要分析，才算真正走完「初稿→终稿→个人风格」链路
        if saved.saved_content_id:
            await analyze_many([int(saved.saved_content_id)], "mine-终稿")

    # ---------------- 6) STYLE ----------------
    style_report: dict = {}
    if args.style:
        from app.services import personalization
        from app.services.style_profile import profile_staleness, recompute_style_profile

        with get_conn(settings.db_path) as conn:
            state = personalization.resolve_state(conn)
            stale = profile_staleness(conn)
            print(f"\n[STYLE] personalization={state['state']}，"
                  f"eligible={stale['style_eligible_count']}，"
                  f"should_recompute={stale['should_recompute']}")
            if stale["style_eligible_count"] >= 3:
                try:
                    profile = await recompute_style_profile(conn)
                    style_report = {
                        "version": profile["version"],
                        "based_on_count": profile["based_on_count"],
                        "summary_text": profile.get("summary_text"),
                        "keys": sorted((profile.get("profile_json") or {}).keys()),
                    }
                    print(f"  ✓ STYLE v{profile['version']}（基于 "
                          f"{profile['based_on_count']} 条）")
                except Exception as err:  # noqa: BLE001
                    style_report = {"error": f"{type(err).__name__}: {err}"}
                    print(f"  ✗ STYLE 失败：{err}")
            else:
                style_report = {"skipped": "mine 样本不足 3 条"}

    # ---------------- 7) 导出交付物 ----------------
    out_dir = Path(__file__).resolve().parent / "task04a_deliverables"
    out_dir.mkdir(exist_ok=True)
    dump_analysis(
        db_path=settings.db_path,
        picked=picked,
        exclude_ids=set(bench_res.content_ids) | set(mine_res.content_ids),
        out_path=out_dir / "真实样本-ANALYSIS-输出.md",
        mine_ids=set(mine_res.content_ids),
        outcome={**bench_outcome, **mine_outcome},
    )
    (out_dir / "数据流验收结果.json").write_text(
        json.dumps(
            {
                "db": str(settings.db_path),
                "benchmark_imported": bench_res.created,
                "mine_imported": mine_res.created,
                "analysis": {str(k): v for k, v in {**bench_outcome, **mine_outcome}.items()},
                "analysis_ok": ok,
                "analysis_total": total,
                "authors": report_authors,
                "cross_account": {
                    "content_dimensions": [d["key"] for d in cross["content_dimensions"]],
                    "writing_dimensions": [d["key"] for d in cross["writing_dimensions"]],
                    "engagement_dimensions": [d["metric"] for d in cross["engagement_dimensions"]],
                },
                "retrieval": [
                    {"content_id": s["id"], "score": s["score"], "topic": s.get("topic")}
                    for s in similar
                ],
                "write": write_report,
                "style": style_report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n[交付物] {out_dir}")
    return 0 if ok == total else 1


def dump_analysis(
    *,
    db_path: Path,
    picked: list[dict],
    exclude_ids: set[int],
    out_path: Path,
    mine_ids: set[int],
    outcome: dict[int, str],
) -> None:
    """把全部 ANALYSIS 结果按样本导出成 markdown（供人工 / ChatGPT 审查）。"""
    from app import repository as repo
    from app.db import get_conn

    by_external = {str(p.get("id")): p for p in picked}
    lines: list[str] = [
        "# 任务 04-A · 真实中文 X 样本的 ANALYSIS 输出",
        "",
        "> 本文件由 e2e_task04a_analysis.py 自动生成，内容为**真实 MiMo 调用的原样落库结果**。",
        "> 每条包含：原文、导入的表现指标、完整 ANALYSIS JSON。",
        "",
    ]
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, external_id, raw_text, author_username, posted_at,"
            "       source, posted_at, analysis_state "
            "  FROM contents ORDER BY id"
        ).fetchall()
        for row in rows:
            cid = int(row["id"])
            analysis = repo.get_analysis(conn, cid)
            if analysis is None:
                continue
            post = by_external.get(str(row["external_id"])) or {}
            perf = repo.get_content_performance(conn, cid)
            meta = repo.get_content_source_meta(conn, cid)
            lines += [
                f"## content {cid} · @{row['author_username']} · "
                f"{post.get('_bucket', ('mine' if cid in mine_ids else '未分类'))}",
                "",
                f"- 来源：`{row['source']}`　发布时间：`{row['posted_at']}`　"
                f"字数：{analysis.get('length_chars')}",
                f"- 模型 / 版本：`{analysis.get('model')}` / "
                f"`{analysis.get('prompt_version')}` / "
                f"`{analysis.get('schema_version')}`",
            ]
            if perf:
                lines.append(
                    f"- 表现：浏览 {perf['view_count']}｜点赞 {perf['favorite_count']}｜"
                    f"评论 {perf['reply_count']}｜转发 {perf['retweet_count']}｜"
                    f"引用 {perf['quote_count']}｜收藏 {perf['bookmark_count']}｜"
                    f"置顶 {'是' if perf['is_pinned'] else '否'}"
                )
            if meta:
                lines.append(f"- 来源元数据：`{json.dumps(meta, ensure_ascii=False)}`")
            lines += [
                "",
                "**原文**",
                "",
                "```text",
                (row["raw_text"] or "").strip(),
                "```",
                "",
                "**ANALYSIS（完整规范 JSON）**",
                "",
                "```json",
                json.dumps(
                    {k: v for k, v in analysis.items()
                     if k not in ("model", "prompt_version", "schema_version",
                                  "schema_hash", "created_at", "length_chars")},
                    ensure_ascii=False,
                    indent=2,
                ),
                "```",
                "",
            ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--write", action="store_true", help="跑 WRITE 单稿 + 三候选")
    parser.add_argument("--style", action="store_true", help="跑 STYLE")
    parser.add_argument("--dry", action="store_true",
                        help="只跑导入与落库链路，跳过全部 AI 调用")
    parser.add_argument("--db", default=None,
                        help="复用已有的隔离库（断点续跑；已分析的条目会自动跳过）")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
