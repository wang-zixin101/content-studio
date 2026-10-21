"""完成 Token 上限检查：四条 AI 调用链的实际请求参数。

用途
----
证明「没有任何人为的输出 Token 上限」。做法不是读代码，而是**拦截真实的
HTTP 请求体**，把四条链真正发出去的 payload 打出来逐项核对：

    ① ANALYSIS（Realtime）
    ② WRITE 单稿
    ③ WRITE 三候选
    ④ STYLE 风格档案
    ⑤ Batch JSONL（同一套 ANALYSIS 提示词的第二条通道）

依据（小米 MiMo 开放平台官方 OpenAI 兼容文档）：
    max_completion_tokens  integer | null  可选
      mimo-v2.6-pro: default 131072（即该模型最大输出）
      Required range: [1, 131072]
    https://platform.xiaomimimo.com/docs/en-US/api/chat/openai-api

**不调用任何付费模型**：httpx 客户端被替换成记录型假客户端，
请求不出本机。

用法
----
    cd backend
    ./.venv/Scripts/python.exe ../check_output_limit.py
    ./.venv/Scripts/python.exe ../check_output_limit.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

FORBIDDEN = ("max_completion_tokens", "max_tokens", "max_output_tokens",
             "max_new_tokens", "maxTokens")


def make_temp_db() -> Path:
    path = Path(tempfile.mkdtemp(prefix="cs_limitcheck_")) / "check.db"
    os.environ["DB_PATH"] = str(path)
    import app.config as config_module

    config_module.settings.db_path = path
    return path


class Recorder:
    """记录型假 httpx 客户端：捕获 payload，不发任何网络请求。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def factory(self, body: str):
        """chat/completions 用：返回固定内容。"""
        calls = self.calls

        class FakeResponse:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": body}}
                    ],
                    "usage": {"completion_tokens": 10},
                }

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, headers=None, **kw):
                calls.append({"method": "POST", "url": url, "payload": json})
                return FakeResponse()

        return FakeClient

    def batch_factory(self):
        """Batch 用：记录 HTTP 方法 / URL / JSON body，不校验响应语义。

        返回结构只要能支撑调用链继续走到下一步即可 —— 我们要的是**路径**，
        不是 Batch 业务结果（且绝不调用付费 Batch）。
        """
        calls = self.calls

        class BatchResponse:
            status_code = 200
            text = "{}"

            def __init__(self, body: dict):
                self._body = body

            def json(self):
                return self._body

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None, files=None,
                           data=None, **kw):
                calls.append({"method": "POST", "url": url, "payload": json})
                if url.endswith("/files"):
                    return BatchResponse({"id": "file_audit"})
                return BatchResponse({"id": "batch_audit", "status": "validating"})

            async def get(self, url, headers=None, **kw):
                calls.append({"method": "GET", "url": url, "payload": None})
                if url.endswith("/content"):
                    return BatchResponse({})
                return BatchResponse({"id": "batch_audit", "status": "validating"})

        return FakeClient


def get_batch_provider_for_audit():
    """构造 Batch provider（只为捕获路径；未配置端点时返回 None）。"""
    from app.ai.factory import get_batch_provider

    return get_batch_provider()


def audit(label: str, calls: list[dict]) -> dict:
    """对一次链路的全部请求做上限审计。"""
    violations: list[str] = []
    for i, call in enumerate(calls, 1):
        payload = call["payload"] or {}
        for key in FORBIDDEN:
            if key in payload:
                violations.append(f"第 {i} 次请求带了 {key}={payload[key]}")
    keys = sorted(calls[0]["payload"]) if calls else []
    return {
        "label": label,
        "requests": len(calls),
        "endpoint": calls[0]["url"] if calls else None,
        "payload_keys": keys,
        "violations": violations,
        "pass": bool(calls) and not violations,
    }


def sample_analysis(text: str = "检查用正文。") -> dict:
    """一份能通过严格校验最小 ANALYSIS 结果（只为驱动请求，不落库）。

    刻意不 import tests.test_regression —— 那个模块在 import 期会重定向
    settings.db_path，把本脚本的隔离库换掉。
    """
    return {
        "topic": "检查",
        "subtopic": None,
        "content_type": "观点表达",
        "core_message": text[:40],
        "tags": [],
        "keywords": [],
        "content": {
            "claims": [{"kind": "作者判断", "text": "示例主张", "evidence": text[:4]}],
            "must_preserve": [],
            "context_limits": [],
        },
        "writing": {
            "opening_type": "直接陈述",
            "structure_type": "单句直述",
            "ending_type": "自然结束",
            "moves": [],
        },
        "style": {
            "tone": "中性",
            "register": "口语",
            "directness": "直接",
            "certainty": "明确肯定",
            "rhythm": "长短交替",
            "markers": [],
            "distinctive_phrases": [],
        },
        "reuse": {"patterns": [], "boundaries": []},
    }


def candidate_reply() -> str:
    """三段候选的标准输出格式。"""
    return (
        "<CANDIDATE_A>\nA 稳妥版正文。\n</CANDIDATE_A>\n\n"
        "<CANDIDATE_B>\nB 强钩子版正文。\n</CANDIDATE_B>\n\n"
        "<CANDIDATE_C>\nC 重构版正文。\n</CANDIDATE_C>"
    )


async def run(json_out: Path | None = None) -> int:
    make_temp_db()

    from app.ai import get_provider
    from app.config import settings
    from app.db import get_conn, init_db
    from app.services import rewrite_candidates, rewriter
    from app.services.analyzer import request_analysis
    from app.services.style_profile import recompute_style_profile

    init_db(settings.db_path)
    settings.ai_api_key = "sk-limitcheck"      # 只为通过「已配置」校验
    settings.ai_base_url = "https://api.xiaomimimo.com/v1"
    # Batch 端点只为核查**路径**：用假 key，且 httpx 被替换掉，不会发出请求
    if not settings.ai_batch_base_url:
        settings.ai_batch_base_url = "https://batch-api-cn.xiaomimimo.com/v1"
    settings.ai_batch_api_key = "sk-limitcheck-batch"
    from app.ai.factory import reset_provider

    reset_provider()

    import unittest.mock as mock

    import app.repository as repo

    rec = Recorder()
    results: list[dict] = []

    # ---------------- ① ANALYSIS（Realtime）----------------
    rec.calls.clear()
    with mock.patch("app.ai.openai_compat.httpx.AsyncClient", rec.factory(
        json.dumps(sample_analysis("检查用正文。"), ensure_ascii=False)
    )):
        await request_analysis(get_provider(), "检查用正文。", model=settings.model_analysis)
    results.append(audit("① ANALYSIS（Realtime）", list(rec.calls)))

    # ---------------- ② WRITE 单稿 ----------------
    rec.calls.clear()
    with mock.patch("app.ai.openai_compat.httpx.AsyncClient",
                    rec.factory("一段初稿正文。")):
        await rewriter.generate_draft(source_text="一段素材正文。")
    results.append(audit("② WRITE 单稿", list(rec.calls)))

    # ---------------- ③ WRITE 三候选 ----------------
    rec.calls.clear()
    with mock.patch("app.ai.openai_compat.httpx.AsyncClient",
                    rec.factory(candidate_reply())):
        try:
            await rewrite_candidates.generate_candidates(source_text="一段素材正文。")
        except Exception:  # noqa: BLE001 —— payload 已捕获，解析结果不影响审计
            pass
    results.append(audit("③ WRITE 三候选", list(rec.calls)))

    # ---------------- ④ STYLE ----------------
    with get_conn(settings.db_path) as conn:
        for i in range(3):
            cid = repo.create_content(
                conn, "mine", f"我的第 {i} 条内容，长度足够用于风格档案。",
                style_eligible=True,
            )
            repo.set_provenance(conn, cid, "human_original")
            data = sample_analysis("我的内容")
            repo.save_analysis(conn, cid, data, "t", raw=data)
            repo.set_analysis_status(conn, cid, "analyzed")

    rec.calls.clear()
    with mock.patch("app.ai.openai_compat.httpx.AsyncClient", rec.factory("{}")):
        with get_conn(settings.db_path) as conn:
            try:
                await recompute_style_profile(conn)
            except Exception:  # noqa: BLE001
                pass
    results.append(audit("④ STYLE 风格档案", list(rec.calls)))

    # ---------------- ⑤ Batch：请求体 + 三类路径分开核查 ----------------
    #
    # 官方文档（mimo.mi.com/docs/.../batch-api）明确三者是**分开**的概念，
    # 不能机械拼成一个 HTTP URL：
    #   1. API Base URL          https://batch-api-cn.xiaomimimo.com/v1
    #   2. JSONL 每行 url        /v1/chat/completions
    #   3. 创建批次的 endpoint   /v1/chat/completions
    # 真实 HTTP 调用走的是 {base}/batches、{base}/files 等。
    from app.services import batch as batch_svc

    payload_bytes, _ = batch_svc.build_jsonl([(1, "一段待分析内容")])
    jsonl_record = (
        json.loads(payload_bytes.decode("utf-8").strip().splitlines()[0])
        if payload_bytes else {}
    )
    jsonl_body = jsonl_record.get("body", {})
    violations = [f"带了 {k}={jsonl_body[k]}" for k in FORBIDDEN if k in jsonl_body]
    results.append(
        {
            "label": "⑤ Batch JSONL 请求体（ANALYSIS 第二通道）",
            "requests": 1,
            "endpoint": jsonl_record.get("url"),
            "payload_keys": sorted(jsonl_body),
            "violations": violations,
            "pass": bool(jsonl_body) and not violations,
        }
    )

    # 真实 HTTP 路径：用记录型假客户端捕获（不调用付费 Batch）
    rec.calls.clear()
    with mock.patch("app.ai.openai_compat.httpx.AsyncClient", rec.batch_factory()):
        prov = get_batch_provider_for_audit()
        if prov is not None:
            try:
                await prov.upload_batch_file("x.jsonl", b'{"a":1}\n')
            except Exception:  # noqa: BLE001
                pass
            try:
                await prov.create_batch("file_1")
            except Exception:  # noqa: BLE001
                pass
            try:
                await prov.get_batch("batch_1")
            except Exception:  # noqa: BLE001
                pass
            try:
                await prov.cancel_batch("batch_1")
            except Exception:  # noqa: BLE001
                pass
            try:
                await prov.download_file("file_1")
            except Exception:  # noqa: BLE001
                pass
    batch_paths = [
        {
            "method": c.get("method"),
            "url": c["url"],
            "endpoint_field": (c["payload"] or {}).get("endpoint"),
        }
        for c in rec.calls
    ]
    for p in batch_paths:
        print(f"    [Batch 实际 HTTP] {p['method']:>4} {p['url']}"
              + (f"  endpoint={p['endpoint_field']!r}" if p["endpoint_field"] else ""))

    # ---------------- 输出 ----------------
    passed = sum(1 for r in results if r["pass"])
    print(f"完成 Token 上限检查：{passed}/{len(results)} 通过\n")
    print(f"{'链路':<34} {'请求数':>5}  payload 键")
    print("-" * 100)
    for r in results:
        mark = "✓" if r["pass"] else "✗"
        print(f"{mark} {r['label']:<32} {r['requests']:>5}  "
              f"{', '.join(r['payload_keys']) or '(无)'}")
        if r["violations"]:
            for v in r["violations"]:
                print(f"    ✗ {v}")
    print()
    print("被禁止出现的键：", ", ".join(FORBIDDEN))
    for r in results[:4]:
        print(f"\n{r['label']} 实际 payload（节选，messages 已省略）：")
        print("  endpoint:", r["endpoint"])
        print("  keys    :", sorted(r["payload_keys"]))

    print("\n" + "=" * 100)
    print("Batch 三类路径（官方文档要求分开，不能机械拼接）：")
    print(f"  ① API Base URL        ：{settings.ai_batch_base_url}")
    print(f"  ② JSONL 每行 url      ：{jsonl_record.get('url')!r}")
    print(f"  ③ 创建批次的 endpoint ："
          f"{next((p['endpoint_field'] for p in batch_paths if p['endpoint_field']), None)!r}")
    print("  ④ 真实 HTTP 调用：")
    for p in batch_paths:
        print(f"       {p['method']:>4} {p['url']}")

    if json_out is not None:
        json_out.write_text(
            json.dumps(
                {
                    "requests": results,
                    "batch_paths": {
                        "api_base_url": settings.ai_batch_base_url,
                        "jsonl_line_url": jsonl_record.get("url"),
                        "create_endpoint_field": next(
                            (p["endpoint_field"] for p in batch_paths
                             if p["endpoint_field"]), None
                        ),
                        "http_calls": batch_paths,
                    },
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n已写出：{json_out}")

    return 0 if passed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json", metavar="PATH", default=None,
        help="把审计结果写成 JSON（默认只打印到终端）",
    )
    args = parser.parse_args()
    out = Path(args.json) if args.json else None
    return asyncio.run(run(out))


if __name__ == "__main__":
    sys.exit(main())
