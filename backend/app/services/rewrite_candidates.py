"""三候选生成（阶段 10）：一次 WRITE AI → A / B / C 三个候选。

------------------------------------------------------------------
AI 调用边界（硬）
------------------------------------------------------------------
    抽素材          0
    选择参考作者    0
    加载各层画像    0
    Context Selector 0
    **生成候选      恰好 1 次 provider.chat()**
    采用 / 保存     0

解析失败**绝不**自动重试第二次 AI —— 直接报错，由用户重按生成。

------------------------------------------------------------------
无状态
------------------------------------------------------------------
本函数**不写任何表**：候选只是本轮临时结果，用户还没选择。
不新增 rewrite_candidates / candidate_status / draft_version 表。
用户点「采用」时才由 API 层建一条正式 rewrite 记录（见 api 层）。

------------------------------------------------------------------
缺什么跳什么
------------------------------------------------------------------
参考作者没有 Writing / Performance 画像 → 跳过，照常生成。
素材没有 ANALYSIS → 跳过，**绝不自动补分析**。

------------------------------------------------------------------
与单稿的关系（任务 04-A 修 D4）
------------------------------------------------------------------
两个出口**共用** services/write_context.build_write_context() 与
prompts 的资料组装器：资料集合完全相同，差别只在最后一段任务描述。
旧版本模块不接收 reference_profile，导致 `_WRITE_BASE` 中整段
REFERENCE_AUTHOR_PROFILE 规则在这个出口永不触发。
"""

from __future__ import annotations

from typing import Any

from app import repository as repo
from app.ai import get_provider
from app.config import WRITE_PROMPT_VERSION, settings
from app.db import get_conn
from app.prompts import (
    CANDIDATE_ROLES,
    build_candidate_write_messages,
    parse_candidates,
)
from app.services import personalization, retriever
from app.services.creation_path import AUTO_TOPIC_PATHS, resolve_creation_path
from app.services.errors import NotFoundError, ServiceError
from app.services.rewrite_strategy import (
    context_is_empty,
    tags_for_role,
)
# 04-C 验收修复 ①：非 COLD_START 分支调用 usable_style_profile(conn)，
# 但本模块**从未导入**它 —— 只要 personalization 进入 LEARNING / PERSONALIZED，
# generate_candidates() 就会抛 NameError。三候选此前只在 COLD_START 下被测试过，
# 所以这个缺陷一直没暴露。
from app.services.style_profile import usable_style_profile
from app.services.rewriter import (
    DEFAULT_EXAMPLES,
    EXAMPLE_CHARS,
    _AUTO_TOPIC_PLACEHOLDER,
    _extract_edit_preferences,
    _resolve_reference_profile,
    reference_profile_requested,
)
from app.services.write_context import build_write_context

ROLE_LABEL = {key: label for key, label, _desc in CANDIDATE_ROLES}


# ---------------------------------------------------------------- 生成

async def generate_candidates(
    *,
    # 04-C：author_auto 下没有素材文本（与单稿同一条纪律，见 rewriter 注释）
    source_text: str | None = None,
    source_content_id: int | None = None,
    instruction: str | None = None,
    reference_author_id: int | None = None,
    use_reference_profile: bool | None = None,
    goal: str | None = None,
    k: int = DEFAULT_EXAMPLES,
    creation_path: str | None = None,
) -> dict[str, Any]:
    """一次 WRITE AI 生成 3 个候选。**无状态：不写任何表**。

    `use_reference_profile` 三态，与单稿完全一致（04-C 验收修复 ②）：
      None（默认）→ 选了作者就用其**可用**写作画像，缺则跳过并记录；
      True        → 显式要求必须用，缺画像时明确报错；
      False       → 明确不用（仅记录来源）。
    参考作者画像一律由**服务端**按 author_id 读取，前端传不进来画像内容。

    任务 04-C：与单稿共用 resolve_creation_path()，支持五种创作输入路径；
    author_auto 下没有素材文本，由 AI 自主选题，仍然是**恰好 1 次** AI。
    """
    source_text = (source_text or "").strip()

    # 只传 content_id 也算子有素材（与单稿同一条纪律，见 rewriter 注释）
    if not source_text and source_content_id is not None:
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, source_content_id)
        if row is None:
            raise NotFoundError(f"内容 {source_content_id} 不存在")
        source_text = (row["raw_text"] or "").strip()

    # 路径先于素材校验：只有 author_auto 允许素材为空
    path = resolve_creation_path(
        creation_path=creation_path,
        has_source=bool(source_text),
        use_reference_profile=use_reference_profile,
        reference_author_id=reference_author_id,
    )
    if not source_text:
        source_text = _AUTO_TOPIC_PLACEHOLDER

    with get_conn(settings.db_path) as conn:
        state_info = personalization.resolve_state(conn)
        state = state_info["state"]

        # ---- 素材已有 ANALYSIS 就复用，没有就跳过（绝不自动分析）----
        # get_analysis 返回**完整**合同对象，content.must_preserve 等约束都在里面，
        # 不再需要单独取 invariants。
        source_analysis = (
            repo.get_analysis(conn, source_content_id) if source_content_id else None
        )

        # ---- 参考作者画像：与单稿**完全一致**的三态语义 ----
        if use_reference_profile is True and reference_author_id is None:
            raise ServiceError("启用作者写作画像时必须提供参考作者")

        if reference_author_id is not None and reference_profile_requested(
            use_reference_profile
        ):
            reference_profile = _resolve_reference_profile(
                conn,
                reference_author_id,
                strict=use_reference_profile is True,
            )
        else:
            reference_profile = None

        # ---- 共用组装器：各层数据有就用，没有就跳过 ----
        context = build_write_context(
            conn,
            source_analysis=source_analysis,
            reference_author_id=reference_author_id,
            goal=goal,
        )
        if reference_author_id is not None and reference_profile is None:
            # 与单稿同一条纪律：如实记录，绝不虚构
            context["skipped"].append("reference_writing_profile")

        if state == personalization.COLD_START:
            similar = []
            profile = None
            style_profile_json = None
            style_summary = None
            edit_preferences = None
        else:
            profile = usable_style_profile(conn)
            # 04-C：author_auto 不得用占位串做 BM25 检索（与单稿同一条纪律）
            similar = retriever.find_similar(
                conn,
                source_analysis,
                k=k,
                exclude_id=source_content_id,
                target_text=None if path in AUTO_TOPIC_PATHS else source_text,
            )
            style_profile_json = (profile or {}).get("profile_json")
            style_summary = (profile or {}).get("summary_text")
            edit_preferences = _extract_edit_preferences(style_profile_json)

    examples = [
        {
            "text": (s.get("raw_text") or "")[:EXAMPLE_CHARS],
            "provenance": s.get("provenance"),
            "edit_ratio": s.get("edit_ratio"),
        }
        for s in similar
    ]

    messages = build_candidate_write_messages(
        state=state,
        source_text=source_text,
        instruction=instruction,
        examples=examples,
        edit_preferences=edit_preferences,
        style_profile=style_profile_json,
        style_summary=style_summary,
        source_analysis=source_analysis,
        strategy_context=context,
        reference_profile=reference_profile,
        creation_path=path,
    )

    provider = get_provider()
    raw = await provider.chat(
        messages,
        model=settings.model_rewrite,
        # 不传 temperature / max_tokens：同上（thinking 模式覆写 + 供应商默认上限）
    )

    parsed = parse_candidates(raw or "")
    if parsed is None:
        # 明确失败，**绝不偷偷再调一次 AI**
        raise ServiceError(
            "模型没有按 A / B / C 三段格式返回候选，请重新生成一次"
            "（不会自动重试，避免额外消耗）。"
        )

    candidates = [
        {
            "key": key,
            "label": ROLE_LABEL.get(key, key),
            "text": parsed[key],
            # strategy_tags 由代码生成，不是模型自报
            "strategy_tags": tags_for_role(context, key),
        }
        for key, _label, _desc in CANDIDATE_ROLES
    ]

    return {
        "source_content_id": source_content_id,
        "reference_author_id": reference_author_id,
        "creation_path": path,
        # 04-C 验收修复 ②：参考作者画像是否真的进入了本次 WRITE 上下文
        "reference_profile_used": reference_profile is not None,
        "candidates": candidates,
        "context": _public_context(context),
        "context_empty": context_is_empty(context),
        "prompt_version": WRITE_PROMPT_VERSION,
        "personalization": state_info,
        "model": settings.model_rewrite,
    }


def _public_context(context: dict[str, Any]) -> dict[str, Any]:
    """给 UI 的轻量上下文：只保留展示需要的部分，绝不回传整份 Stage 8/9 JSON。"""
    return {
        "source_analysis": context.get("source_analysis"),
        "writing_count": len(context.get("writing") or []),
        "performance_high": context.get("performance_high") or [],
        "performance_low": context.get("performance_low") or [],
        "cross_high": context.get("cross_high") or [],
        "cross_low": context.get("cross_low") or [],
        "account_dependent": context.get("account_dependent") or [],
        "skipped": context.get("skipped") or [],
        "budget": context.get("budget"),
    }
