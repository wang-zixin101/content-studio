"""合并测试集：ChatGPT 提供的 18 条边界样本 + 30 条真实 Xporter 样本 = 48 条。

合并规则（确定性）：
    - 先 18 条人工边界样本（id 前缀 T），再 30 条真实语料（id 前缀 R）
    - 二者 id 不冲突，合并后保留来源标记 origin
    - 不改写任何 text

0 AI。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNTH = HERE / "cases" / "eval_cases_v3.jsonl"
REAL = HERE / "cases" / "real_xporter_30.jsonl"
OUT = HERE / "cases" / "merged_48.jsonl"


def load(path: Path, origin: str) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        obj["origin"] = origin
        rows.append(obj)
    return rows


def main() -> None:
    synth = load(SYNTH, "chatgpt_synthetic")
    real = load(REAL, "xporter_real")
    merged = synth + real

    ids = [r["id"] for r in merged]
    assert len(ids) == len(set(ids)), "id 冲突"

    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n",
        encoding="utf-8",
    )
    lens = [len(r["text"]) for r in merged]
    print(f"合并完成：{len(synth)} 人工 + {len(real)} 真实 = {len(merged)} 条 → {OUT}")
    print(f"字符数：中位 {sorted(lens)[len(lens)//2]}｜最小 {min(lens)}｜最大 {max(lens)}")
    print(f"合计字符数：{sum(lens)}")


if __name__ == "__main__":
    main()
