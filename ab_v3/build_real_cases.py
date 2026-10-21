"""从真实 Xporter 语料中分层抽样，补充 ChatGPT 设计文档要求的 30 条真实样本。

设计文档（analysis_v3_design.md §5）原文要求：
    「人工设计样本不能替代真实 X 语料；实际实验应另从真实 159 条 Xporter 中
      分层抽样，例如 30 条，使总测试集达到 48 条。」

抽样口径（确定性，固定种子，可复现）：
    - 语料：hope yin 小曦 10.6 159条帖子.json（159 条）
    - 分层：按正文 Unicode 字符数分 4 层，按各层在总体中的占比分配名额
    - 层内：按 id 升序后等距取样（不是随机，保证可复现）
    - 去重：完全相同正文只保留第一条
    - 清洗：跳过空正文；保留换行原样（不 strip 掉结构）

0 AI / 0 网络 / 只读语料。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

CORPUS = Path(r"C:\Users\18483\Desktop\x运营\hope yin 小曦 10.6 159条帖子.json")
OUT = Path(__file__).resolve().parent / "cases" / "real_xporter_30.jsonl"

TARGET = 30
SEED = 20261009

# 分层边界（字符数）：与交接材料中报告的分布口径一致
BANDS = ((0, 30), (30, 100), (100, 300), (300, 10**9))
BAND_LABELS = ("<30", "30-100", "100-300", ">=300")


def main() -> None:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))
    print(f"语料条数：{len(rows)}")

    # 去重 + 去空
    seen: set[str] = set()
    usable: list[dict] = []
    for r in rows:
        text = r.get("text") or ""
        if not text.strip():
            continue
        if text in seen:
            continue
        seen.add(text)
        usable.append({"source_id": r.get("id"), "text": text, "type": r.get("type")})
    print(f"去重后可抽样：{len(usable)}")

    # 分层
    buckets: list[list[dict]] = [[] for _ in BANDS]
    for item in usable:
        n = len(item["text"])
        for idx, (lo, hi) in enumerate(BANDS):
            if lo <= n < hi:
                buckets[idx].append(item)
                break

    counts = [len(b) for b in buckets]
    print("各层实际条数：", dict(zip(BAND_LABELS, counts)))

    # 按比例分配名额（最大余额法）
    raw = [TARGET * c / len(usable) for c in counts]
    quota = [int(x) for x in raw]
    remainder = TARGET - sum(quota)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - quota[i], reverse=True)
    for i in order[:remainder]:
        quota[i] += 1

    # 保证每个非空层至少 1 条：长文本层只有 1 条语料，按比例会被算成 0，
    # 但长帖压力测试正是本轮要覆盖的场景，必须强制纳入。
    for i, c in enumerate(counts):
        if c > 0 and quota[i] == 0:
            quota[i] = 1
    while sum(quota) > TARGET:
        # 从当前名额最多的层扣（且不会扣到 1 以下）
        i = max((j for j, c in enumerate(counts) if c > 0), key=lambda j: quota[j])
        if quota[i] <= 1:
            break
        quota[i] -= 1
    print("各层名额：", dict(zip(BAND_LABELS, quota)))

    # 层内等距取样（确定性）
    rng = random.Random(SEED)
    picked: list[dict] = []
    for idx, bucket in enumerate(buckets):
        bucket = sorted(bucket, key=lambda x: str(x["source_id"]))
        k = quota[idx]
        if k <= 0 or not bucket:
            continue
        if k >= len(bucket):
            chosen = bucket
        else:
            step = len(bucket) / k
            chosen = [bucket[int(i * step)] for i in range(k)]
        picked.extend(chosen)

    # 打乱层间顺序，避免测试集按长度排列（仍然确定性）
    rng.shuffle(picked)

    lines = []
    for i, item in enumerate(picked, 1):
        lines.append(
            json.dumps(
                {
                    "id": f"R{i:02d}",
                    "category": "真实语料",
                    "source": "xporter_real",
                    "source_id": item["source_id"],
                    "tweet_type": item["type"],
                    "text": item["text"],
                    "expected_checks": None,  # 真实语料不预设人工判据，由评审阶段判定
                },
                ensure_ascii=False,
            )
        )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    lens = [len(p["text"]) for p in picked]
    print(f"\n已写出 {len(picked)} 条 → {OUT}")
    print(f"字符数：中位 {sorted(lens)[len(lens)//2]}｜最小 {min(lens)}｜最大 {max(lens)}")


if __name__ == "__main__":
    main()
