"""04-C WRITE 升级验证：五条路径的 prompt 形态 + **真实**中文 X 三候选样本。

------------------------------------------------------------------
两条模式
------------------------------------------------------------------
    （默认 dry） 只组装 prompt，不调用模型，打印结构与字数
    --real      调用**一次**真实 MiMo，产出三候选样本并落 JSON 证据

真实样本每次运行只打 **1 次** provider.chat()（要求 7：三候选仍然是一次调用）。

------------------------------------------------------------------
纪律
------------------------------------------------------------------
- 隔离数据库：生产库 data/content_studio.db 与 Desktop V1 **只读不改**；
- mimo-v2.6-pro，保留 Thinking，不传 temperature / max_tokens；
- 参考作者的分析数据是**脚本构造的夹具**（不是用户真实数据），
  报告里必须如实标注，不得描述成真实分析结果。

用法：
    ./backend/.venv/Scripts/python.exe validate_write_04c.py
    ./backend/.venv/Scripts/python.exe validate_write_04c.py --real
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

OUT_DIR = Path(__file__).resolve().parent / "task04c_deliverables"

#: 待改写的原帖（素材）。来自真实中文 X 常见题材：AI 工具 / 效率。
SOURCE_POST = """很多人问我 AI 工具到底该怎么选。

我的答案一直是：别选工具，先选问题。

去年我试了 17 个 AI 写作工具，最后留下来的只有 2 个。
不是因为那 2 个最强，而是因为它们刚好卡在我真正的两个问题上：
一个是把长材料压成要点，一个是在我写完之后帮我找逻辑漏洞。

工具评测看的是功能表，你真正需要看的是自己的工作流里到底断在哪一段。"""

#: 仅主题创作的输入（不是原帖，只是方向）
TOPIC_IDEA = """想写一条关于"为什么大多数人用 AI 工具用不出效果"的内容，
重点是我自己踩过的坑：不是工具不行，是把工具当成了答案而不是杠杆。"""

#: 参考作者：脚本夹具（不是用户真实数据）
AUTHOR_USERNAME = "老雷"


def make_db() -> Path:
    path = Path(tempfile.mkdtemp(prefix="cs_write_04c_")) / "write_04c.db"
    os.environ["DB_PATH"] = str(path)
    import app.config as cm

    cm.settings.db_path = path
    return path


def restore_db_path(path: Path) -> None:
    """导入 tests.test_regression 会在模块级改写 settings.db_path，必须恢复。"""
    import app.config as cm

    cm.settings.db_path = path
    os.environ["DB_PATH"] = str(path)


def v3_analysis(text: str, *, topic: str, opening: str) -> dict:
    """一条能通过 ANALYSIS v3.1 合同的最小结果（脚本夹具）。"""
    return {
        "topic": topic,
        "subtopic": None,
        "content_type": "观点表达",
        "core_message": text[:40],
        "tags": ["AI", "工具"],
        "keywords": ["AI", "工具", "工作流"],
        "content": {
            "claims": [
                {
                    "kind": "作者判断",
                    "text": "选工具不如先选问题",
                    "evidence": text[:8],
                }
            ],
            "must_preserve": [],
            "context_limits": [],
        },
        "writing": {
            "opening_type": opening,
            "structure_type": "观点—理由",
            "ending_type": "行动号召",
            "moves": [
                {"type": "对比", "evidence": text[:6], "mechanism": "用反差建立张力"}
            ],
        },
        "style": {
            "tone": "轻松",
            "register": "口语",
            "directness": "直接",
            "certainty": "明确肯定",
            "rhythm": "长短交替",
            "markers": [{"type": "换行", "evidence": text[:4]}],
            "distinctive_phrases": [],
        },
        "reuse": {"patterns": [], "boundaries": []},
    }


def seed_reference_author(conn, username: str) -> int:
    """一个有「ANALYSIS + 表现数据」的参考作者：6 高 + 6 低（脚本夹具）。"""
    from app import repository as repo

    aid = repo.create_author(conn, username=username)
    for i in range(6):
        text = f"高表现样本 {i}：直接给结论，再补两条理由，正文长度足够用于统计。"
        cid = repo.create_content(conn, "benchmark", text)
        repo.save_analysis(
            conn, cid, v3_analysis(text, topic="AI工具", opening="直接陈述"),
            "seed", raw=None,
        )
        repo.set_analysis_status(conn, cid, "analyzed")
        repo.set_content_author(conn, [cid], aid)
        _attach_perf(conn, cid, views=120_000 + i * 500)
    for i in range(6):
        text = f"低表现样本 {i}：先讲一段背景故事，最后才给结论，正文长度足够。"
        cid = repo.create_content(conn, "benchmark", text)
        repo.save_analysis(
            conn, cid, v3_analysis(text, topic="效率方法", opening="故事场景"),
            "seed", raw=None,
        )
        repo.set_analysis_status(conn, cid, "analyzed")
        repo.set_content_author(conn, [cid], aid)
        _attach_perf(conn, cid, views=800 + i * 10)
    return aid


def _attach_perf(conn, content_id: int, *, views: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO content_performance("
        " content_id, view_count, favorite_count, reply_count, retweet_count,"
        " quote_count, bookmark_count, is_pinned, media_type, captured_at)"
        " VALUES(?,?,0,0,0,0,0,0,'',datetime('now'))",
        (content_id, views),
    )
    conn.execute(
        "UPDATE contents SET posted_at = datetime('now', '-5 days') WHERE id = ?",
        (content_id,),
    )


def build_messages(state: str, source_text: str, creation_path: str, **kwargs):
    from app.prompts import (
        build_candidate_write_messages,
        build_write_system,
    )

    msgs = build_candidate_write_messages(
        state=state,
        source_text=source_text,
        creation_path=creation_path,
        **kwargs,
    )
    return build_write_system(state), msgs[-1]["content"], msgs


async def real_candidates(db_path: Path, aid: int, creation_path: str) -> dict:
    """**一次**真实 MiMo 调用，产出三候选。"""
    from app.config import settings
    from app.prompts import CANDIDATE_ROLES
    from app.services import personalization, rewrite_candidates
    from app.db import get_conn

    with get_conn(db_path) as conn:
        state = personalization.resolve_state(conn)["state"]

    kwargs: dict = {"reference_author_id": aid, "use_reference_profile": True}
    if creation_path != "author_auto":
        kwargs["source_text"] = SOURCE_POST

    # 一次 generate_candidates == 一次 provider.chat()（要求 7）
    result = await rewrite_candidates.generate_candidates(
        creation_path=creation_path, **kwargs
    )
    restore_db_path(db_path)
    return {
        "state": state,
        "model": settings.model_rewrite,
        "creation_path": result["creation_path"],
        "candidates": result["candidates"],
        "context_empty": result["context_empty"],
        "prompt_version": result["prompt_version"],
        "roles": [{"key": k, "label": l} for k, l, _d in CANDIDATE_ROLES],
    }


async def real_single_draft(db_path: Path, aid: int, creation_path: str) -> dict:
    """**一次**真实 MiMo 调用，产出单稿（验证「单稿只输出正文」）。"""
    from app.config import settings
    from app.services import personalization, rewriter
    from app.db import get_conn

    with get_conn(db_path) as conn:
        state = personalization.resolve_state(conn)["state"]

    result = await rewriter.generate_draft(
        source_text=TOPIC_IDEA,
        reference_author_id=aid,
        use_reference_profile=True,
        creation_path=creation_path,
    )
    restore_db_path(db_path)
    return {
        "state": state,
        "model": settings.model_rewrite,
        "creation_path": result["creation_path"],
        "ai_draft": result["ai_draft"],
        "prompt_version": result["prompt_version"],
    }


def check_output_discipline(text: str) -> list[str]:
    """检查候选正文里有没有夹带说明 / 提醒 / 声明。"""
    banned = [
        "以下是", "供参考", "希望对你有帮助", "你可以再调整",
        "注：", "（注", "免责", "本内容由", "AI 生成", "模型生成",
        "仅供参考", "事实核查", "```",
    ]
    return [b for b in banned if b in text]


async def run(real: bool, sample: str) -> int:
    db_path = make_db()
    from app.config import settings, WRITE_PROMPT_VERSION
    from app.db import get_conn, init_db

    init_db(db_path)
    restore_db_path(db_path)
    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)

    print("=" * 92)
    print("04-C WRITE 升级验证（隔离库；生产库与 Desktop V1 只读）")
    print("=" * 92)
    print(f"[模型]        {settings.model_rewrite}")
    print(f"[Prompt]      WRITE_PROMPT_VERSION = {WRITE_PROMPT_VERSION}")
    print(f"[隔离库]      {db_path}")
    print(f"[模式]        {'真实模型调用' if real else 'dry（不调用模型）'}"
          f"  [样本] {sample}")

    with get_conn(db_path) as conn:
        aid = seed_reference_author(conn, AUTHOR_USERNAME)

    restore_db_path(db_path)

    # ================================================================
    # ① 五条路径的 prompt 形态
    # ================================================================
    print("\n" + "-" * 92)
    print("① 五条创作输入路径：prompt 输入块与任务句")
    print("-" * 92)

    from app.services.creation_path import CREATION_PATHS, CREATION_PATH_LABELS
    from app.services import personalization

    with get_conn(db_path) as conn:
        state = personalization.resolve_state(conn)["state"]

    shapes: dict[str, dict] = {}
    for path in CREATION_PATHS:
        source = "" if path == "author_auto" else SOURCE_POST
        system, user, _msgs = build_messages(
            state, source, path,
            reference_profile=None,
            strategy_context={"cross_high": [{"value": "直接陈述"}]},
        )
        has_source_block = "## 当前素材" in user
        has_topic_block = "## 本次主题" in user
        has_auto_block = "## 本次没有提供素材或主题" in user
        shapes[path] = {
            "label": CREATION_PATH_LABELS[path],
            "user_chars": len(user),
            "source_block": has_source_block,
            "topic_block": has_topic_block,
            "auto_topic_block": has_auto_block,
            "traffic_rules": "流量目标" in system,
            "restructure": "重构自由度" in system,
            "output_discipline": "输出纪律" in system,
        }
        print(
            f"  {CREATION_PATH_LABELS[path]:<12} "
            f"user={len(user):>5}字  "
            f"素材块={int(has_source_block)} 主题块={int(has_topic_block)} "
            f"自主选题块={int(has_auto_block)}  "
            f"流量={int('流量目标' in system)} 重构={int('重构自由度' in system)} "
            f"输出纪律={int('输出纪律' in system)}"
        )

    ok_shapes = all(
        v["traffic_rules"] and v["restructure"] and v["output_discipline"]
        for v in shapes.values()
    )
    print(f"\n  → 五条路径都带齐三段新规则：{ok_shapes}")

    # ================================================================
    # ② 真实三候选样本
    # ================================================================
    print("\n" + "-" * 92)
    print("② 真实中文 X 三候选样本（原帖 + 参考作者 路径）")
    print("-" * 92)

    if not real:
        system, user, _ = build_messages(
            state, SOURCE_POST, "rewrite_author",
            strategy_context={"cross_high": [{"value": "直接陈述"}]},
        )
        print(f"  [dry] system = {len(system)} 字 / user = {len(user)} 字")
        print("  [dry] 加 --real 才会调用一次真实模型")
        print("\nOK（dry 模式，未调用模型）")
        return 0

    if not settings.ai_api_key:
        print("  未配置 AI_API_KEY，无法产出真实样本（backend/.env）")
        return 2

    payload: dict = {
        "model": settings.model_rewrite,
        "path_shapes": shapes,
        "note": (
            "参考作者（老雷）的分析与表现数据是脚本构造的夹具，"
            "不是用户真实数据；模型调用与输出是真实的。"
        ),
    }

    if sample == "single_topic":
        single = await real_single_draft(db_path, aid, "topic_author")
        restore_db_path(db_path)
        print(f"  [状态] {single['state']}　[路径] {single['creation_path']}"
              f"　[模型] {single['model']}　[prompt] {single['prompt_version']}")
        print(f"  [主题] {TOPIC_IDEA}")
        print()
        print("──── 单稿正文（%d 字）────" % len(single["ai_draft"]))
        print(single["ai_draft"])
        print()
        bad = check_output_discipline(single["ai_draft"])
        print(f"  只输出正文：{not bad}" + (f"　⚠ 夹带：{bad}" if bad else ""))
        payload.update(
            {
                "single_draft": {
                    "creation_path": single["creation_path"],
                    "state": single["state"],
                    "topic": TOPIC_IDEA,
                    "ai_draft": single["ai_draft"],
                    "chars": len(single["ai_draft"]),
                    "violations": bad,
                }
            }
        )
        payload.update({"prompt_version": single["prompt_version"]})
    else:
        path = "author_auto" if sample == "auto_topic" else "rewrite_author"
        result = await real_candidates(db_path, aid, path)
        restore_db_path(db_path)

        print(f"  [状态] {result['state']}　[路径] {result['creation_path']}"
              f"　[模型] {result['model']}　[prompt] {result['prompt_version']}")
        print(f"  [输入] {'（无素材无主题，由 AI 自主选题）' if path == 'author_auto' else '原帖'}")
        print(f"  [角色] {', '.join(f'{r['key']}·{r['label']}' for r in result['roles'])}")
        print()

        violations: dict[str, list[str]] = {}
        for c in result["candidates"]:
            print(f"──── {c['key']} · {c['label']}（{len(c['text'])} 字）────")
            print(c["text"])
            print()
            bad = check_output_discipline(c["text"])
            if bad:
                violations[c["key"]] = bad

        texts = [c["text"] for c in result["candidates"]]
        print(f"  三候选互不相同：{len(set(texts)) == 3}")
        print(f"  均非空：{all(bool(t.strip()) for t in texts)}")
        if violations:
            print(f"  ⚠ 夹带额外文字：{violations}")
        else:
            print("  输出纪律：三个候选都只有正文，无说明 / 提醒 / 声明")

        payload.update(
            {
                "prompt_version": result["prompt_version"],
                "personalization_state": result["state"],
                "creation_path": result["creation_path"],
                "context_empty": result["context_empty"],
                "roles": result["roles"],
                "source_post": None if path == "author_auto" else SOURCE_POST,
                "candidates": result["candidates"],
                "output_discipline_violations": violations,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"WRITE-v3-真实样本-{sample}.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  证据已写入：{out}")

    # 把三段真实样本合并成一份总证据（便于交付时一次看完）
    merged = OUT_DIR / "WRITE-v3-真实样本汇总.json"
    bundle: dict = {}
    if merged.exists():
        bundle = json.loads(merged.read_text(encoding="utf-8"))
    bundle[sample] = payload
    bundle.setdefault("path_shapes", shapes)
    bundle.setdefault("model", settings.model_rewrite)
    merged.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  汇总已更新：{merged}")
    print("\nOK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="04-C WRITE 升级验证")
    parser.add_argument(
        "--real", action="store_true", help="调用真实模型产出样本"
    )
    parser.add_argument(
        "--sample",
        default="rewrite_author",
        choices=["rewrite_author", "auto_topic", "single_topic"],
        help="要产出的真实样本（每种恰好 1 次模型调用）",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.real, args.sample))


if __name__ == "__main__":
    raise SystemExit(main())
