"""把实验结果打包成便于上传到 ChatGPT 的 ZIP。

包含：
    1. 48 条测试样本及完整原文
    2. 全部请求槽位的原始响应与失败记录（含完整 messages）
    3. 解析后的结构化 JSON
    4. 脱敏的请求配置与提示词版本指纹
    5. 全部 usage / 耗时 / 费用
    6. V2/V3 自动统计对照表
    7. 12 条重复测试样本选择记录
    8. 随机化执行顺序、可复现设置与运行日志
    9. 盲评匿名结果 + 独立版本映射表

不含：API Key、.env 任何密钥、其它敏感配置。
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.prompts import (  # noqa: E402
    ANALYSIS_SCHEMA_HINT as V2_HINT,
    ANALYSIS_SYSTEM as V2_SYSTEM,
)
from app.config import ANALYSIS_SCHEMA_VERSION  # noqa: E402
from app.services.analysis_spec import ANALYSIS_SCHEMA_HASH  # noqa: E402

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "v3cand", HERE / "candidate" / "analysis_v3_candidate.py"
)
v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3)


def build(run_dir: Path, zip_path: Path, round2_executed: bool) -> None:
    req_files = sorted((run_dir / "requests").glob("*.json"))
    assert req_files, "没有请求记录"

    tmp = HERE / "_zip_build"
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "00_manifest").mkdir(parents=True)
    (tmp / "01_testset").mkdir()
    (tmp / "02_requests_raw").mkdir()
    (tmp / "03_parsed_json").mkdir()
    (tmp / "04_blind_review").mkdir()
    (tmp / "05_mapping").mkdir()
    (tmp / "06_stats").mkdir()
    (tmp / "07_config").mkdir()
    (tmp / "08_prompts_reference").mkdir()
    (tmp / "09_logs").mkdir()

    manifest = json.loads((run_dir / "_frozen_manifest.json").read_text(encoding="utf-8"))
    stats = json.loads((run_dir / "_stats.json").read_text(encoding="utf-8"))
    key = json.loads((run_dir / "_blind_key.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "_run_summary.json").read_text(encoding="utf-8"))

    # 00 清单
    (tmp / "00_manifest" / "frozen_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "00_manifest" / "prompt_fingerprints.json").write_text(
        json.dumps(manifest["prompts"], ensure_ascii=False, indent=2), encoding="utf-8")

    # 01 测试集
    for f in ("merged_48.jsonl", "real_xporter_30.jsonl", "eval_cases_v3.jsonl"):
        shutil.copy(HERE / "cases" / f, tmp / "01_testset" / f)
    shutil.copy(HERE / "round2_selection.json", tmp / "01_testset" / "round2_selection.json")

    # 02/03 原始请求 + 解析结果
    order = []
    for f in req_files:
        r = json.loads(f.read_text(encoding="utf-8"))
        shutil.copy(f, tmp / "02_requests_raw" / f.name)
        (tmp / "03_parsed_json" / f.name).write_text(
            json.dumps({
                "slot_index": r["slot_index"], "blind_id": r["blind_id"],
                "round": r["round"], "repeat": r["repeat"],
                "case_id": r["case_id"], "text_sha256": r["text_sha256"],
                "schema_ok": r["schema"]["schema_ok"],
                "schema_error": r["schema"],
                "length_chars": r["length_chars"],
                "analysis": r["parse"]["parsed"],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        order.append({
            "slot_index": r["slot_index"], "blind_id": r["blind_id"],
            "case_id": r["case_id"], "round": r["round"], "repeat": r["repeat"],
            "timestamp": r.get("timestamp"),
        })
    order.sort(key=lambda x: x["slot_index"])

    # 04/05 盲评 + 映射
    for f in sorted((run_dir / "blind_review").glob("*.json")):
        shutil.copy(f, tmp / "04_blind_review" / f.name)
    (tmp / "05_mapping" / "blind_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8")

    # 06 统计
    (tmp / "06_stats" / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "06_stats" / "comparison_table.md").write_text(
        comparison_md(stats, summary), encoding="utf-8")

    # 07 脱敏配置
    cfg = {
        "provider": manifest["provider"]["provider"],
        "base_url": manifest["provider"]["base_url"],
        "timeout_s": manifest["provider"]["timeout_s"],
        "api_key": "<已脱敏，不包含在本包内>",
        "model": manifest["params"]["model"],
        "temperature": manifest["params"]["temperature"],
        "max_completion_tokens": manifest["params"]["max_tokens"],
        "response_format": manifest["params"]["response_format"],
        "stream": False,
        "concurrency": manifest["params"]["concurrency"],
        "random_seed": manifest["params"]["seed"],
        "auto_json_retry": manifest["params"]["auto_json_retry"],
        "http_fallback_repost": manifest["params"]["http_error_fallback"],
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_schema_hash": ANALYSIS_SCHEMA_HASH,
        "pricing_source": "https://platform.xiaomimimo.com/docs/price/pay-as-you-go (2026-10-09)",
        "pricing_cny_per_1M": {"input_cache_miss": 3.00, "input_cache_hit": 0.025, "output": 6.00},
    }
    (tmp / "07_config" / "request_config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 08 提示词原文（非盲评材料）
    (tmp / "08_prompts_reference" / "v2_SYSTEM.txt").write_text(V2_SYSTEM, encoding="utf-8")
    (tmp / "08_prompts_reference" / "v2_SCHEMA_HINT.txt").write_text(V2_HINT, encoding="utf-8")
    (tmp / "08_prompts_reference" / "v3_SYSTEM.txt").write_text(v3.ANALYSIS_SYSTEM, encoding="utf-8")
    (tmp / "08_prompts_reference" / "v3_SCHEMA_HINT.txt").write_text(v3.ANALYSIS_SCHEMA_HINT, encoding="utf-8")
    shutil.copy(HERE / "candidate" / "analysis_v3_candidate.py",
                tmp / "08_prompts_reference" / "analysis_v3_candidate.py")

    # 09 日志
    (tmp / "09_logs" / "execution_order.jsonl").write_text(
        "\n".join(json.dumps(o, ensure_ascii=False) for o in order) + "\n", encoding="utf-8")
    (tmp / "09_logs" / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "09_logs" / "round2_plan.json").write_text(
        json.dumps({
            "executed": round2_executed,
            "planned_requests": 48 if not round2_executed else 0,
            "samples": json.loads((HERE / "round2_selection.json").read_text(encoding="utf-8"))["selection"],
            "note": ("未执行：第一轮出现系统性输出截断（见 README 与 comparison_table.md），"
                     "按预算与故障控制条款暂停，等待批准后执行。" if not round2_executed else "已执行"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # README
    (tmp / "README.md").write_text(readme(stats, summary, round2_executed), encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(tmp.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp)
    print(f"已打包 {zip_path}  ({zip_path.stat().st_size / 1024:.0f} KB)")


def comparison_md(stats: dict, summary: dict) -> str:
    a, b = stats["by_version"]["v2"], stats["by_version"]["v3"]
    t = stats["totals"]
    lines = [
        "# V2 / V3 自动统计对照表（客观计数，不含质量评分）",
        "",
        f"- run: {summary['run_id']}　请求总数 {t['requests_total']}",
        f"- 输入 token {t['tokens_input_total']:,}　输出 token {t['tokens_output_total']:,}",
        f"- 费用：保守口径 ¥{t['cost_total_conservative']:.4f}　缓存感知口径 ¥{t['cost_total_cache_aware']:.4f}",
        "",
        "| 指标 | V2 | V3 |",
        "|---|---:|---:|",
    ]
    rows = [
        ("请求数", a["n_requests"], b["n_requests"]),
        ("HTTP 成功率", a["http_success_rate"], b["http_success_rate"]),
        ("合法 JSON 比例", a["valid_json_rate"], b["valid_json_rate"]),
        ("Schema 通过率(全部请求)", a["schema_pass_rate"], b["schema_pass_rate"]),
        ("Schema 通过率(仅已解析)", a["schema_pass_rate_of_parsed"], b["schema_pass_rate_of_parsed"]),
        ("输出截断率", a["truncation_rate"], b["truncation_rate"]),
        ("length_chars 准确率", a["length_chars_accuracy"], b["length_chars_accuracy"]),
        ("平均输入 token", a["tokens"]["input_avg"], b["tokens"]["input_avg"]),
        ("平均输出 token", a["tokens"]["output_avg"], b["tokens"]["output_avg"]),
        ("平均推理 token", a["tokens"]["reasoning_avg"], b["tokens"]["reasoning_avg"]),
        ("缓存命中率", a["tokens"]["cached_ratio"], b["tokens"]["cached_ratio"]),
        ("平均耗时 s", a["latency_s"]["avg"], b["latency_s"]["avg"]),
        ("单条费用 ¥(缓存感知)", a["cost_cny"]["per_request_cache_aware"], b["cost_cny"]["per_request_cache_aware"]),
        ("每千条费用 ¥(保守)", a["cost_cny"]["per_1000_conservative"], b["cost_cny"]["per_1000_conservative"]),
    ]
    for label, x, y in rows:
        fx = f"{x:.4f}" if isinstance(x, float) else str(x)
        fy = f"{y:.4f}" if isinstance(y, float) else str(y)
        lines.append(f"| {label} | {fx} | {fy} |")
    lines += [
        "",
        "## finish_reason 分布",
        f"- V2: {a['finish_reason']}",
        f"- V3: {b['finish_reason']}",
        "",
        "## Schema 失败分类",
        f"- V2: {a['schema_fail_reasons']}",
        f"- V3: {b['schema_fail_reasons']}",
        "",
        "## 说明",
        "- 本表只做计数与费用计算，不对任何一版做质量判断。",
        "- 「截断率」= finish_reason == 'length' 的比例，即输出被 max_completion_tokens 截断。",
        "- 「Schema 通过率(仅已解析)」分母是能解析出 JSON 的请求数。",
    ]
    return "\n".join(lines)


def readme(stats: dict, summary: dict, round2_executed: bool) -> str:
    t = stats["totals"]
    a, b = stats["by_version"]["v2"], stats["by_version"]["v3"]
    return "\n".join([
        "# Content Studio · ANALYSIS V2 / V3 对照实验数据包",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"run_id：{summary['run_id']}",
        "",
        "## 实验设置",
        "- 模型 `mimo-v2.6-pro`，temperature 0.2，max_completion_tokens **1500**（两版严格相同）",
        "- response_format `json_object`，并发 4，随机种子 20261009（确定性交错顺序）",
        "- **禁用自动 JSON 重试**、**禁用 HTTP 降级重发**：每个请求槽位严格 1 次 HTTP",
        "- 提示词：V2 = 生产 `backend/app/prompts.py`；V3 = ChatGPT 交付候选 `v3-rc1`（逐字节未改）",
        "- 字段结构未变：`analysis-v2` / schema_hash `d1bec6d5…`，59 路径 54 叶子",
        "",
        "## 执行结果",
        f"- 实际请求 **{t['requests_total']}** 次（第 1 轮 96 次；第 2 轮 "
        f"{'48 次' if round2_executed else '0 次，未执行'}）",
        f"- HTTP 成功率：V2 {a['http_success_rate']:.1%} / V3 {b['http_success_rate']:.1%}",
        f"- 输入 token {t['tokens_input_total']:,}　输出 token {t['tokens_output_total']:,}",
        f"- 费用：保守 ¥{t['cost_total_conservative']:.4f}　缓存感知 ¥{t['cost_total_cache_aware']:.4f}",
        "",
        "## ⚠️ 本次实验的系统性发现",
        f"- 输出截断率：V2 {a['truncation_rate']:.1%}、V3 {b['truncation_rate']:.1%}",
        f"- 平均**推理** token：V2 {a['tokens']['reasoning_avg']:.0f}、V3 {b['tokens']['reasoning_avg']:.0f}",
        "- 说明 `mimo-v2.6-pro` 是推理模型，`max_completion_tokens=1500` **包含推理 token**，",
        "  因此留给 JSON 正文的预算被大幅挤占。这是生产参数层面的问题，不是某一版提示词的独有缺陷。",
        "- 两版受影响方向一致（V3 略高，因其提示词更长、诱导更多推理），对比本身仍然公平。",
        "",
        "## 包内结构",
        "```",
        "00_manifest/    冻结清单 + 提示词 SHA256 指纹",
        "01_testset/     48 条样本原文 + 12 条重测样本选择记录",
        "02_requests_raw/ 每个请求槽位的完整 messages、原始响应、HTTP 状态、usage、耗时",
        "03_parsed_json/  解析后的结构化 JSON（含 schema 校验结果与失败字段路径）",
        "04_blind_review/ 盲评材料（不含 V2/V3 标签）",
        "05_mapping/      揭盲映射表 blind_key.json",
        "06_stats/        自动统计 stats.json + comparison_table.md",
        "07_config/       脱敏的请求配置与定价口径",
        "08_prompts_reference/ 两版提示词原文（非盲评材料）",
        "09_logs/         执行顺序、run 摘要、第二轮计划",
        "```",
        "",
        "## 给评审方的提醒",
        "- 盲评请用 04_blind_review/，揭盲用 05_mapping/blind_key.json。",
        "- 不要用「字段填得越多越好」作质量判断。",
        "- 语义准确性、幻觉、修辞判断、证据充分性、写作价值由 ChatGPT 独立评估。",
        "- 本包不含任何 API Key 或环境变量密钥。",
    ])


if __name__ == "__main__":
    rd = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        HERE / f"analysis_v3_ab_{rd.name}.zip")
    r2 = (len(sys.argv) > 3 and sys.argv[3] == "round2_done")
    build(rd, out, r2)
