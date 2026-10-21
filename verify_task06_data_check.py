"""任务 06 · 场景 D 的**数据库侧**断言（只读预览库）。

配合 `verify_task06_data.mjs` 使用：那边在浏览器里走完六种创作 / 保存方式，
这边直接查库验证 source / provenance / style_eligible / ai_draft 基线 /
final_text / edit_ratio 是否与规格一致。

只读预览库，**不碰生产库与 Desktop V1**。

用法：
    ./backend/.venv/Scripts/python.exe verify_task06_data_check.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PREVIEW_DB = Path(tempfile.gettempdir()) / "cs_task05_preview" / "preview.db"

BASELINE = "<T06-BASELINE> 去年试了 17 个写作工具，最后只留 2 个。"

failures: list[str] = []


def check(label: str, got, want) -> None:
    mark = "✓" if got == want else "✗"
    print(f"  {mark} {label}: {got!r}" + ("" if got == want else f"（期望 {want!r}）"))
    if got != want:
        failures.append(f"{label}: got={got!r} want={want!r}")


def main() -> int:
    if not PREVIEW_DB.exists():
        print(f"预览库不存在：{PREVIEW_DB}")
        return 2
    conn = sqlite3.connect(f"file:{PREVIEW_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print("=" * 78)
    print("场景 D：来源规则核对（预览库只读）")
    print("=" * 78)

    # ---------------- ① 手写原创成稿 ----------------
    print("\n① 手写原创 → 成稿")
    row = conn.execute(
        "SELECT * FROM contents WHERE raw_text LIKE '%【T06手写成稿】%'"
    ).fetchone()
    if row is None:
        failures.append("找不到【T06手写成稿】那一条")
        print("  ✗ 没找到【T06手写成稿】")
    else:
        check("source", row["source"], "mine")
        check("provenance", row["provenance"], "human_original")
        check("style_eligible", int(row["style_eligible"]), 1)
        check("analysis_state", row["analysis_state"], "unprocessed")

    # ---------------- ② 个人灵感 ----------------
    print("\n② 个人灵感")
    row = conn.execute(
        "SELECT * FROM contents WHERE raw_text LIKE '%【T06灵感】%'"
    ).fetchone()
    if row is None:
        failures.append("找不到【T06灵感】那一条")
        print("  ✗ 没找到【T06灵感】")
    else:
        check("source", row["source"], "mine")
        check("provenance", row["provenance"], "human_original")
        check("style_eligible（灵感不作为成熟风格样本）", int(row["style_eligible"]), 0)

    # ---------------- ⑤ 对标原帖 ----------------
    print("\n⑤ 对标原帖收录")
    row = conn.execute(
        "SELECT * FROM contents WHERE raw_text LIKE '%【T06对标收录】%'"
    ).fetchone()
    if row is None:
        failures.append("找不到【T06对标收录】那一条")
        print("  ✗ 没找到【T06对标收录】")
    else:
        check("source", row["source"], "benchmark")
        check("provenance（benchmark 不带 provenance）", row["provenance"], None)
        check("style_eligible", int(row["style_eligible"]), 0)

    # ---------------- ④ AI 采用稿 ----------------
    print("\n④ AI 候选采用 → 编辑 → 保存")
    row = conn.execute(
        "SELECT * FROM contents WHERE raw_text LIKE '%我又改了一版%'"
    ).fetchone()
    if row is None:
        failures.append("找不到 AI 采用稿那一条")
        print("  ✗ 没找到 AI 采用稿")
    else:
        check("source", row["source"], "mine")
        check("provenance（AI 参与过就是 ai_assisted）", row["provenance"], "ai_assisted")
        check("style_eligible", int(row["style_eligible"]), 1)
        check("analysis_state（保存不自动分析）", row["analysis_state"], "unprocessed")

        rw = conn.execute(
            "SELECT * FROM rewrites WHERE saved_content_id = ?", (row["id"],)
        ).fetchone()
        if rw is None:
            failures.append("AI 采用稿没有对应的 rewrite 记录")
            print("  ✗ 没有 rewrite 记录")
        else:
            check("ai_draft 基线保留", rw["ai_draft"], BASELINE)
            check("final_text 是人工终稿", rw["final_text"], row["raw_text"])
            check("is_saved_as_mine", int(rw["is_saved_as_mine"]), 1)
            ratio = rw["edit_ratio"]
            ok_ratio = ratio is not None and float(ratio) > 0
            print(f"  {'✓' if ok_ratio else '✗'} 修改指标已计算: "
                  f"edit_ratio={ratio} edit_distance={rw['edit_distance']}")
            if not ok_ratio:
                failures.append("edit_ratio 缺失或为 0")
            different = rw["ai_draft"] != rw["final_text"]
            print(f"  {'✓' if different else '✗'} 终稿与 AI 基线确实不同")
            if not different:
                failures.append("终稿与 AI 基线相同，无法验证编辑指标")

    # ---------------- 未修改直接保存的 AI 稿仍属 ai_assisted ----------------
    print("\n（附加）未修改直接保存的 AI 稿仍应算 ai_assisted")
    rows = conn.execute(
        "SELECT c.id, c.provenance, r.ai_draft, r.final_text "
        "  FROM contents c JOIN rewrites r ON r.saved_content_id = c.id "
        " WHERE c.provenance = 'ai_assisted'"
    ).fetchall()
    print(f"  当前预览库里 ai_assisted 记录数：{len(rows)}")
    for r in rows:
        same = r["ai_draft"] == r["final_text"]
        print(f"  · id={r['id']} 未修改直接保存={same}")

    print()
    if failures:
        print(f"✗ {len(failures)} 项不通过：")
        for f in failures:
            print("   -", f)
        return 1
    print("✓ 场景 D 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
