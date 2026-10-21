"""内容分析流水线：调用 AI 生成结构化分析并落库。

------------------------------------------------------------------
任务 04-A 的三条纪律
------------------------------------------------------------------
1. **严格校验**：模型输出必须通过 services/analysis_spec.validate_analysis()
   才允许落库。缺字段 / 多字段 / 枚举值不合法一律拒收 ——
   不允许「先存成 analyzed、下游读不到再说」。
   这条纪律对 Realtime / Batch / 外部分析**完全一致**（旧版只有外部分析校验）。
2. **截断即失败**：mimo-v2.6-pro 是推理模型，max_completion_tokens 包含
   reasoning_tokens。撞到上限时 HTTP 仍是 200、内容也是字符串，
   但 JSON 是半截的。因此必须看 finish_reason，把它当作明确失败而不是
   「解析不出来先跳过」。
3. **字数由本地计算**：length_chars 不进模型合同，由 compute_length_chars()
   在落库前算好。模型既不该数数，也不该有机会数错。
"""

from __future__ import annotations

import logging
from typing import Any

from app import repository as repo
from app.ai import get_provider
from app.ai.base import AIError, AIProvider, extract_json
from app.config import (
    ANALYSIS_DETECT_TRUNCATION,
    ANALYSIS_MAX_ATTEMPTS,
    settings,
)
from app.db import get_conn
from app.prompts import build_analysis_messages
from app.services.analysis_spec import SpecError, validate_analysis
from app.services.persistence import persist_analysis_result

logger = logging.getLogger("content_studio.analyzer")

#: 判定为「被上限截断」的 finish_reason
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})


def compute_length_chars(raw_text: str) -> int:
    """本机计算正文字数（去掉首尾空白后的字符数）。

    刻意不在模型合同里放 length_chars：模型数数不可靠，而这是纯客观量。
    """
    return len((raw_text or "").strip())


class AnalysisContractError(AIError):
    """模型输出不符合 ANALYSIS 合同（缺字段 / 多字段 / 枚举越界 / 非法 JSON）。"""


def validate_model_output(data: Any) -> dict[str, Any]:
    """校验模型输出并返回**可直接落库**的合同对象。

    合同对象原样保留（存进 analysis_json 的就是这一份，不含本地计算字段）；
    length_chars 只落到 analyses.length_chars 这一列，
    `repo.get_analysis()` 读取时再合并出来。
    """
    validate_analysis(data)
    return data


def _truncation_error(meta: dict[str, Any], model: str) -> AnalysisContractError:
    """截断失败。**不引用任何本地配额常量** —— 我们根本没设上限。

    请求里没有 max_completion_tokens，所以撞顶只可能是服务端判定
    （例如上下文与最大输出之和越界）。报出真实用量，让日志自己说话：
    被截的是谁、截在多少 token、其中推理占多少。
    """
    usage = meta.get("usage") or {}
    detail = usage.get("completion_tokens_details") or {}
    return AnalysisContractError(
        f"分析输出被截断（模型 {model}，"
        f"finish_reason={meta.get('finish_reason')}，"
        f"completion_tokens={usage.get('completion_tokens')}，"
        f"reasoning_tokens={detail.get('reasoning_tokens')}）。"
        f"请求未指定 max_completion_tokens，使用的是供应商默认上限。",
        retryable=True,
    )


def _contract_error(err: SpecError) -> AnalysisContractError:
    return AnalysisContractError(
        f"分析结果不符合 ANALYSIS 合同：{err}",
        retryable=False,
    )


def _correction_messages(
    messages: list[dict[str, str]], previous: str, reason: str
) -> list[dict[str, str]]:
    """构造一次「带着具体错误」的纠正请求（只用于结构性问题）。"""
    return messages + [
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                f"上一次输出不可接受：{reason}\n"
                "请重新输出**完整**的 JSON 对象：根对象的每个字段都必须存在，"
                "嵌套对象的每个键都必须存在，"
                "分类字段只能使用 ANALYSIS_SCHEMA_HINT 中列出的枚举词，"
                "不要新增字段，不要输出解释文字或 Markdown。"
            ),
        },
    ]


async def request_analysis(
    provider: AIProvider,
    raw_text: str,
    *,
    model: str,
    max_tokens: int | None = None,
    max_attempts: int = ANALYSIS_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """调用模型并返回**已通过严格校验**的 ANALYSIS 对象。

    max_tokens 默认为 None：**不发送完成 Token 上限，使用供应商默认值**
    （mimo-v2.6-pro 为 131072）。只有测试或未来的显式需求才会传入。

    重试策略（有界、且区分原因）：
      - 被截断            → 立刻抛出（同参数重发只会再截断一次，浪费预算）；
        由上层队列决定是否整条重试（推理长度逐次不同，重试有意义）。
      - 解析失败 / 合同不符 → 用「具体错在哪」的纠正消息再试一次；
        仍然失败则抛出，绝不把半成品当成分析成功。
    """
    messages = build_analysis_messages(raw_text)
    last_err: AnalysisContractError | None = None

    for attempt in range(max(1, max_attempts)):
        text, meta = await provider.chat_with_meta(
            messages,
            model=model,
            max_tokens=max_tokens,
            json_mode=True,
        )

        if ANALYSIS_DETECT_TRUNCATION and (
            meta.get("finish_reason") in TRUNCATED_FINISH_REASONS
        ):
            raise _truncation_error(meta, model)

        # 每次调用都留下 finish_reason / 用量 —— 验收与排障都靠它，
        # 不能只在失败分支才有日志（否则「为什么这条变短了」无从查证）。
        usage = meta.get("usage") or {}
        detail = usage.get("completion_tokens_details") or {}
        logger.info(
            "分析调用完成：model=%s attempt=%s finish_reason=%s "
            "completion_tokens=%s reasoning_tokens=%s",
            model, attempt + 1, meta.get("finish_reason"),
            usage.get("completion_tokens"), detail.get("reasoning_tokens"),
        )

        try:
            data = extract_json(text)
        except AIError as err:
            last_err = AnalysisContractError(str(err), retryable=False)
            messages = _correction_messages(messages, text, "输出不是合法 JSON 对象。")
            continue

        try:
            validate_analysis(data)
        except SpecError as err:
            last_err = _contract_error(err)
            messages = _correction_messages(messages, text, f"字段校验失败：{err}")
            continue

        if attempt:
            logger.info("分析在第 %s 次尝试后通过合同校验", attempt + 1)
        return data

    assert last_err is not None
    raise last_err


async def analyze_content(content_id: int) -> None:
    """分析单条内容：取原文 → AI 分析 → 严格校验 → 落库。

    失败时**不写状态**，直接抛出异常；由调用方（后台队列）根据错误是否可恢复
    决定重试还是标记 failed。这样「重试期间」内容会保持 processing，
    而不是被过早标记为永久失败。
    """
    provider = get_provider()

    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            return
        raw_text = row["raw_text"]

    data = await request_analysis(provider, raw_text, model=settings.model_analysis)
    length_chars = compute_length_chars(raw_text)

    with get_conn(settings.db_path) as conn:
        # 落库统一走 services.persistence —— 与 Batch、外部分析导入**完全同一套**
        # 副作用（save_analysis / tags / summary / 状态推进）。
        persist_analysis_result(
            conn,
            content_id,
            data,
            raw=data,
            model=settings.model_analysis,
            raw_text=raw_text,
        )
        # length_chars 是本地计算量，不在模型合同里，单独落到索引列
        conn.execute(
            "UPDATE analyses SET length_chars = ? WHERE content_id = ?",
            (length_chars, int(content_id)),
        )
