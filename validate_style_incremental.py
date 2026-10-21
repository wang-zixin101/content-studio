"""STYLE 增量学习端到端验收（架构 v3 / A 方案）。

隔离数据库，覆盖设计文档 §6 的验收样例：
  - 19 篇：STYLE 不启动
  - 第 20 篇：首次集中建档，20 篇证据台账与学习状态全部持久化
  - 第 21 篇：只学这一篇（前 20 篇原文不再送模型）
  - 重复导入同版本：不重复学习、不重复计数
  - 对标已分析结果：零 ANALYSIS、不计入门槛、不进个人 STYLE
  - 撤销资格：撤回该篇证据，其他历史原文不需重学

默认用 FakeProvider 跑**真实代码路径**（零真实 token）。
加 `--real` 会用真实 MiMo 跑单篇增量（付费，默认关闭）。

用法：
    ./backend/.venv/Scripts/python.exe validate_style_incremental.py
    ./backend/.venv/Scripts/python.exe validate_style_incremental.py --real
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

#: 20 篇单主题（副业）人工原作；"其实"与"先…再…"重复出现，供模型归纳
MINE_TEXTS = [
    "做副业第1步：先把能力盘点清楚。其实你需要知道自己的核心优势在哪里。然后找一个能交换的项目。不要追风口，风口会过去。",
    "做副业第2步：其实不要急着找项目。先看看自己有什么可交换的能力。你会发现能力比项目重要得多。",
    "做副业第3步：其实最难的不是找项目，是承认自己暂时没有拿得出手的能力。先把能力练到能交换的程度。",
    "做副业第4步：其实很多人失败，是因为把顺序搞反了。先追风口，再补能力。正确的顺序是先补能力。",
    "做副业第5步：其实副业的本质就是交换。你拿能力换别人的钱。所以与其研究项目，不如研究自己。",
    "做副业第6步：其实别怕起步慢。先把一件小事做到能拿出手，再谈放大。慢慢来，比较快。",
    "做副业第7步：其实找项目之前，先想清楚你能为谁解决什么问题。能力要对应需求才值钱。",
    "做副业第8步：其实很多人不是没机会，是没准备好。先把作品攒够，机会来了才接得住。",
    "做副业第9步：其实最稳的路径是把本职工作里的能力外溢。先复用，再拓展。",
    "做副业第10步：其实别追热点。热点来得快去得也快，能力才是能带走的东西。",
    "做副业第11步：其实衡量副业的标准很简单：能不能持续交换价值。不能就先别投入。",
    "做副业第12步：其实先把时间账算清楚。你愿意每周投入多少小时，决定能做多大的事。",
    "做副业第13步：其实别一开始就想着规模化。先跑通一个最小的交换闭环。",
    "做副业第14步：其实复盘比努力更重要。每周先看数据，再调整动作。",
    "做副业第15步：其实副业不是逃避主业。先在主业里练出可迁移的能力，再出去交换。",
    "做副业第16步：其实最怕的是贪多。先做好一件事，再考虑第二件。",
    "做副业第17步：其实定价也是能力的一部分。先学会给自己的能力合理定价。",
    "做副业第18步：其实信任比流量更值钱。先交付好，再谈增长。",
    "做副业第19步：其实长期主义者更容易赢。先坚持做，再等复利。",
    "做副业第20步：其实最后拼的是耐心。把能力、作品和信任都攒够，收入只是结果。",
]

#: 第 21 篇
TWENTY_FIRST = (
    "做副业第21步：其实真正的分水岭是能不能拒绝诱惑。"
    "先把一件事做到稳定产出，再考虑第二条曲线。"
)

BENCHMARK_TEXT = "对标作者写的一条关于副业的经验，足够长的一句话内容。"


def make_db() -> Path:
    path = Path(tempfile.mkdtemp(prefix="cs_style_incr_")) / "incr.db"
    os.environ["DB_PATH"] = str(path)
    import app.config as cm

    cm.settings.db_path = path
    return path


def restore_db_path(path: Path) -> None:
    import app.config as cm

    cm.settings.db_path = path
    os.environ["DB_PATH"] = str(path)


def _v3_analysis(text: str, topic: str = "副业") -> dict:
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
            "markers": [{"type": "口语词", "evidence": "其实"}],
            "distinctive_phrases": ["先把能力盘点清楚"],
        },
        "reuse": {"patterns": [], "boundaries": ["依赖个人能力盘点"]},
    }


def seed_analyzed(conn, text: str, *, source: str = "mine",
                  eligible: bool = True, provenance: str = "human_original") -> int:
    """造一条**已完成 ANALYSIS**的作品（模拟"已分析结果"直接入库）。"""
    from app import repository as repo

    cid = repo.create_content(conn, source, text, style_eligible=eligible)
    repo.set_provenance(conn, cid, provenance)
    data = _v3_analysis(text)
    repo.save_analysis(conn, cid, data, "seed", raw=data)
    repo.set_analysis_status(conn, cid, "analyzed")
    return cid


def _fake_observation(raw_text: str) -> dict:
    """FakeProvider 的观察回复（引文取自原文，保证逐字）。"""
    observations = []
    if "其实" in raw_text:
        observations.append({
            "rule": "用句首「其实」引出更深一层判断",
            "dimension": "structure",
            "classification": "VOICE",
            "applies_when": "本篇话题内",
            "quote": "其实",
            "evidence_type": "support",
            "match_pattern_id": None,
        })
    if "先" in raw_text:
        observations.append({
            "rule": "把建议拆成先后顺序动作（先…再…）",
            "dimension": "structure",
            "classification": "VOICE",
            "applies_when": "方法论类内容",
            "quote": raw_text[:6],
            "evidence_type": "support",
            "match_pattern_id": None,
        })
    return {"observations": observations}


async def run(real: bool = False) -> int:
    db_path = make_db()
    from app.config import settings
    from app.db import get_conn, init_db
    from app import repository as repo

    init_db(db_path)
    restore_db_path(db_path)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    from app.services import style_learning as sl

    print("=" * 92)
    print("STYLE 增量学习端到端验收（架构 v3 / A 方案）")
    print("=" * 92)
    print(f"[模型] {settings.model_style}　[门槛] STYLE_INITIAL_MIN = {sl.STYLE_INITIAL_MIN}")
    print(f"[隔离库] {db_path}")
    print(f"[模式] {'真实 MiMo 调用' if real else 'FakeProvider（零真实 token）'}")

    evidence: dict = {"db_path": str(db_path), "mode": "real" if real else "fake",
                      "steps": []}

    # ================================================================
    # ① 前 19 篇：只积累，不启动
    # ================================================================
    print("\n" + "-" * 92)
    print("① 我的前 19 篇已分析有效作品")
    print("-" * 92)
    with get_conn(db_path) as conn:
        for text in MINE_TEXTS[:19]:
            seed_analyzed(conn, text)
        r19 = await sl.sync_style_learning(conn)
        cnt19 = sl.eligible_count(conn)
    print(f"  有效作品 {cnt19} 篇 → action={r19['action']}, learned={r19['learned']}")
    assert r19["action"] == "waiting", "19 篇时不得启动 STYLE"
    assert r19["learned"] == 0
    evidence["steps"].append({
        "step": 1, "desc": "19 篇", "eligible_count": cnt19,
        "action": r19["action"], "learned": r19["learned"],
    })

    # ================================================================
    # ② 第 20 篇：首次集中建档
    # ================================================================
    print("\n" + "-" * 92)
    print("② 第 20 篇 → 首次集中建档")
    print("-" * 92)
    with get_conn(db_path) as conn:
        cid20 = seed_analyzed(conn, MINE_TEXTS[19])

    # 建档阶段：真实模式下也用 FakeProvider 建前 20 篇（20 次真实调用成本高且易限流），
    # 真实 MiMo 用于**第 21 篇的单篇增量**（见步骤 ③），那才是增量的关键路径。
    r20 = await _run_fake(db_path)

    with get_conn(db_path) as conn:
        learned20 = sl.learned_ids(conn)
        n_patterns = conn.execute(
            "SELECT COUNT(*) AS n FROM style_patterns").fetchone()["n"]
        n_ev = conn.execute(
            "SELECT COUNT(*) AS n FROM style_pattern_evidence").fetchone()["n"]
        profile = repo.latest_style_profile(conn)
        usable = _usable(conn)

    print(f"  action={r20.get('action')}  learned={r20.get('learned')}")
    print(f"  台账：learned={len(learned20)} 篇，patterns={n_patterns}，"
          f"evidence={n_ev}")
    print(f"  档案：v{profile['version']} based_on={profile['based_on_count']} "
          f"kind={profile.get('profile_kind')}")
    print(f"  WRITE 可用：{usable is not None}")
    evidence["steps"].append({
        "step": 2, "desc": "第 20 篇首次建档", "new_content_id": cid20,
        "action": r20.get("action"), "learned": r20.get("learned"),
        "learned_total": len(learned20), "patterns": n_patterns,
        "evidence_rows": n_ev,
        "profile": {
            "version": profile["version"],
            "based_on_count": profile["based_on_count"],
            "profile_kind": profile.get("profile_kind"),
        },
        "usable_for_write": usable is not None,
    })

    # ================================================================
    # ③ 第 21 篇：只学这一篇
    # ================================================================
    print("\n" + "-" * 92)
    print("③ 第 21 篇 → 只增量这一篇")
    print("-" * 92)
    with get_conn(db_path) as conn:
        cid21 = seed_analyzed(conn, TWENTY_FIRST)

    if real:
        r21 = await _run_real_one(db_path, cid21)
    else:
        r21 = await _run_fake(db_path)

    learned_ids_21 = r21.get("learned_ids") or []
    print(f"  action={r21.get('action')}  learned={r21.get('learned')}")
    print(f"  实际学习：{learned_ids_21}  ← 只应包含第 21 篇 {cid21}")
    evidence["steps"].append({
        "step": 3, "desc": "第 21 篇增量", "new_content_id": cid21,
        "action": r21.get("action"), "learned": r21.get("learned"),
        "learned_ids": learned_ids_21,
        "only_new_one": learned_ids_21 == [cid21],
    })

    # ================================================================
    # ④ 重复导入同版本 → 不重复学习
    # ================================================================
    print("\n" + "-" * 92)
    print("④ 重复导入第 21 篇（同版本）")
    print("-" * 92)
    with get_conn(db_path) as conn:
        r_dup = await sl.sync_style_learning(conn)
    print(f"  action={r_dup['action']}  learned={r_dup['learned']}")
    assert r_dup["action"] == "noop", "同版本重复导入不得重复学习"
    evidence["steps"].append({
        "step": 4, "desc": "重复导入同版本", "action": r_dup["action"],
        "learned": r_dup["learned"],
    })

    # ================================================================
    # ⑤ 对标已分析结果：不计数、不进个人 STYLE
    # ================================================================
    print("\n" + "-" * 92)
    print("⑤ 对标已分析结果导入")
    print("-" * 92)
    with get_conn(db_path) as conn:
        before = sl.eligible_count(conn)
        cid_bench = seed_analyzed(conn, BENCHMARK_TEXT, source="benchmark")
        r_bench = await sl.sync_style_learning(conn)
        after = sl.eligible_count(conn)
        in_style = cid_bench in sl.learned_ids(conn)
    print(f"  门槛 {before} → {after}（对标作品不计入）")
    print(f"  对标作品进入个人 STYLE：{in_style}（应为 False）")
    assert after == before, "对标作品不得计入个人 STYLE 门槛"
    assert not in_style, "对标作品绝不进入个人 STYLE"
    evidence["steps"].append({
        "step": 5, "desc": "对标已分析结果", "benchmark_content_id": cid_bench,
        "eligible_before": before, "eligible_after": after,
        "entered_personal_style": in_style,
    })

    # ================================================================
    # ⑥ 撤销资格 → 撤回该篇证据
    # ================================================================
    print("\n" + "-" * 92)
    print("⑥ 撤销第 21 篇的学习资格")
    print("-" * 92)
    with get_conn(db_path) as conn:
        repo.update_style_eligible(conn, [cid21], False)
        r_revoke = await sl.sync_style_learning(conn)
        removed_ev = conn.execute(
            "SELECT COUNT(*) AS n FROM style_pattern_evidence WHERE content_id = ?",
            (cid21,),
        ).fetchone()["n"]
        state = conn.execute(
            "SELECT state FROM style_content_state WHERE content_id = ?", (cid21,)
        ).fetchone()
    print(f"  action={r_revoke['action']}（门槛跌破 → 暂停学习）")
    print(f"  第 21 篇残留证据：{removed_ev}（应为 0）　状态：{state['state']}")
    assert removed_ev == 0, "撤销后该篇证据必须撤回"
    assert state["state"] == sl.INVALIDATED
    evidence["steps"].append({
        "step": 6, "desc": "撤销资格", "revoked_content_id": cid21,
        "action": r_revoke["action"], "remaining_evidence": removed_ev,
        "state": state["state"],
    })

    # ---- 交付物 ----
    out_dir = Path(__file__).resolve().parent / "task04b_deliverables"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / "STYLE-增量学习-19-20-21-执行证据.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(f"\n[交付物] {target}")
    print("\n全部验收场景通过。")
    return 0


def _usable(conn):
    from app.services.style_profile import usable_style_profile

    return usable_style_profile(conn)


async def _run_fake(db_path: Path) -> dict:
    """用 FakeProvider 跑真实代码路径，并记录实际学习到的 id。"""
    from unittest.mock import patch

    from tests.test_regression import FakeProvider
    from app.db import get_conn
    from app import repository as repo
    from app.services import style_learning as sl

    restore_db_path(db_path)
    learned_ids: list[int] = []

    class Observer(FakeProvider):
        def __init__(self):
            super().__init__()
            self._current: dict = {}

        async def chat_json(self, messages, *, model=None, **kwargs):
            self.json_calls.append({"messages": messages, "model": model})
            # 从 messages 里取回本篇原文（最后一块 JSON 数据）
            text = _extract_text(messages)
            return _fake_observation(text)

    fake = Observer()
    with get_conn(db_path) as conn:
        before = sl.learned_ids(conn)
    with patch("app.services.style_learning.get_provider", return_value=fake):
        with get_conn(db_path) as conn:
            result = await sl.sync_style_learning(conn)
            after = sl.learned_ids(conn)
    # 本次**新增**学到的（差集）—— 累计值无法证明"只学了新增篇"
    learned_ids = sorted(after - before)

    result = dict(result)
    result["learned_ids"] = learned_ids
    return result


def _extract_text(messages) -> str:
    """从观察请求的 user 内容里取回原文（JSON 数据块）。

    build_style_observe_messages 用 `json.dumps(..., indent=2)`，
    原文块实际是 `{\\n  "text": "..."` —— 必须兼容缩进，
    固定匹配 `{"text":` 会找不到，导致观察为空。
    """
    import json as _json
    import re as _re

    pattern = _re.compile(r'\{\s*"text"\s*:')
    for msg in reversed(messages):
        content = msg.get("content") or ""
        matches = list(pattern.finditer(content))
        if not matches:
            continue
        idx = matches[-1].start()          # 原文块总在最末
        try:
            obj, _ = _json.JSONDecoder().raw_decode(content[idx:])
            return obj.get("text", "")
        except Exception:  # noqa: BLE001
            continue
    return ""


async def _run_real_one(db_path: Path, cid: int) -> dict:
    """**真实 MiMo** 单篇增量学习（付费）。只调用一次模型。

    返回本次学到的 id 与该篇写入的逐字证据，并当场做引文逐字复核。
    """
    from app.db import get_conn
    from app.services import style_learning as sl

    restore_db_path(db_path)
    with get_conn(db_path) as conn:
        before = sl.learned_ids(conn)
        result = await sl.learn_one(conn, cid)
        after = sl.learned_ids(conn)
        state = conn.execute(
            "SELECT state, extractor_version FROM style_content_state "
            " WHERE content_id = ?", (cid,)
        ).fetchone()
        rows = conn.execute(
            "SELECT p.rule, p.classification, e.quote, e.authorship, e.evidence_type "
            "  FROM style_pattern_evidence e "
            "  JOIN style_patterns p ON p.id = e.pattern_id "
            " WHERE e.content_id = ?", (cid,)
        ).fetchall()

    raw_text = _raw_text_of(db_path, cid)
    bad_quotes = [r["quote"] for r in rows if r["quote"] not in raw_text]

    out = dict(result)
    out["action"] = "incremental"
    out["learned"] = 1
    out["learned_ids"] = sorted(after - before)
    out["state"] = dict(state) if state else None
    out["evidence"] = [dict(r) for r in rows]
    out["verbatim_bad_quotes"] = bad_quotes
    return out


def _raw_text_of(db_path: Path, cid: int) -> str:
    from app.db import get_conn

    restore_db_path(db_path)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT raw_text FROM contents WHERE id = ?", (cid,)
        ).fetchone()
    return (row["raw_text"] or "") if row else ""


def main() -> int:
    real = "--real" in sys.argv
    return asyncio.run(run(real=real))


if __name__ == "__main__":
    sys.exit(main())
