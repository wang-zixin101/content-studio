"""04-B STYLE 小样本**真实**验证：生产实际路径 + 合同校验 + 一次纠正重试。

严格按《04-B STYLE 正式设计 v1》§6 与验收反馈第 2 条执行：
  - 隔离数据库，生产 / Desktop 只读；
  - MiMo 推理模式，不设输出 Token 上限；
  - **不使用 FakeProvider 冒充成功** —— 走生产实际的
    `recompute_style_profile()`：真实模型调用 → 合同校验 →
    失败则一次纠正重试 → 成功才落库；
  - 成功：交付真实模型的有效落库档案 + 单稿 / 三候选完整风格上下文；
  - 失败：保留错误并明确报告，不伪造成功。

用法：
    ./backend/.venv/Scripts/python.exe validate_style_v3.py
"""

from __future__ import annotations

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

#: 5 条**单主题**人工原作（验收场景 ①）。
#: 刻意让"其实"这个口语填充词与"先…再…"的推进方式重复出现 ——
#: 这样模型才有真实可归纳的规律，并给出逐字引文。
SAMPLES = [
    "做副业第一步：先把能力盘点清楚。其实你需要知道自己的核心优势在哪里。"
    "然后找一个能交换的项目。不要追风口，风口会过去。",

    "做副业第二步：其实不要急着找项目。先看看自己有什么可交换的能力。"
    "你会发现能力比项目重要得多。慢慢来，比较快。",

    "做副业第三步：其实最难的不是找项目，是承认自己暂时没有拿得出手的能力。"
    "先把能力练到能交换的程度，项目会自己找上门。",

    "做副业第四步：其实很多人失败，是因为把顺序搞反了。先追风口，再补能力。"
    "正确的顺序是先补能力，再等风口。",

    "做副业第五步：其实副业的本质就是交换。你拿能力换别人的钱。"
    "所以与其研究项目，不如研究自己到底能拿出什么。",
]


def make_db() -> Path:
    path = Path(tempfile.mkdtemp(prefix="cs_style_v3_")) / "style_v3.db"
    os.environ["DB_PATH"] = str(path)
    import app.config as cm

    cm.settings.db_path = path
    return path


def restore_db_path(path: Path) -> None:
    """把 settings.db_path 恢复成隔离库路径。

    关键：导入 `tests.test_regression` 会在**模块级**把 settings.db_path
    指向它自己的测试库（一个空库）。若不恢复，后续 `get_conn` 会连到空库
    并报 "no such table: contents" —— 这也是上一版脚本场景 ② 失败的根因。
    """
    import app.config as cm

    cm.settings.db_path = path
    os.environ["DB_PATH"] = str(path)


def _v3_analysis(text: str, topic: str = "副业") -> dict:
    """一条能通过 ANALYSIS v3.1 合同的最小分析结果。"""
    has_qishi = "其实" in text
    return {
        "topic": topic,
        "subtopic": "方法论步骤",
        "content_type": "经验分享",
        "core_message": text[:60],
        "tags": ["副业", "能力交换"],
        "keywords": ["副业", "能力", "项目", "风口"],
        "content": {
            "claims": [
                {
                    "kind": "作者判断",
                    "text": "作者认为副业的本质是能力交换。",
                    "evidence": text[:8],
                }
            ],
            "must_preserve": ["副业的本质是能力交换，不是追风口"],
            "context_limits": [],
        },
        "writing": {
            "opening_type": "直接陈述",
            "structure_type": "观点—理由",
            "ending_type": "观点收束",
            "moves": [
                {
                    "type": "递进",
                    "evidence": text[:10],
                    "mechanism": "用步骤编号推进方法论。",
                }
            ],
        },
        "style": {
            "tone": "中性",
            "register": "口语",
            "directness": "直接",
            "certainty": "明确肯定",
            "rhythm": "长短交替",
            "markers": [
                {"type": "口语词", "evidence": "其实" if has_qishi else text[:4]},
            ],
            "distinctive_phrases": (
                ["先把能力盘点清楚"] if "先把能力盘点清楚" in text else []
            ),
        },
        "reuse": {"patterns": [], "boundaries": ["依赖个人能力盘点，换话题需重做"]},
    }


def seed_analyzed_mine(conn, texts: list[str]) -> list[int]:
    """创建**已分析**的 mine 样本（style_eligible=True + human_original）。

    必须同时满足 `analysis_state='analyzed'` 与 `style_eligible=1`，
    否则 `mine_contents_for_style()` 返回 0 行（验收反馈第 1 条）。
    """
    from app import repository as repo

    ids: list[int] = []
    for i, text in enumerate(texts):
        cid = repo.create_content(conn, "mine", text, style_eligible=True)
        repo.set_provenance(conn, cid, "human_original")
        analysis = _v3_analysis(text, topic=f"副业起步{i % 2}")
        repo.save_analysis(conn, cid, analysis, "seed", raw=analysis)
        repo.set_analysis_status(conn, cid, "analyzed")
        ids.append(cid)
    return ids


async def run() -> int:
    db_path = make_db()
    from app.config import settings, STYLE_PROMPT_VERSION
    from app.db import get_conn, init_db
    from app import repository as repo

    init_db(db_path)
    restore_db_path(db_path)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    from app.services.style_profile import (
        MIN_CONTENT,
        MAX_SAMPLES,
        aggregate_stats,
        _pick_samples,
        recompute_style_profile,
        usable_style_profile,
        profile_staleness,
    )
    from app.services.style_contract import StyleContractError

    print("=" * 92)
    print("04-B STYLE v3 真实模型验证（生产实际路径，不使用 FakeProvider）")
    print("=" * 92)
    print(f"[模型] {settings.model_style}")
    print(f"[Prompt] STYLE_PROMPT_VERSION = {STYLE_PROMPT_VERSION}")
    print(f"[门槛] MIN_CONTENT = {MIN_CONTENT}　[MAX_SAMPLES] {MAX_SAMPLES}")
    print(f"[隔离库] {db_path}")

    # ================================================================
    # ① 造数据：5 条单主题人工原作（必须全部进入模型）
    # ================================================================
    print("\n" + "-" * 92)
    print("① 造数据：5 条单主题人工原作")
    print("-" * 92)

    with get_conn(db_path) as conn:
        ids = seed_analyzed_mine(conn, SAMPLES)
        rows = repo.mine_contents_for_style(conn)
        stats = aggregate_stats(rows)
        picked = _pick_samples(rows)
        prov = repo.mine_sample_stats(conn)

    print(f"  有效样本（eligible+analyzed）: {len(rows)}")
    print(f"  实际抽样（text_sample_count）: {len(picked)}")
    print(f"  来源统计: {prov}")
    assert len(rows) == 5, f"应有 5 条有效样本，实际 {len(rows)}"
    assert len(picked) == 5, f"5 条单主题应全部抽样，实际 {len(picked)}"
    print("  ✓ 5/5 进入模型（单主题不再被无故丢弃）")

    input_evidence = {
        "content_ids": ids,
        "sample_count": len(rows),
        "text_sample_count": len(picked),
        "input_stats": stats,
        "provenance_stats": prov,
        "input_samples": picked,
    }

    # ================================================================
    # ② 真实建档：recompute_style_profile（含一次纠正重试）
    # ================================================================
    print("\n" + "-" * 92)
    print("② 真实建档：recompute_style_profile（真实模型 + 合同校验 + 纠正重试）")
    print("-" * 92)

    profile = None
    failure: dict | None = None
    try:
        with get_conn(db_path) as conn:
            profile = await recompute_style_profile(conn)
        print(f"  ✓ 成功落库：version={profile['version']} "
              f"based_on={profile['based_on_count']} "
              f"prompt_version={profile.get('prompt_version')}")
    except StyleContractError as err:
        failure = {"type": "StyleContractError", "path": err.path, "message": str(err)}
        print(f"  ✗ 合同校验未通过（真实模型输出被拒收）：{err.path}: {err}")
    except Exception as err:  # noqa: BLE001
        failure = {"type": type(err).__name__, "message": str(err)[:500]}
        print(f"  ✗ 建档失败：{type(err).__name__}: {str(err)[:300]}")

    if failure is not None:
        return _report_failure(input_evidence, failure, db_path)

    # ================================================================
    # ③ 用真实档案做 WRITE 上下文（单稿 + 三候选）
    # ================================================================
    print("\n" + "-" * 92)
    print("③ WRITE 单稿与三候选的完整风格上下文（使用真实落库档案）")
    print("-" * 92)

    from app.prompts import (
        _style_write_context,
        build_write_messages,
        build_candidate_write_messages,
    )

    with get_conn(db_path) as conn:
        real = usable_style_profile(conn)
    assert real is not None, "刚落库的档案应当可用"

    profile_json = real["profile_json"]
    summary_text = real.get("summary_text") or ""
    context_block = _style_write_context(profile_json, summary_text)
    print(f"  投影长度: {len(context_block)} 字")

    with get_conn(db_path) as conn:
        staleness = profile_staleness(conn)
    print(f"  新鲜度: usable={staleness['usable']} "
          f"style_eligible={staleness['style_eligible_count']} "
          f"should_recompute={staleness['should_recompute']}")

    source_analysis = _v3_analysis(SAMPLES[0])
    single = build_write_messages(
        state="PERSONALIZED",
        source_text=SAMPLES[0],
        style_profile=profile_json,
        style_summary=summary_text,
        source_analysis=source_analysis,
    )
    candidates = build_candidate_write_messages(
        state="PERSONALIZED",
        source_text=SAMPLES[0],
        style_profile=profile_json,
        style_summary=summary_text,
        source_analysis=source_analysis,
    )

    for label, msgs in (("单稿", single), ("三候选", candidates)):
        body = msgs[1]["content"]
        print(f"  [{label}] user 内容 {len(body)} 字；"
              f"含风格块={'## 全局风格档案' in body}；"
              f"含证据构成={'证据构成' in body}；"
              f"含置信度={'档案置信度' in body}")

    return _report_success(
        input_evidence=input_evidence,
        profile=profile,
        real=real,
        context_block=context_block,
        single=single,
        candidates=candidates,
        staleness=staleness,
        db_path=db_path,
    )


def _write(out: dict) -> None:
    out_dir = Path(__file__).resolve().parent / "task04b_deliverables"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / "STYLE-v3-真实模型验证.json"
    target.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n[交付物] {target}")


def _report_success(*, input_evidence, profile, real, context_block,
                    single, candidates, staleness, db_path) -> int:
    _write({
        "result": "SUCCESS",
        "basis": "真实 MiMo 调用 + 生产实际合同校验路径（未使用 FakeProvider）",
        "db_path": str(db_path),
        "input": input_evidence,
        "profile": {
            "version": profile["version"],
            "based_on_count": profile["based_on_count"],
            "prompt_version": profile.get("prompt_version"),
            "profile_json": profile["profile_json"],
            "summary_text": profile.get("summary_text"),
        },
        "usable_profile_from_db": {
            "version": real["version"],
            "sample_ids": real["sample_ids"],
        },
        "staleness": staleness,
        "style_context": context_block,
        "write_single_draft": {
            "messages": len(single),
            "user_content": single[1]["content"],
        },
        "write_candidates": {
            "messages": len(candidates),
            "user_content": candidates[1]["content"],
        },
    })
    print("\n结论：真实模型档案已落库，单稿与三候选均收到完整风格上下文。")
    return 0


def _report_failure(input_evidence: dict, failure: dict, db_path: Path) -> int:
    _write({
        "result": "FAILURE",
        "basis": ("真实 MiMo 调用后合同校验未通过 / 建档失败；"
                  "**未**用模拟数据冒充成功"),
        "db_path": str(db_path),
        "input": input_evidence,
        "failure": failure,
        "profile": None,
        "style_context": None,
        "write_single_draft": None,
        "write_candidates": None,
    })
    print("\n结论：真实模型验证**失败**，已保留错误；未生成任何模拟档案或上下文。")
    return 1


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
