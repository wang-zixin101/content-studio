"""冻结第二轮重测的 12 条样本。

严格执行 ChatGPT 确定的构成（五个类别，互不重复，同一条只占一个名额）：
    1. 极短、信息不足的帖子        4 条
    2. 30–100 字常规帖子           4 条
    3. 100–300 字帖子              2 条
    4. 真实最长的 3999 字帖子       1 条
    5. 引述 / 反讽 / 观点归属模糊   1 条
                                合计 12 条

选择规则全部确定性、可复现（不引入随机）：

    cat1  在全 48 条中按正文字符数升序取最前 4 条
    cat2  真实语料中 30 <= len < 100 的样本，按长度升序排列，
          取索引 [n//4, n//2, 3n//4, n-1]（在带内均匀铺开）
    cat3  真实语料中 100 <= len < 300 的样本，按长度升序取首、尾各 1 条
    cat4  真实语料中长度最大的 1 条
    cat5  人工边界样本里类别属于「引文归属 / 讽刺反话 / 匿名转述」的，
          按该优先级取第一条；若被前面类别占用则顺延

最后一类若候选不足则**报告缺口，不自行编造样本**（本脚本会断言）。

本脚本在任何付费请求之前运行，产出 round2_selection.json 即冻结。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases" / "merged_48.jsonl"
OUT = HERE / "round2_selection.json"

CAT5_PRIORITY = ("引文归属", "讽刺反话", "匿名转述")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    cases = [
        json.loads(line)
        for line in CASES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    real = [c for c in cases if c.get("origin") == "xporter_real"]
    synth = [c for c in cases if c.get("origin") == "chatgpt_synthetic"]

    used: set[str] = set()
    picks: list[dict] = []

    def add(case: dict, slot: str, rule: str) -> None:
        assert case["id"] not in used, f"{case['id']} 被重复占用"
        used.add(case["id"])
        picks.append(
            {
                "slot": slot,
                "case_id": case["id"],
                "origin": case.get("origin"),
                "case_category": case.get("category"),
                "text_len": len(case["text"]),
                "text_sha256": sha256_text(case["text"]),
                "selection_rule": rule,
                "text": case["text"],
            }
        )

    # cat1 极短、信息不足 4 条
    by_len = sorted(cases, key=lambda c: (len(c["text"]), c["id"]))
    for c in by_len[:4]:
        add(c, "1-极短信息不足", "全 48 条按正文字符数升序取最前 4 条")

    # cat2 30–100 字常规 4 条
    band2 = sorted(
        [c for c in real if 30 <= len(c["text"]) < 100],
        key=lambda c: (len(c["text"]), c["id"]),
    )
    n2 = len(band2)
    idx2 = sorted({n2 // 4, n2 // 2, 3 * n2 // 4, n2 - 1})
    assert len(idx2) == 4, f"cat2 名额不足：band={n2} idx={idx2}"
    for i in idx2:
        add(band2[i], "2-常规30-100字", f"真实语料 30–100 字带内 n={n2}，取索引 {idx2}")

    # cat3 100–300 字 2 条
    band3 = sorted(
        [c for c in real if 100 <= len(c["text"]) < 300],
        key=lambda c: (len(c["text"]), c["id"]),
    )
    assert len(band3) >= 2, f"cat3 候选不足：{len(band3)}"
    for c in (band3[0], band3[-1]):
        add(c, "3-中长100-300字", f"真实语料 100–300 字带内 n={len(band3)}，取首尾各 1 条")

    # cat4 真实最长 1 条
    longest = max(real, key=lambda c: len(c["text"]))
    add(longest, "4-真实最长帖", "真实语料中正文字符数最大的 1 条")

    # cat5 引述 / 反讽 / 归属模糊 1 条
    cat5 = None
    for label in CAT5_PRIORITY:
        cand = [c for c in synth if c.get("category") == label and c["id"] not in used]
        if cand:
            cat5 = sorted(cand, key=lambda c: c["id"])[0]
            break
    assert cat5 is not None, "cat5 候选不足：需报告缺口，不得自行编造"
    add(cat5, "5-引述或反讽或归属模糊", f"人工边界样本按优先级 {CAT5_PRIORITY} 取第一条未被占用者")

    # 校验
    from collections import Counter

    cnt = Counter(p["slot"][0] for p in picks)
    assert cnt == {"1": 4, "2": 4, "3": 2, "4": 1, "5": 1}, cnt
    assert len(used) == 12, len(used)

    OUT.write_text(
        json.dumps(
            {
                "frozen_at": "2026-10-09",
                "source_test_set": str(CASES),
                "total_cases": len(cases),
                "selection": picks,
                "counts": dict(cnt),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"已冻结 {len(picks)} 条 → {OUT}")
    print(f"类别计数：{dict(cnt)}")
    print()
    for p in picks:
        print(f"  {p['slot']:<22} {p['case_id']} | {p['text_len']:>4}字 | "
              f"sha256={p['text_sha256'][:12]}… | {p['text'][:26]!r}")


if __name__ == "__main__":
    main()
