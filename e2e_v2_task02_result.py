"""V2 任务 02 端到端：把一份真实导出的任务文件「假装成外部模型」回写成结果文件。

0 AI：ANALYSIS V2 对象是**本地拼出来的**，不调用任何模型。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import EXTERNAL_ANALYSIS_FORMAT_VERSION
from app.services.analysis_spec import ANALYSIS_SCHEMA_HASH

TASK_FILE = sys.argv[1]
OUT_FILE = sys.argv[2]
TAKE = int(sys.argv[3]) if len(sys.argv) > 3 else 300


def v2(i: int) -> dict:
    """一份字段齐全的 ANALYSIS V2（与本仓库 spec 完全一致）。"""
    return {
        "topic": "AI 工具",
        "subtopic": "Agent 工作流",
        "content_type": "观点",
        "core_viewpoint": f"核心观点 {i}",
        "structure": "问题—分析—结论",
        "opening_type": "提问",
        "ending_type": "行动号召",
        "tone": "克制",
        "emotion": "平静",
        "sentence_style": "短句为主",
        "length_chars": 120,
        "tags": ["AI", "效率"],
        "keywords": ["Agent", "自动化"],
        "content_invariants": {
            "named_entities": ["X"], "numbers_and_quantities": ["3 倍"],
            "factual_claims": ["已上线"], "opinions_or_judgments": ["值得做"],
            "causal_relations": ["因为所以"], "uncertainty_or_attribution": ["据称"],
            "must_preserve": ["原文数据"],
        },
        "rhetorical": {
            "hook_mechanism": "反差", "moves": ["设问"], "evidence_modes": ["案例"],
            "closing_function": "总结", "information_density": "高",
        },
        "stance_engagement": {
            "certainty": "中", "directness": "直接", "subjectivity": "中",
            "disagreement_intensity": "低", "hedge_markers": ["可能"],
            "booster_markers": ["确实"], "attitude_markers": ["难得"],
            "self_mention": "低", "reader_address": "你", "engagement_devices": ["提问"],
        },
        "microstyle": {
            "sentence_rhythm": "短", "fragments_level": "低", "sentence_length_pattern": "短句",
            "discourse_markers": ["但是"], "conjunction_habits": ["于是"],
            "punctuation_habits": ["省略号"], "line_break_habit": "多换行",
            "parenthetical_habit": "少", "quote_habit": "少", "enumeration_habit": "用数字",
            "numeral_habit": "阿拉伯数字", "emoji_habit": "少", "repetition_habit": "无",
            "distinctive_phrases": ["说白了"], "register": "口语",
        },
        "style_boundary": {
            "likely_voice_markers": ["短句"], "platform_or_genre_markers": ["X"],
            "topic_bound_markers": ["AI"], "uncertain_markers": ["可能"],
        },
    }


lines = Path(TASK_FILE).read_text(encoding="utf-8").strip().split("\n")
tasks = [json.loads(x) for x in lines[1:] if x.strip()][:TAKE]

meta = {
    "format": "content-studio-analysis-result",
    "format_version": EXTERNAL_ANALYSIS_FORMAT_VERSION,
    "analysis_schema_version": "analysis-v2",
    "schema_hash": ANALYSIS_SCHEMA_HASH,
    "prompt_version": "v2",
    "model": "e2e-local-stub",
    "count": len(tasks),
}
out = [json.dumps({"_meta": meta}, ensure_ascii=False)]
for idx, t in enumerate(tasks):
    out.append(
        json.dumps(
            {
                "content_id": t["content_id"],
                "content_hash": t["content_hash"],
                "analysis": v2(idx),
            },
            ensure_ascii=False,
        )
    )
Path(OUT_FILE).write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"结果文件：{OUT_FILE} · {len(tasks)} 条 · "
      f"{Path(OUT_FILE).stat().st_size / 1024 / 1024:.2f} MB")
print(f"首个 content_id = {tasks[0]['content_id']}")
