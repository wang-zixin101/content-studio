"""从 requests/*.json 生成 V2/V3 自动统计对照表。

只做客观计数与费用计算，**不做质量打分、不判断哪一版更好**。
语义准确性、幻觉、修辞判断、证据充分性、写作价值留给 ChatGPT 独立评估。

费用口径：
    - 保守口径：全部输入按未命中缓存 ¥3.00/M
    - 缓存感知口径：cached_tokens × ¥0.025/M + 其余输入 × ¥3.00/M，输出 ¥6.00/M
      （官方定价 platform.xiaomimimo.com/docs/price/pay-as-you-go）
"""

from __future__ import annotations

import glob
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import sys

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))
from app.services.analysis_spec import spec_field_paths  # noqa: E402

OBJ_KEYS = {"content_invariants", "rhetorical", "stance_engagement",
            "microstyle", "style_boundary"}
LEAF = [p for p in spec_field_paths() if p not in OBJ_KEYS]

PRICE_IN = 3.00
PRICE_IN_CACHED = 0.025
PRICE_OUT = 6.00


def cost_conservative(u: dict) -> float:
    return (u.get("prompt_tokens", 0) / 1e6) * PRICE_IN + (u.get("completion_tokens", 0) / 1e6) * PRICE_OUT


def cost_cached_aware(u: dict) -> float:
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    tin = u.get("prompt_tokens") or 0
    uncached = max(tin - cached, 0)
    return (cached / 1e6) * PRICE_IN_CACHED + (uncached / 1e6) * PRICE_IN + (
        (u.get("completion_tokens") or 0) / 1e6) * PRICE_OUT


def main(run_dir: Path) -> dict:
    files = sorted((run_dir / "requests").glob("*.json"))
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in files]

    stats: dict[str, dict] = {}
    for v in ("v2", "v3"):
        rs = [r for r in recs if r["version"] == v]
        n = len(rs)
        if n == 0:
            continue
        http_ok = sum(1 for r in rs if r["response"]["http_status"] == 200 and not r["response"]["error"])
        parsed = sum(1 for r in rs if r["parse"]["parsed"] is not None)
        schema_ok = sum(1 for r in rs if r["schema"]["schema_ok"])
        trunc = sum(1 for r in rs if r["response"]["finish_reason"] == "length")
        lc_match = sum(1 for r in rs if r["length_chars"].get("match"))
        lc_evaluable = sum(1 for r in rs if r["parse"]["parsed"] is not None)

        tin = [ (r.get("usage") or {}).get("prompt_tokens") or 0 for r in rs ]
        tout = [ (r.get("usage") or {}).get("completion_tokens") or 0 for r in rs ]
        treason = [ ((r.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for r in rs ]
        tcached = [ ((r.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for r in rs ]
        elapsed = [ r["response"]["elapsed_s"] or 0 for r in rs ]

        c_consv = sum(cost_conservative(r.get("usage") or {}) for r in rs)
        c_cache = sum(cost_cached_aware(r.get("usage") or {}) for r in rs)

        # 逐字段形态统计（仅对解析成功的记录）
        field = {p: Counter() for p in LEAF}
        for r in rs:
            fs = r.get("field_stats") or {}
            for p in LEAF:
                field[p][fs.get(p, "absent")] += 1

        stats[v] = {
            "n_requests": n,
            "http_ok": http_ok,
            "http_success_rate": round(http_ok / n, 4),
            "valid_json": parsed,
            "valid_json_rate": round(parsed / n, 4),
            "schema_ok": schema_ok,
            "schema_pass_rate": round(schema_ok / n, 4),
            "schema_pass_rate_of_parsed": round(schema_ok / parsed, 4) if parsed else None,
            "finish_reason": dict(Counter(r["response"]["finish_reason"] for r in rs)),
            "truncated": trunc,
            "truncation_rate": round(trunc / n, 4),
            "parse_mode": dict(Counter(r["parse"]["mode"] for r in rs)),
            "schema_fail_reasons": dict(Counter(
                ("missing_field" if "缺少必需字段" in (r["schema"].get("error_message") or "")
                 else "unknown_field" if "未知字段" in (r["schema"].get("error_message") or "")
                 else "type_error" if "应为" in (r["schema"].get("error_message") or "")
                 else "not_parsable")
                for r in rs if not r["schema"]["schema_ok"]
            )),
            "length_chars_match": lc_match,
            "length_chars_evaluable": lc_evaluable,
            "length_chars_accuracy": round(lc_match / lc_evaluable, 4) if lc_evaluable else None,
            "tokens": {
                "input_total": sum(tin), "input_avg": round(statistics.mean(tin), 1),
                "output_total": sum(tout), "output_avg": round(statistics.mean(tout), 1),
                "reasoning_total": sum(treason), "reasoning_avg": round(statistics.mean(treason), 1),
                "cached_total": sum(tcached),
                "cached_ratio": round(sum(tcached) / sum(tin), 4) if sum(tin) else 0,
            },
            "latency_s": {
                "avg": round(statistics.mean(elapsed), 2),
                "median": round(statistics.median(elapsed), 2),
                "min": round(min(elapsed), 2), "max": round(max(elapsed), 2),
            },
            "cost_cny": {
                "total_conservative": round(c_consv, 4),
                "total_cache_aware": round(c_cache, 4),
                "per_request_conservative": round(c_consv / n, 5),
                "per_request_cache_aware": round(c_cache / n, 5),
                "per_1000_conservative": round(c_consv / n * 1000, 2),
                "per_1000_cache_aware": round(c_cache / n * 1000, 2),
            },
            "field_fill": {
                p: {"filled": c.get("filled", 0), "empty_list": c.get("empty_list", 0),
                    "null_or_blank": c.get("null_or_blank", 0), "absent": c.get("absent", 0)}
                for p, c in field.items()
            },
        }

    total = {
        "run_dir": str(run_dir),
        "requests_total": len(recs),
        "cost_total_conservative": round(sum(cost_conservative(r.get("usage") or {}) for r in recs), 4),
        "cost_total_cache_aware": round(sum(cost_cached_aware(r.get("usage") or {}) for r in recs), 4),
        "tokens_input_total": sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in recs),
        "tokens_output_total": sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in recs),
        "wall_clock_s": round(sum(r["response"]["elapsed_s"] or 0 for r in recs), 1),
    }

    out = {"totals": total, "by_version": stats}
    (run_dir / "_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print("=" * 78)
    print(f"自动统计对照表  ·  {run_dir.name}")
    print("=" * 78)
    print(f"总请求 {total['requests_total']} 次 ｜ 输入 {total['tokens_input_total']:,} tok "
          f"｜ 输出 {total['tokens_output_total']:,} tok")
    print(f"费用：保守 ¥{total['cost_total_conservative']:.4f} ｜ 缓存感知 ¥{total['cost_total_cache_aware']:.4f}")
    print()
    rows = [
        ("请求数", "n_requests"),
        ("HTTP 成功", "http_ok"),
        ("HTTP 成功率", "http_success_rate"),
        ("可解析 JSON", "valid_json"),
        ("合法 JSON 比例", "valid_json_rate"),
        ("Schema 通过", "schema_ok"),
        ("Schema 通过率(全部)", "schema_pass_rate"),
        ("Schema 通过率(仅已解析)", "schema_pass_rate_of_parsed"),
        ("截断数", "truncated"),
        ("截断率", "truncation_rate"),
        ("length_chars 正确", "length_chars_match"),
        ("length_chars 准确率", "length_chars_accuracy"),
    ]
    print(f"{'指标':<26}{'V2':>16}{'V3':>16}")
    print("-" * 58)
    for label, key in rows:
        a = stats.get("v2", {}).get(key)
        b = stats.get("v3", {}).get(key)
        fa = f"{a:.3f}" if isinstance(a, float) else str(a)
        fb = f"{b:.3f}" if isinstance(b, float) else str(b)
        print(f"{label:<26}{fa:>16}{fb:>16}")
    print("-" * 58)
    for label, key in (("平均输入 token", "input_avg"), ("平均输出 token", "output_avg"),
                       ("平均推理 token", "reasoning_avg")):
        a = stats["v2"]["tokens"][key]; b = stats["v3"]["tokens"][key]
        print(f"{label:<26}{a:>16}{b:>16}")
    print(f"{'缓存命中率':<26}{stats['v2']['tokens']['cached_ratio']:>16.3f}{stats['v3']['tokens']['cached_ratio']:>16.3f}")
    print(f"{'平均耗时 s':<26}{stats['v2']['latency_s']['avg']:>16}{stats['v3']['latency_s']['avg']:>16}")
    print(f"{'单条费用(缓存感知) ¥':<26}{stats['v2']['cost_cny']['per_request_cache_aware']:>16.5f}"
          f"{stats['v3']['cost_cny']['per_request_cache_aware']:>16.5f}")
    print(f"{'每千条费用(保守) ¥':<26}{stats['v2']['cost_cny']['per_1000_conservative']:>16.2f}"
          f"{stats['v3']['cost_cny']['per_1000_conservative']:>16.2f}")
    print()
    print("finish_reason 分布：", {v: stats[v]["finish_reason"] for v in stats})
    print("Schema 失败分类：", {v: stats[v]["schema_fail_reasons"] for v in stats})
    print()
    print(f"已写入 {run_dir / '_stats.json'}")
    return out


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted((HERE / "results").glob("*_r1"))[-1]
    main(d)
