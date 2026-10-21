"""04-A 定点修正：小样本真实验证（v3 → v3.1 前后对照）。

严格按《04-A 定点修正与验收 v3.1》§7.4 执行：
  - 只用**指定 ID**（3、5、11、13、16、18、1），
    **禁止**用宽泛 SQL 条件误跑语料池 —— 本轮只分析这 7 条；
  - 隔离数据库：把已有验收库复制一份，在副本上跑，
    源库只读（旧输出必须保持原样，否则无法做前后对照）；
  - 保留 finish_reason 等日志与完整输出，供人工 / ChatGPT 审查；
  - 不调用付费 Batch。

用法：
    ./backend/.venv/Scripts/python.exe validate_v31.py <source_db> [--ids 3,5,11,13,16,18,1]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 文档 §7.4 指定的验证样本
DEFAULT_IDS = (3, 5, 11, 13, 16, 18, 1)

#: 文档 §3 已证实的问题 → 用于逐项判定是否修复
KNOWN_ISSUES: dict[int, list[dict]] = {
    1: [
        {"key": "must_preserve 把「三个感叹号」列为必须逐字保留",
         "check": "no_punct_in_must_preserve"},
    ],
    3: [
        {"key": "「自己想办法」被标 号召互动", "check": "no_interaction_for_advice"},
        {"key": "无数字列表被标 数字强调", "check": "no_digit_marker_without_digits"},
        {"key": "claims[].text 以悬空「（」结尾", "check": "claims_text_complete"},
    ],
    5: [
        {"key": "「我是建议大家玩X都别碰」被标 号召互动",
         "check": "no_interaction_for_advice"},
    ],
    11: [
        {"key": "「Dots 入口：<链接>」被标 号召互动",
         "check": "no_interaction_for_link"},
    ],
    13: [
        {"key": "「先发起来，别他妈天天研究怎么开始。」被标 号召互动",
         "check": "no_interaction_for_advice"},
    ],
    16: [
        {"key": "claims 存在只写一半的语义残句", "check": "claims_text_complete"},
    ],
    18: [
        {"key": "双关线索仅作字面食物解读", "check": "core_message_mentions_double_meaning"},
    ],
}


# ------------------------------------------------------------------ 判定器

def _moves(analysis: dict) -> list[dict]:
    return (analysis.get("writing") or {}).get("moves") or []


def _markers(analysis: dict) -> list[dict]:
    return (analysis.get("style") or {}).get("markers") or []


def _claims(analysis: dict) -> list[dict]:
    return (analysis.get("content") or {}).get("claims") or []


def _has_interaction_move(analysis: dict) -> dict | None:
    for m in _moves(analysis):
        if m.get("type") == "号召互动":
            return m
    return None


def _is_platform_interaction(analysis: dict) -> bool:
    """被判为「号召互动」的 move 是否**真的**在邀请平台互动。

    判据（来自补充规则）：evidence 里必须出现回复 / 评论 / 推荐 / 转发 /
    点赞 / 参与讨论 等平台互动词。否则视为误标。
    """
    move = _has_interaction_move(analysis)
    if move is None:
        return False
    ev = move.get("evidence") or ""
    keywords = ("评论", "回复", "推荐", "转发", "点赞", "参与", "留言",
                "补充", "告诉我", "在线等", "说说", "回答")
    return any(k in ev for k in keywords)


def check_no_interaction_for_advice(analysis: dict) -> tuple[bool, str]:
    """行动建议 / 命令 / 劝诫 / 纯链接 ≠ 平台互动号召。"""
    move = _has_interaction_move(analysis)
    if move is None:
        return True, "未标 号召互动"
    ev = move.get("evidence") or ""
    if any(k in ev for k in ("评论", "回复", "推荐", "转发", "点赞",
                             "参与", "留言", "补充", "告诉我", "在线等",
                             "说说", "回答")):
        return True, f"标了 号召互动，但 evidence 含互动词：{ev[:40]!r}"
    return False, f"仍误标 号召互动：{ev[:60]!r}"


def check_no_interaction_for_link(analysis: dict) -> tuple[bool, str]:
    """提供链接 ≠ 请读者评论或回复。"""
    move = _has_interaction_move(analysis)
    if move is None:
        return True, "未标 号召互动"
    ev = move.get("evidence") or ""
    if "http" in ev or "入口" in ev:
        return False, f"仍把「提供链接」标为 号召互动：{ev[:60]!r}"
    return check_no_interaction_for_advice(analysis)


#: 中文数字
_CN_DIGITS = "零一二三四五六七八九十百千万亿两"
#: 可跟在数量后面的量词 / 数位（只有「数词 + 量词」才算在表达数量）
_CN_MEASURES = (
    "个篇次条种类点位名件只张本台部套年月日天时分秒块元角组批层重轮遍番回"
    "万亿千百十"
)


def _has_actual_number(text: str) -> bool:
    """文本里是否有**实际数字**参与表达。

    两类都算：
      - 阿拉伯数字（`3`、`100万`）；
      - 中文数字**且后接量词**（`九个`、`三个`、`一天`、`半年`）。

    为什么中文侧必须要求量词：原文写「这九个话题，至少有三个都坚决不能碰」时
    「九」「三」是实打实的数量；但若只看到「一」就判为有数字，
    「集合装逼、键政…为一体」这种**显然没有数量**的句子也会被误判成合法。
    （第一版只认阿拉伯数字 → 漏判；第二版只认字符 → 误判。两轮都踩过。）
    """
    if any(ch.isdigit() for ch in text):
        return True
    for i, ch in enumerate(text):
        if ch in _CN_DIGITS and i + 1 < len(text) and text[i + 1] in _CN_MEASURES:
            return True
    return False


def check_no_digit_marker_without_digits(analysis: dict) -> tuple[bool, str]:
    """「数字强调」的 evidence 里必须真的有数字（阿拉伯或中文数字）。"""
    bad = [
        m for m in _markers(analysis)
        if m.get("type") == "数字强调"
        and not _has_actual_number(m.get("evidence") or "")
    ]
    if bad:
        return False, f"仍有无数字的 数字强调：{[b.get('evidence') for b in bad]}"
    return True, "数字强调均含实际数字（或未标）"


#: 明显残缺的结尾特征（启发式，不是完备判定）
_UNCLOSED_OPENERS = ("（", "(", "「", "『", "“", "《")
#: 连接词 / 结构助词 / 单字介词：出现在结尾即为未说完
_DANGLING_TAILS = (
    "的", "与", "和", "及", "或", "而", "并", "以及", "并且", "而且",
    "为", "对", "把", "被", "在", "从", "向", "给", "跟", "同", "将",
)
#: 单字动词：需要宾语却没接上
_DANGLING_VERBS = (
    "入", "是", "有", "做", "成", "用", "说", "写", "看", "让", "使",
    "叫", "称", "当", "作", "归", "含", "指",
)


def _looks_incomplete(text: str) -> str | None:
    """返回残缺原因；完整则返回 None。"""
    t = (text or "").strip()
    if not t:
        return "空文本"
    if t.count("（") > t.count("）") or t.count("(") > t.count(")"):
        return "括号未闭合"
    if t.endswith(_UNCLOSED_OPENERS):
        return "以开括号/引号结尾"
    if any(t.endswith(tail) for tail in _DANGLING_TAILS):
        return "以连接词/介词结尾"
    if len(t) <= 4 and not any(ch in "。！？!?" for ch in t):
        return f"过短且无句末标点（{len(t)} 字）"
    if any(t.endswith(v) for v in _DANGLING_VERBS) and not t.endswith(("认为", "指出")):
        return "以缺宾语的单字动词结尾"
    return None


def check_claims_text_complete(analysis: dict) -> tuple[bool, str]:
    """claims[].text / must_preserve[] / core_message 都必须是完整通顺的表达。

    补充规则只显式约束了 `claims[].text`，但 04-A 交付样本显示
    `core_message` 与 `must_preserve` 也会残缺（content 16 的 core_message
    就是半句「作者用两个同构的」）。这里一并检查 ——
    如实暴露问题，而不是只检查规则提到的那一个字段。
    """
    problems: list[str] = []

    for c in _claims(analysis):
        reason = _looks_incomplete(c.get("text"))
        if reason:
            problems.append(f"claims.text {reason}：{(c.get('text') or '')[:30]!r}")

    for item in (analysis.get("content") or {}).get("must_preserve") or []:
        reason = _looks_incomplete(item)
        if reason:
            problems.append(f"must_preserve {reason}：{item[:30]!r}")

    reason = _looks_incomplete(analysis.get("core_message"))
    if reason:
        problems.append(
            f"core_message {reason}：{(analysis.get('core_message') or '')[:30]!r}"
        )

    if problems:
        return False, "；".join(problems[:4])
    return True, "claims / must_preserve / core_message 文本完整"


def check_no_punct_in_must_preserve(analysis: dict) -> tuple[bool, str]:
    """must_preserve 不得把**标点/换行类表达技巧**写成事实约束。

    只判规则里最明确、也最常被误用的部分：感叹号 / 标点 / 括号 / 换行。
    「句式」不自动判负 —— 例如「两个议题以完全相同的疑问句式并列」
    可能确实是必须保住的内容结构（规则本身也留了「除非原文的特殊符号
    本身就是被讨论的对象」这个口子）。这类边界由人工复核，机器不替人下结论。
    """
    items = (analysis.get("content") or {}).get("must_preserve") or []
    bad = [i for i in items if any(k in i for k in ("感叹号", "标点", "换行", "括号"))]
    if bad:
        return False, f"仍把标点/换行类技巧写进 must_preserve：{bad}"
    return True, f"{len(items)} 条 must_preserve 未含标点/换行类技巧约束"


def check_core_message_mentions_double_meaning(analysis: dict) -> tuple[bool, str]:
    """双关线索允许提示「可能有双关」，且不得把一种解读断言为唯一事实。"""
    core = analysis.get("core_message") or ""
    has_double = any(k in core for k in ("双关", "谐音", "暗示", "可能", "一语双关"))
    if has_double:
        return True, "核心含义中提示了双关/可能解读"
    return False, f"核心含义未提示双关线索：{core[:80]!r}"


CHECKS = {
    "no_interaction_for_advice": check_no_interaction_for_advice,
    "no_interaction_for_link": check_no_interaction_for_link,
    "no_digit_marker_without_digits": check_no_digit_marker_without_digits,
    "claims_text_complete": check_claims_text_complete,
    "no_punct_in_must_preserve": check_no_punct_in_must_preserve,
    "core_message_mentions_double_meaning": check_core_message_mentions_double_meaning,
}

#: 全部 7 条通用的结构不变量
UNIVERSAL = (
    ("no_interaction_for_advice", "行动建议/劝诫不得标 号召互动"),
    ("no_digit_marker_without_digits", "数字强调必须有实际数字"),
    ("claims_text_complete", "claims 文本必须完整"),
    ("no_punct_in_must_preserve", "标点/换行不得进 must_preserve"),
)


def open_ro(db_path: str) -> sqlite3.Connection:
    uri = "file:" + str(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


async def run(source_db: str, ids: list[int], dry: bool = False) -> int:
    source = Path(source_db)
    if not source.exists():
        print(f"源库不存在：{source}")
        return 2
    if source.name == "content_studio.db":
        print("拒绝运行：这是真实库")
        return 2

    # ---- 复制一份：源库保持只读，旧输出用于前后对照 ----
    work_dir = Path(tempfile.mkdtemp(prefix="cs_v31_"))
    work_db = work_dir / "v31.db"
    shutil.copy2(source, work_db)
    print(f"[源库（只读，v3 输出）] {source}")
    print(f"[工作库（v3.1 重跑）  ] {work_db}")

    import app.config as config_module

    config_module.settings.db_path = work_db

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    from app.config import settings
    from app.db import get_conn
    from app.services import analyzer

    if not settings.ai_configured:
        print("未配置 AI_API_KEY")
        return 2

    print(f"[模型] {settings.model_analysis}；[提示词版本] "
          f"{config_module.ANALYSIS_PROMPT_VERSION}")
    print(f"[样本] 仅这 {len(ids)} 条（显式 ID，绝不用宽泛条件）：{ids}\n")

    # ---- 旧输出（源库，只读）----
    old: dict[int, dict] = {}
    texts: dict[int, str] = {}
    ro = open_ro(str(source))
    try:
        for cid in ids:
            r = ro.execute(
                "SELECT a.analysis_json, a.prompt_version, c.raw_text "
                "  FROM analyses a JOIN contents c ON c.id = a.content_id "
                " WHERE a.content_id = ?",
                (cid,),
            ).fetchone()
            if r is None:
                print(f"  ! content {cid} 在源库无分析结果，跳过")
                continue
            old[cid] = {
                "analysis": json.loads(r["analysis_json"]),
                "prompt_version": r["prompt_version"],
            }
            texts[cid] = r["raw_text"]
    finally:
        ro.close()

    if not old:
        print("没有可对照的样本")
        return 2

    # ---- 用 v3.1 重跑（工作库）----
    print("[ANALYSIS v3.1] 逐条重跑")
    fresh: dict[int, dict] = {}
    errors: dict[int, str] = {}
    if dry:
        print("  [DRY] 跳过全部 AI 调用；旧输出即为新输出（只为验证脚本链路）")
        fresh = {cid: old[cid] for cid in ids if cid in old}
    for cid in ([] if dry else ids):
        if cid not in old:
            continue
        try:
            await analyzer.analyze_content(cid)
            with get_conn(settings.db_path) as conn:
                row = conn.execute(
                    "SELECT analysis_json, prompt_version FROM analyses "
                    " WHERE content_id = ?",
                    (cid,),
                ).fetchone()
            fresh[cid] = {
                "analysis": json.loads(row["analysis_json"]),
                "prompt_version": row["prompt_version"],
            }
            print(f"  ✓ content {cid}（{row['prompt_version']}）")
        except Exception as err:  # noqa: BLE001
            errors[cid] = f"{type(err).__name__}: {err}"
            print(f"  ✗ content {cid} → {type(err).__name__}: {err}")

    # ---- 逐项判定 ----
    print("\n" + "=" * 100)
    print("逐项验收（文档 §3 已证实的问题）")
    print("=" * 100)
    verdicts: list[dict] = []
    for cid in ids:
        if cid not in fresh:
            verdicts.append({"content_id": cid, "status": "未跑成功",
                             "items": [], "error": errors.get(cid)})
            continue
        items = []
        checks = list(KNOWN_ISSUES.get(cid, [])) + [
            {"key": label, "check": key} for key, label in UNIVERSAL
        ]
        seen = set()
        for c in checks:
            if c["check"] in seen:
                continue
            seen.add(c["check"])
            ok, detail = CHECKS[c["check"]](fresh[cid]["analysis"])
            items.append({"issue": c["key"], "fixed": ok, "detail": detail})
        n_ok = sum(1 for i in items if i["fixed"])
        verdicts.append(
            {"content_id": cid, "status": f"{n_ok}/{len(items)}",
             "items": items, "error": None}
        )

    for v in verdicts:
        head = f"content {v['content_id']}：{v['status']}"
        print(f"\n{head}")
        for i in v["items"]:
            print(f"   {'✓' if i['fixed'] else '✗'} {i['issue']}")
            print(f"       {i['detail']}")
        if v.get("error"):
            print(f"   ✗ 异常：{v['error']}")

    # ---- 导出对照交付物 ----
    out_dir = Path(__file__).resolve().parent / "task04a_deliverables"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / "v3.1-修正前后对照.md"
    json_path = out_dir / "v3.1-逐项验收.json"

    lines = [
        "# 04-A 定点修正：v3 → v3.1 真实样本前后对照",
        "",
        "> 由 `validate_v31.py` 自动生成。只跑文档 §7.4 指定的 7 条样本，",
        "> 使用隔离数据库副本重跑，源库（v3 输出）只读保留。**未调用付费 Batch。**",
        "",
        f"- 模型：`{settings.model_analysis}`",
        f"- 提示词版本：v3 → **v3.1**（Schema 仍为 `analysis-v3`，字段树与枚举未变）",
        f"- 样本：{ids}",
        "",
    ]
    for cid in ids:
        if cid not in old:
            continue
        lines += [f"## content {cid}", ""]
        v = next((x for x in verdicts if x["content_id"] == cid), None)
        if v:
            lines += [f"**判定：{v['status']}**", ""]
            for i in v["items"]:
                lines.append(f"- {'✓' if i['fixed'] else '✗'} {i['issue']} — {i['detail']}")
            lines.append("")
        lines += ["**原文**", "", "```text", (texts[cid] or "").strip(), "```", ""]

        for label, payload in (("v3（旧）", old.get(cid)),
                               ("v3.1（新）", fresh.get(cid))):
            if payload is None:
                lines += [f"**{label}**：_(未产出 —— {errors.get(cid, '跳过')})_", ""]
                continue
            body = {k: val for k, val in payload["analysis"].items()
                    if k not in ("model", "prompt_version", "schema_version",
                                 "schema_hash", "created_at", "length_chars")}
            lines += [
                f"**{label}**　`prompt_version={payload['prompt_version']}`",
                "",
                "```json",
                json.dumps(body, ensure_ascii=False, indent=2),
                "```",
                "",
            ]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    total_items = sum(len(v["items"]) for v in verdicts)
    fixed_items = sum(1 for v in verdicts for i in v["items"] if i["fixed"])
    json_path.write_text(
        json.dumps(
            {
                "source_db": str(source),
                "work_db": str(work_db),
                "prompt_version": config_module.ANALYSIS_PROMPT_VERSION,
                "schema_version": config_module.ANALYSIS_SCHEMA_VERSION,
                "ids": ids,
                "items_total": total_items,
                "items_fixed": fixed_items,
                "verdicts": verdicts,
                "errors": errors,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    print("\n" + "=" * 100)
    print(f"汇总：{fixed_items}/{total_items} 项判定为已修复")
    print(f"交付物：{md_path}")
    print(f"        {json_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_db")
    parser.add_argument("--ids", default=",".join(str(i) for i in DEFAULT_IDS))
    parser.add_argument("--dry", action="store_true",
                        help="只验证脚本链路，跳过全部 AI 调用")
    args = parser.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    return asyncio.run(run(args.source_db, ids, dry=args.dry))


if __name__ == "__main__":
    sys.exit(main())
