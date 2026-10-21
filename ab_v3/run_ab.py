"""ANALYSIS V2 vs V3 付费对照实验执行器。

------------------------------------------------------------------
授权与硬约束（用户 2026-10-09 明确授权）
------------------------------------------------------------------
- 授权总量 **144 次实际 API 请求**：第 1 轮 96 + 第 2 轮 48。不是每轮 144。
- 预算上限 **¥6**（目标 ¥1–3）。
- 模型 `mimo-v2.6-pro`、temperature `0.2`、max_tokens `1500`、response_format `json_object`、并发 ≤ 4。
- **两版完全同参**：禁止把 V3 单独提到 2000 token。
- **禁用自动 JSON 重试**：每个请求槽位严格 1 次 HTTP 请求。
  解析失败 / 截断 / Schema 错误都原样记录并计入结果，不为美化成功率重试。
- **HTTP 错误不静默切换模型、参数或接口**，所有实际请求计入 144 次额度。
- 失败不补调用；最终成功数可能少于 144，如实报告。

------------------------------------------------------------------
用法
------------------------------------------------------------------
    # 0 调用的配置自检 + 预算估算
    python ab_v3/run_ab.py --dry-run

    # 付费执行（需 AB_EXECUTE=1）
    AB_EXECUTE=1 python ab_v3/run_ab.py --execute --round 1
    AB_EXECUTE=1 python ab_v3/run_ab.py --execute --round 2
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.prompts import (  # noqa: E402
    ANALYSIS_SCHEMA_HINT as V2_HINT,
    ANALYSIS_SYSTEM as V2_SYSTEM,
    build_analysis_messages as v2_build,
)
from app.services.analysis_spec import (  # noqa: E402
    ANALYSIS_SCHEMA_HASH,
    spec_field_paths,
    validate_analysis_v2,
)

_spec = importlib.util.spec_from_file_location(
    "v3cand", HERE / "candidate" / "analysis_v3_candidate.py"
)
v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3)

CASES = HERE / "cases" / "merged_48.jsonl"
ROUND2 = HERE / "round2_selection.json"
RESULTS = HERE / "results"
FROZEN = HERE / "frozen_manifest.json"

# ---- 实验参数（与生产 analyzer.analyze_content 一致，两版完全相同）----
MODEL = settings.model_analysis
TEMPERATURE = 0.2
MAX_TOKENS = 1500          # 两版都 1500，禁止给 V3 提到 2000
JSON_MODE = True
CONCURRENCY = 4
SEED = 20261009
MAX_BUDGET_CNY = 6.0

# ---- 官方定价（platform.xiaomimimo.com/docs/price/pay-as-you-go，2026-10-09 核对）----
PRICE_IN = 3.00            # ¥/百万 输入 token（未命中缓存，保守口径）
PRICE_OUT = 6.00           # ¥/百万 输出 token

OBJ_KEYS = {
    "content_invariants", "rhetorical", "stance_engagement",
    "microstyle", "style_boundary",
}
LEAF_PATHS = [p for p in spec_field_paths() if p not in OBJ_KEYS]

# ---- Token 估算（仅 dry-run 用；执行时用真实 usage）----
CJK_FACTORS = (0.60, 0.70, 1.00)
ASCII_FACTORS = (0.30, 0.35, 0.45)
OUTPUT_ASSUMPTION = (450, 850, 1500)
CJK_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3000, 0x303F),
              (0xFF00, 0xFFEF), (0x20000, 0x2A6DF))


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in CJK_RANGES)


def est_tokens(text: str) -> tuple[int, int, int]:
    cjk = sum(1 for c in text if _is_cjk(c))
    other = len(text) - cjk
    return tuple(round(cjk * CJK_FACTORS[i] + other * ASCII_FACTORS[i]) for i in range(3))  # type: ignore[return-value]


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 提示词指纹

def prompt_fingerprints() -> dict:
    return {
        "v2": {
            "source": "backend/app/prompts.py",
            "prompt_version": "v2",
            "system_sha256": sha256_text(V2_SYSTEM),
            "hint_sha256": sha256_text(V2_HINT),
            "combined_sha256": sha256_text(V2_SYSTEM + "\n" + V2_HINT),
            "system_chars": len(V2_SYSTEM),
            "hint_chars": len(V2_HINT),
        },
        "v3": {
            "source": "ab_v3/candidate/analysis_v3_candidate.py",
            "prompt_version": v3.ANALYSIS_PROMPT_VERSION_CANDIDATE,
            "system_sha256": sha256_text(v3.ANALYSIS_SYSTEM),
            "hint_sha256": sha256_text(v3.ANALYSIS_SCHEMA_HINT),
            "combined_sha256": sha256_text(v3.ANALYSIS_SYSTEM + "\n" + v3.ANALYSIS_SCHEMA_HINT),
            "system_chars": len(v3.ANALYSIS_SYSTEM),
            "hint_chars": len(v3.ANALYSIS_SCHEMA_HINT),
            "candidate_file_sha256": sha256_file(HERE / "candidate" / "analysis_v3_candidate.py"),
        },
    }


def build_messages(version: str, text: str) -> list[dict[str, str]]:
    return v2_build(text) if version == "v2" else v3.build_analysis_messages(text)


# ---------------------------------------------------------------- 计划

def load_cases() -> list[dict]:
    return [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]


def build_frozen_manifest() -> dict:
    """在任何付费请求之前冻结：提示词指纹、测试集指纹、第二轮名单指纹、参数。"""
    cases = load_cases()
    r2 = json.loads(ROUND2.read_text(encoding="utf-8"))
    return {
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "authorization": {
            "max_requests": 144,
            "round1": 96,
            "round2": 48,
            "budget_cap_cny": MAX_BUDGET_CNY,
        },
        "params": {
            "model": MODEL,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"} if JSON_MODE else None,
            "concurrency": CONCURRENCY,
            "seed": SEED,
            "auto_json_retry": False,
            "http_error_fallback": False,
        },
        "schema": {
            "analysis_schema_version": "analysis-v2",
            "analysis_schema_hash": ANALYSIS_SCHEMA_HASH,
            "field_paths": len(spec_field_paths()),
            "leaf_fields": len(LEAF_PATHS),
        },
        "test_set": {
            "file": str(CASES),
            "sha256": sha256_file(CASES),
            "cases": len(cases),
            "cases_sha256": {c["id"]: sha256_text(c["text"]) for c in cases},
        },
        "round2_selection": {
            "file": str(ROUND2),
            "sha256": sha256_file(ROUND2),
            "ids": [p["case_id"] for p in r2["selection"]],
            "slots": {p["case_id"]: p["slot"] for p in r2["selection"]},
            "counts": r2["counts"],
        },
        "prompts": prompt_fingerprints(),
        "provider": {
            "base_url": settings.ai_base_url,
            "provider": settings.ai_provider,
            "timeout_s": settings.ai_timeout,
            "api_key_configured": bool(settings.ai_api_key),
        },
    }


def plan_runs(round_no: str) -> list[dict]:
    """生成请求槽位。确定性交错（种子 20261009），两版本在同一时段内交错。"""
    cases = load_cases()
    rng = random.Random(SEED)

    slots: list[dict] = []
    if round_no in ("1", "all"):
        for c in cases:
            for v in ("v2", "v3"):
                slots.append({"round": 1, "case": c, "version": v, "repeat": 0})

    if round_no in ("2", "all"):
        r2 = json.loads(ROUND2.read_text(encoding="utf-8"))
        picked_ids = [p["case_id"] for p in r2["selection"]]
        by_id = {c["id"]: c for c in cases}
        for cid in picked_ids:
            for v in ("v2", "v3"):
                for rep in (1, 2):
                    slots.append(
                        {"round": 2, "case": by_id[cid], "version": v, "repeat": rep}
                    )

    rng.shuffle(slots)

    # 盲评臂：每个 (case, repeat) 随机决定 A/B 与 v2/v3 的对应，评审看不到版本
    for s in slots:
        s["blind_id"] = hashlib.sha256(
            f"{s['case']['id']}|{s['version']}|{s['repeat']}".encode()
        ).hexdigest()[:12]
        s["arm"] = rng.choice(("A", "B"))
    return slots


# ---------------------------------------------------------------- 单次调用（严格 1 次 HTTP）

async def call_once(client, messages: list[dict[str, str]]) -> dict:
    """一次请求 = 一次 HTTP。无重试、无 response_format 降级重发。"""
    import httpx

    url = f"{settings.ai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.ai_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": TEMPERATURE,
        "stream": False,
        "max_completion_tokens": MAX_TOKENS,
    }
    if JSON_MODE:
        payload["response_format"] = {"type": "json_object"}

    t0 = time.time()
    try:
        resp = await client.post(url, json=payload, headers=headers)
        elapsed = time.time() - t0
        status = resp.status_code
        body_text = resp.text
    except Exception as err:  # noqa: BLE001 —— 网络层异常也如实记录，不重试
        elapsed = time.time() - t0
        return {
            "ok": False, "http_status": None, "error": f"{type(err).__name__}: {err}",
            "elapsed_s": round(elapsed, 2), "raw_body": None,
            "content": None, "finish_reason": None, "usage": None,
        }

    content = None
    finish_reason = None
    usage = None
    error = None
    data = None
    if status < 400:
        try:
            data = resp.json()
        except ValueError:
            error = "响应不是合法 JSON"
            data = None
        if data is not None:
            choices = data.get("choices") or []
            if not choices:
                error = "响应无 choices"
            else:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if not content:
                    content = msg.get("reasoning_content")
                if not content:
                    error = "响应内容为空"
                finish_reason = choices[0].get("finish_reason")
            usage = data.get("usage")
    else:
        error = f"HTTP {status}"

    return {
        "ok": error is None and content is not None,
        "http_status": status,
        "error": error,
        "elapsed_s": round(elapsed, 2),
        "raw_body": body_text[:20000],
        "content": content,
        "finish_reason": finish_reason,
        "usage": usage,
    }


def extract_json(text: str | None):
    import re

    if not text:
        return None, "no_content"
    t = text.strip()
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            return d, "direct"
    except json.JSONDecodeError:
        pass
    m = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL).search(t)
    if m:
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict):
                return d, "fenced"
        except json.JSONDecodeError:
            pass
    s, e = t.find("{"), t.rfind("}")
    if s != -1 and e > s:
        try:
            d = json.loads(t[s : e + 1])
            if isinstance(d, dict):
                return d, "sliced"
        except json.JSONDecodeError:
            pass
    return None, "unparsable"


def schema_check(data) -> dict:
    if not isinstance(data, dict):
        return {"schema_ok": False, "error_path": "$", "error_message": "解析结果不是对象"}
    try:
        validate_analysis_v2(data)
        return {"schema_ok": True, "error_path": None, "error_message": None}
    except Exception as err:  # noqa: BLE001
        return {
            "schema_ok": False,
            "error_path": getattr(err, "path", None),
            "error_message": getattr(err, "message", str(err)),
        }


def field_stats(data) -> dict:
    """逐字段形态统计：null / 空数组 / 非空。不做质量判断。"""
    if not isinstance(data, dict):
        return {}
    out = {}
    for p in LEAF_PATHS:
        node = data
        for part in p.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, list):
            out[p] = "empty_list" if not node else "filled"
        elif isinstance(node, str):
            out[p] = "null_or_blank" if not node.strip() else "filled"
        elif isinstance(node, int):
            out[p] = "filled"
        else:
            out[p] = "null_or_blank"
    return out


def cost_of(usage: dict | None) -> float:
    if not usage:
        return 0.0
    tin = usage.get("prompt_tokens") or 0
    tout = usage.get("completion_tokens") or 0
    return (tin / 1_000_000) * PRICE_IN + (tout / 1_000_000) * PRICE_OUT


class BudgetGuard:
    """保守预算控制：按「已发生费用 + 剩余请求 × 保守单请求费用」判断。"""

    def __init__(self, total: int) -> None:
        self.total = total
        self.done = 0
        self.spent = 0.0
        self.max_in_seen = 3500  # 无任何观测时的保守先验（V3 中位约 3100）

    def conservative_unit(self) -> float:
        return (self.max_in_seen / 1_000_000) * PRICE_IN + (MAX_TOKENS / 1_000_000) * PRICE_OUT

    def allow_next(self) -> tuple[bool, str]:
        remaining = self.total - self.done
        projected = self.spent + remaining * self.conservative_unit()
        if projected > MAX_BUDGET_CNY:
            return False, (
                f"预计总费用 ¥{projected:.2f} > 上限 ¥{MAX_BUDGET_CNY:.2f}"
                f"（已花费 ¥{self.spent:.3f}，剩余 {remaining} 次，"
                f"保守单请求 ¥{self.conservative_unit():.4f}）"
            )
        return True, ""

    def record(self, usage: dict | None) -> None:
        self.done += 1
        self.spent += cost_of(usage)
        if usage and usage.get("prompt_tokens"):
            self.max_in_seen = max(self.max_in_seen, int(usage["prompt_tokens"]))


# ---------------------------------------------------------------- 执行

async def execute(round_no: str) -> None:
    if os.getenv("AB_EXECUTE", "") != "1":
        print("拒绝执行：需设置 AB_EXECUTE=1（付费保护）")
        sys.exit(2)
    if not settings.ai_api_key:
        print("拒绝执行：未配置 AI_API_KEY")
        sys.exit(2)
    if MODEL != "mimo-v2.6-pro":
        print(f"拒绝执行：模型被改为 {MODEL}，只允许 mimo-v2.6-pro")
        sys.exit(2)

    import httpx

    manifest = build_frozen_manifest()
    slots = plan_runs(round_no)
    expected = {"1": 96, "2": 48, "all": 144}[round_no]
    assert len(slots) == expected, f"槽位数 {len(slots)} != 预期 {expected}"

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_r{round_no}"
    outdir = RESULTS / run_id
    (outdir / "requests").mkdir(parents=True, exist_ok=True)
    (outdir / "blind_review").mkdir(parents=True, exist_ok=True)
    (outdir / "_frozen_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=" * 78)
    print(f"执行第 {round_no} 轮：{len(slots)} 次请求（授权内）")
    print(f"  model={MODEL} temp={TEMPERATURE} max_tokens={MAX_TOKENS} "
          f"json_object={JSON_MODE} concurrency={CONCURRENCY}")
    print(f"  预算上限 ¥{MAX_BUDGET_CNY}｜自动 JSON 重试=关闭｜HTTP 降级重发=关闭")
    print(f"  输出目录 {outdir}")
    print("=" * 78)

    guard = BudgetGuard(len(slots))
    sem = asyncio.Semaphore(CONCURRENCY)
    stopped_reason: str | None = None
    records: list[dict] = []

    timeout = httpx.Timeout(settings.ai_timeout)
    async with httpx.AsyncClient(timeout=timeout) as client:

        async def one(idx: int, s: dict):
            async with sem:
                nonlocal stopped_reason
                if stopped_reason:
                    return None
                ok_budget, why = guard.allow_next()
                if not ok_budget:
                    stopped_reason = why
                    return None

                c = s["case"]
                msgs = build_messages(s["version"], c["text"])
                res = await call_once(client, msgs)
                data, parse_mode = extract_json(res["content"])
                sc = schema_check(data)
                fs = field_stats(data)
                lc = data.get("length_chars") if isinstance(data, dict) else None
                rec = {
                    "slot_index": idx,
                    "round": s["round"],
                    "repeat": s["repeat"],
                    "case_id": c["id"],
                    "case_category": c.get("category"),
                    "case_origin": c.get("origin"),
                    "text_len": len(c["text"]),
                    "text_sha256": sha256_text(c["text"]),
                    "text": c["text"],
                    "version": s["version"],          # 非盲评文件才含此字段
                    "blind_id": s["blind_id"],
                    "arm": s["arm"],
                    "request": {
                        "model": MODEL,
                        "temperature": TEMPERATURE,
                        "max_completion_tokens": MAX_TOKENS,
                        "response_format": {"type": "json_object"},
                        "stream": False,
                        "messages": msgs,
                    },
                    "response": {
                        "http_status": res["http_status"],
                        "error": res["error"],
                        "finish_reason": res["finish_reason"],
                        "elapsed_s": res["elapsed_s"],
                        "raw_body_excerpt": res["raw_body"],
                        "raw_content": res["content"],
                    },
                    "usage": res["usage"],
                    "cost_cny_est": round(cost_of(res["usage"]), 6),
                    "parse": {"mode": parse_mode, "parsed": data},
                    "schema": sc,
                    "field_stats": fs,
                    "length_chars": {
                        "reported": lc,
                        "actual": len(c["text"]),
                        "match": lc == len(c["text"]),
                    },
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                guard.record(res["usage"])
                (outdir / "requests" /
                 f"{idx:03d}_{c['id']}_{s['version']}_rep{s['repeat']}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  [{guard.done}/{len(slots)}] {c['id']} {s['version']} rep{s['repeat']} "
                      f"http={res['http_status']} parse={parse_mode} "
                      f"schema={'OK' if sc['schema_ok'] else 'FAIL'} "
                      f"fin={res['finish_reason']} "
                      f"tok={ (res['usage'] or {}).get('prompt_tokens') }/"
                      f"{(res['usage'] or {}).get('completion_tokens')} "
                      f"累计¥{guard.spent:.3f}")
                return rec

        results = await asyncio.gather(*[one(i, s) for i, s in enumerate(slots)])

    records = [r for r in results if r]

    # 盲评副本（不含 version 字段）
    key = {}
    for r in records:
        key[r["blind_id"]] = {
            "case_id": r["case_id"], "version": r["version"],
            "repeat": r["repeat"], "arm": r["arm"], "round": r["round"],
        }
        (outdir / "blind_review" / f"variant_{r['blind_id']}.json").write_text(
            json.dumps(
                {
                    "blind_id": r["blind_id"], "arm": r["arm"],
                    "case_id": r["case_id"], "case_category": r["case_category"],
                    "text_sha256": r["text_sha256"], "text_len": r["text_len"],
                    "round": r["round"], "repeat": r["repeat"],
                    "schema_ok": r["schema"]["schema_ok"],
                    "field_stats": r["field_stats"],
                    "analysis": r["parse"]["parsed"],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    (outdir / "_blind_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "run_id": run_id,
        "round": round_no,
        "planned_requests": len(slots),
        "executed_requests": len(records),
        "stopped_reason": stopped_reason,
        "spent_cny": round(guard.spent, 4),
        "budget_cap_cny": MAX_BUDGET_CNY,
        "frozen_manifest": manifest,
    }
    (outdir / "_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"第 {round_no} 轮结束：执行 {len(records)}/{len(slots)} 次，"
          f"累计费用 ¥{guard.spent:.4f}")
    if stopped_reason:
        print(f"⚠️ 预算保护触发，提前停止：{stopped_reason}")
    print(f"结果目录：{outdir}")


# ---------------------------------------------------------------- dry-run

def dry_run() -> None:
    manifest = build_frozen_manifest()
    print("=" * 78)
    print("配置自检 + 预算估算（0 API 调用）")
    print("=" * 78)
    p = manifest["params"]
    pr = manifest["provider"]
    print(f"model={p['model']}  temperature={p['temperature']}  "
          f"max_tokens={p['max_tokens']}  json_object={p['response_format'] is not None}")
    print(f"并发={p['concurrency']}  种子={p['seed']}  "
          f"自动JSON重试={p['auto_json_retry']}  HTTP降级重发={p['http_error_fallback']}")
    print(f"端点={pr['base_url']}  provider={pr['provider']}  "
          f"timeout={pr['timeout_s']}s  API Key 已配置={pr['api_key_configured']}")
    print(f"schema: {manifest['schema']['analysis_schema_version']} "
          f"hash={ANALYSIS_SCHEMA_HASH[:16]}… "
          f"{manifest['schema']['field_paths']} 路径 / {manifest['schema']['leaf_fields']} 叶子")
    print()
    print("--- 提示词指纹 ---")
    for v in ("v2", "v3"):
        f = manifest["prompts"][v]
        print(f"  {v}: prompt_version={f['prompt_version']}  "
              f"system={f['system_chars']}字  hint={f['hint_chars']}字")
        print(f"      system_sha256={f['system_sha256'][:16]}…")
        print(f"      hint_sha256  ={f['hint_sha256'][:16]}…")
        print(f"      combined     ={f['combined_sha256'][:16]}…")
    print()
    print("--- 测试集 ---")
    print(f"  {manifest['test_set']['cases']} 条  文件 sha256={manifest['test_set']['sha256'][:16]}…")
    print(f"  第二轮 12 条：{manifest['round2_selection']['ids']}")
    print(f"  类别计数：{manifest['round2_selection']['counts']}")
    print()
    for r in ("1", "2", "all"):
        slots = plan_runs(r)
        n_v2 = sum(1 for s in slots if s["version"] == "v2")
        in_chars = sum(
            len(build_messages(s["version"], s["case"]["text"])[0]["content"])
            + len(build_messages(s["version"], s["case"]["text"])[1]["content"])
            for s in slots
        )
        print(f"  第 {r} 轮：{len(slots)} 次（v2 {n_v2} / v3 {len(slots)-n_v2}）"
              f"  输入字符合计 {in_chars:,}")

    cases = load_cases()
    r1 = plan_runs("1")
    r2 = plan_runs("2")
    ch = {"v2": 0, "v3": 0}
    for s in r1 + r2:
        m = build_messages(s["version"], s["case"]["text"])
        ch[s["version"]] += len(m[0]["content"]) + len(m[1]["content"])
    tok = [0, 0, 0]
    for s in r1 + r2:
        m = build_messages(s["version"], s["case"]["text"])
        e = est_tokens(m[0]["content"] + m[1]["content"])
        for i in range(3):
            tok[i] += e[i]
    print()
    print("--- 预算估算（144 次，保守：全部按未命中缓存）---")
    for i, tag in enumerate(("低", "中", "高")):
        out_tok = 144 * OUTPUT_ASSUMPTION[i]
        cost = (tok[i] / 1_000_000) * PRICE_IN + (out_tok / 1_000_000) * PRICE_OUT
        print(f"  [{tag}档] 输入 {tok[i]:,} tok / 输出 {out_tok:,} tok → ¥{cost:.2f}")
    print(f"  预算上限 ¥{MAX_BUDGET_CNY:.2f}")


async def resume(run_dir: Path) -> None:
    """断点续跑：只执行该 run 目录中**尚未落盘**的槽位，绝不重复请求。

    进程被环境超时中断时使用；已完成的请求文件原样保留。
    """
    if os.getenv("AB_EXECUTE", "") != "1":
        print("拒绝执行：需设置 AB_EXECUTE=1")
        sys.exit(2)
    if not settings.ai_api_key:
        print("拒绝执行：未配置 AI_API_KEY")
        sys.exit(2)

    import httpx
    import re as _re

    manifest = json.loads((run_dir / "_frozen_manifest.json").read_text(encoding="utf-8"))
    round_no = run_dir.name.rsplit("_r", 1)[-1]
    slots = plan_runs(round_no)

    done: set[tuple[str, int]] = set()
    max_idx = -1
    spent = 0.0
    max_in = 3500
    for f in (run_dir / "requests").glob("*.json"):
        m = _re.match(r"(\d+)_(.+)_(v2|v3)_rep(\d+)\.json", f.name)
        if not m:
            continue
        max_idx = max(max_idx, int(m.group(1)))
        done.add((f"{m.group(2)}|{m.group(3)}", int(m.group(4))))
        rec = json.loads(f.read_text(encoding="utf-8"))
        u = rec.get("usage") or {}
        spent += cost_of(u)
        if u.get("prompt_tokens"):
            max_in = max(max_in, int(u["prompt_tokens"]))

    remaining = [
        s for s in slots
        if (f"{s['case']['id']}|{s['version']}", s["repeat"]) not in done
    ]
    print(f"已完成 {len(done)} 个槽位，剩余 {len(remaining)} 个待执行（run={run_dir.name}）")
    if not remaining:
        print("无需续跑")
        return

    guard = BudgetGuard(len(slots))
    guard.done = len(done)
    guard.spent = spent
    guard.max_in_seen = max_in
    print(f"已发生费用 ¥{spent:.4f}／上限 ¥{MAX_BUDGET_CNY:.2f}")

    sem = asyncio.Semaphore(CONCURRENCY)
    idx = max_idx + 1
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.ai_timeout)) as client:

        async def one(i: int, s: dict):
            async with sem:
                ok_b, why = guard.allow_next()
                if not ok_b:
                    print(f"⚠️ 预算保护：{why}")
                    return
                c = s["case"]
                msgs = build_messages(s["version"], c["text"])
                res = await call_once(client, msgs)
                data, mode = extract_json(res["content"])
                sc = schema_check(data)
                rec = {
                    "slot_index": i, "round": s["round"], "repeat": s["repeat"],
                    "case_id": c["id"], "case_category": c.get("category"),
                    "case_origin": c.get("origin"), "text_len": len(c["text"]),
                    "text_sha256": sha256_text(c["text"]), "text": c["text"],
                    "version": s["version"], "blind_id": s["blind_id"], "arm": s["arm"],
                    "request": {
                        "model": MODEL, "temperature": TEMPERATURE,
                        "max_completion_tokens": MAX_TOKENS,
                        "response_format": {"type": "json_object"}, "stream": False,
                        "messages": msgs,
                    },
                    "response": {
                        "http_status": res["http_status"], "error": res["error"],
                        "finish_reason": res["finish_reason"],
                        "elapsed_s": res["elapsed_s"],
                        "raw_body_excerpt": res["raw_body"],
                        "raw_content": res["content"],
                    },
                    "usage": res["usage"],
                    "cost_cny_est": round(cost_of(res["usage"]), 6),
                    "parse": {"mode": mode, "parsed": data},
                    "schema": sc, "field_stats": field_stats(data),
                    "length_chars": {
                        "reported": data.get("length_chars") if isinstance(data, dict) else None,
                        "actual": len(c["text"]),
                        "match": isinstance(data, dict) and data.get("length_chars") == len(c["text"]),
                    },
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                guard.record(res["usage"])
                (run_dir / "requests" /
                 f"{i:03d}_{c['id']}_{s['version']}_rep{s['repeat']}.json").write_text(
                    json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"  [{guard.done}/{len(slots)}] {c['id']} {s['version']} rep{s['repeat']} "
                      f"http={res['http_status']} parse={mode} "
                      f"schema={'OK' if sc['schema_ok'] else 'FAIL'} "
                      f"fin={res['finish_reason']} 累计¥{guard.spent:.3f}")

        await asyncio.gather(*[one(idx + k, s) for k, s in enumerate(remaining)])

    # 用全部文件重算盲评副本与摘要
    finalize_run_dir(run_dir, manifest, round_no)
    print(f"\n续跑结束：目录 {run_dir}")


def finalize_run_dir(run_dir: Path, manifest: dict, round_no: str) -> None:
    """从 requests/ 下全部文件重算盲评副本、映射表与 run 摘要。"""
    files = sorted((run_dir / "requests").glob("*.json"))
    key: dict[str, dict] = {}
    spent = 0.0
    tin = tout = 0
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        spent += r.get("cost_cny_est") or 0.0
        u = r.get("usage") or {}
        tin += u.get("prompt_tokens") or 0
        tout += u.get("completion_tokens") or 0
        key[r["blind_id"]] = {
            "case_id": r["case_id"], "version": r["version"],
            "repeat": r["repeat"], "arm": r["arm"], "round": r["round"],
        }
        (run_dir / "blind_review" / f"variant_{r['blind_id']}.json").write_text(
            json.dumps(
                {
                    "blind_id": r["blind_id"], "arm": r["arm"],
                    "case_id": r["case_id"], "case_category": r["case_category"],
                    "text_sha256": r["text_sha256"], "text_len": r["text_len"],
                    "round": r["round"], "repeat": r["repeat"],
                    "schema_ok": r["schema"]["schema_ok"],
                    "field_stats": r["field_stats"],
                    "analysis": r["parse"]["parsed"],
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
    (run_dir / "_blind_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "_run_summary.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name, "round": round_no,
                "executed_requests": len(files),
                "total_prompt_tokens": tin, "total_completion_tokens": tout,
                "spent_cny": round(spent, 4), "budget_cap_cny": MAX_BUDGET_CNY,
                "frozen_manifest": manifest,
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", default=None, help="续跑指定 run 目录（只跑缺失槽位）")
    ap.add_argument("--round", default="all", choices=["1", "2", "all"])
    args = ap.parse_args()
    if args.resume:
        asyncio.run(resume(Path(args.resume)))
    elif args.execute:
        asyncio.run(execute(args.round))
    else:
        dry_run()


if __name__ == "__main__":
    main()
