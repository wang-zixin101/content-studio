"""写作引擎：素材 / 想法 / 对标 → AI 初稿 → 用户编辑 → 保存为「我的内容」。

核心不变量（整个系统最重要的一条）：

    AI 初稿永远不会成为「我的内容」。
    只有用户点击「保存为我的」之后的 final text 才写入 contents(source='mine')。

AI 初稿只存在于 rewrites 表，用途仅限：
    1. 与终稿做对照；
    2. 作为 edit learning 数据；
    3. 调试记录。
它绝不会被选为 reference example（reference 只从 contents 表检索）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app import repository as repo
from app.ai import get_provider
from app.config import WRITE_PROMPT_VERSION, settings
from app.db import get_conn
from app.prompts import build_write_messages
from app.services import personalization, retriever
from app.services.creation_path import AUTO_TOPIC_PATHS, resolve_creation_path
from app.services.errors import NotFoundError, ServiceError
from app.services.style_profile import compute_edit_metrics, usable_style_profile
from app.services.write_context import build_write_context

EXAMPLE_CHARS = 600
DEFAULT_EXAMPLES = 5

#: author_auto 下没有素材文本；组装器会把它换成「自主选题」说明块。
#: 保留一个显式常量而不是让空串流进 prompt —— 空串一旦被当成素材，
#: 模型会以为「素材就是空的」而拒绝生成。
_AUTO_TOPIC_PLACEHOLDER = "（本次未提供素材或主题）"


# ---------------------------------------------------------------- 参考作者画像

def _author_label(author: dict[str, Any]) -> str:
    return (author.get("display_name") or "").strip() or f"@{author.get('username')}"


def _resolve_reference_profile(
    conn: sqlite3.Connection,
    author_id: int,
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    """服务端按 author_id 读取**正式**的写作画像（阶段 6 的聚合结果）。

    阶段 7 纪律：
      - 绝不接受前端传来的画像 JSON —— 只能读库里算好的那一份；
      - 作者不存在 → 404；画像与作者不匹配 → 报错（两种情况与 strict 无关）。

    04-C 验收修复 ②：选择参考作者后**默认**就使用其可用写作画像，
    不需要额外操作启用。因此画像缺失分两种处理：

      - `strict=True`（调用方**显式**要求必须用画像）→ 明确报错，
        绝不静默降级、绝不自动去分析（保留阶段 7 的既有契约与测试）；
      - `strict=False`（默认路径：只是选了作者）→ 返回 None，
        由调用方记进 skipped[] 并照常生成 —— 缺哪层跳哪层，
        不虚构画像、不自动调用 ANALYSIS、不阻塞本次生成。
    """
    author = repo.get_author(conn, author_id)
    if author is None:
        raise NotFoundError(f"参考作者 {author_id} 不存在")

    profile = repo.author_writing_profile(conn, author_id)
    if int(profile.get("author_id") or author_id) != author_id:
        raise ServiceError("参考作者画像与作者不匹配")

    if int(profile.get("analyzed_sample_count") or 0) <= 0:
        if strict:
            raise ServiceError(
                f"暂无可用画像：{_author_label(author)} 还没有任何已分析内容。"
                "请先分析该作者的帖子，再启用「使用作者写作画像」。"
            )
        return None

    return profile


def reference_profile_requested(use_reference_profile: bool | None) -> bool:
    """是否**要求**使用参考作者画像。

    `use_reference_profile` 是三态：
      - `None`（默认）→ 跟随作者选择：选了作者就用其**可用**画像，
        缺画像则跳过并如实记录；
      - `True`        → 显式要求必须用，缺画像时明确报错；
      - `False`       → 明确不用（仅记录来源）。
    """
    return use_reference_profile is not False


# ---------------------------------------------------------------- 生成初稿

def _extract_edit_preferences(profile_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """从风格档案里取出可用于生成的修改偏好。

    优先使用确定性统计（edit_patterns），其次才是模型归纳的 edit_preferences。
    """
    if not isinstance(profile_json, dict):
        return None

    out: dict[str, Any] = {}
    patterns = profile_json.get("edit_patterns")
    if isinstance(patterns, dict):
        if patterns.get("frequent_deletions"):
            out["frequent_deletions"] = patterns["frequent_deletions"]
        if patterns.get("frequent_insertions"):
            out["frequent_additions"] = patterns["frequent_insertions"]
        if patterns.get("frequent_replacements"):
            out["frequent_replacements"] = patterns["frequent_replacements"]

    prefs = profile_json.get("edit_preferences")
    if isinstance(prefs, dict):
        for key, value in prefs.items():
            if value:
                out.setdefault(key, value)

    return out or None


async def generate_draft(
    *,
    # 04-C：author_auto 下没有素材文本，因此 source_text 允许为空。
    # 是否真的可以空，由 resolve_creation_path() 判定（无素材 + 无作者 → 报错），
    # 不再靠「必填参数」这一层来挡 —— 那样只会给出一句看不懂的 TypeError。
    source_text: str | None = None,
    source_content_id: int | None = None,
    instruction: str | None = None,
    base_text: str | None = None,
    k: int = DEFAULT_EXAMPLES,
    reference_author_id: int | None = None,
    use_reference_profile: bool | None = None,
    goal: str | None = None,
    creation_path: str | None = None,
) -> dict[str, Any]:
    """生成一版可直接编辑的初稿，并落库（保存 AI 初稿）。

    `use_reference_profile` 三态（04-C 验收修复 ②）：
      None（默认）→ 选了作者就用其**可用**写作画像，缺则跳过并记录；
      True        → 显式要求必须用，缺画像时明确报错；
      False       → 明确不用（仅记录来源）。
    三种情况都**不**自动调用 ANALYSIS、都**不**虚构画像。

    任务 04-C：五种创作输入路径。
      - rewrite / rewrite_author：source_text 是待改写的原帖；
      - topic / topic_author    ：source_text 是主题或灵感；
      - author_auto             ：**没有** source_text，由 AI 依据
        参考作者画像 + 用户风格 + 历史流量数据自主选题。
    路径由 services/creation_path.resolve_creation_path() 判定，
    与三候选**同一个函数**（避免两个出口判出不同路径）。

    未分析的素材（新粘贴、或 analysis_state != analyzed）**照常生成**：
    source_analysis 为 None 时该段直接跳过，绝不阻塞、绝不自动补分析。

    参考作者画像（阶段 7）：
      - 只有 use_reference_profile=True 时才读画像、才注入 prompt；
      - 「仅记录来源」= use_reference_profile=False → **完全不读画像**，
        不产生任何额外的 AI 调用（整个 generate_draft 始终只有 1 次 WRITE 调用）；
      - 画像由**服务端**按 reference_author_id 读取，前端传不进来画像内容。

    任务 04-A 修 D1：
      - source_analysis 现在是**完整**的分析对象（repo.get_analysis 返回合同对象
        + 本地字段），prompt 组装器会把 content.must_preserve 等约束真正注入。
        旧版只传 13 个扁平列，而在 builder 里又去取 content_invariants ——
        那是一个生产路径上永不成立的分支。
      - 单稿现在也使用**历史数据规律**（strategy_context），
        与三候选走同一个组装器，不再是「只有三候选有数据、单稿裸写」。
    """
    source_text = (source_text or "").strip()

    # 04-C：只传 content_id 也算子有素材 —— 文本由服务端自己取，
    # 不再要求调用方先取好再传（旧代码里漏传就会静默生成一版空素材初稿）。
    if not source_text and source_content_id is not None:
        with get_conn(settings.db_path) as conn:
            row = repo.get_content_raw(conn, source_content_id)
        if row is None:
            raise NotFoundError(f"内容 {source_content_id} 不存在")
        source_text = (row["raw_text"] or "").strip()

    # 路径先于素材校验 —— 只有 author_auto 允许素材为空
    path = resolve_creation_path(
        creation_path=creation_path,
        has_source=bool(source_text),
        use_reference_profile=use_reference_profile,
        reference_author_id=reference_author_id,
    )
    if not source_text:
        # author_auto：用占位文本进入组装器，实际输入块由 prompt 换成
        # 「自主选题」说明；**绝不能**把空字符串当成素材传给模型。
        source_text = _AUTO_TOPIC_PLACEHOLDER

    with get_conn(settings.db_path) as conn:
        state_info = personalization.resolve_state(conn)
        state = state_info["state"]

        if use_reference_profile is True and reference_author_id is None:
            raise ServiceError("启用作者写作画像时必须提供参考作者")

        # 04-C 验收修复 ②：选了参考作者 → 默认就用其可用画像（strict 仅在
        # 调用方**显式**要求时为真）。缺画像时返回 None，稍后记进 skipped[]。
        if reference_author_id is not None and reference_profile_requested(
            use_reference_profile
        ):
            reference_profile = _resolve_reference_profile(
                conn,
                reference_author_id,
                strict=use_reference_profile is True,
            )
        else:
            # 「仅记录来源」或没选作者：不读画像、不注入、不额外调用 AI
            reference_profile = None

        source_analysis = (
            repo.get_analysis(conn, source_content_id) if source_content_id else None
        )

        # ---- 参考作者的数据层（写作画像 + 表现洞察 + 跨账号共性）----
        # 与三候选**共用同一个组装器**（services/write_context）；
        # 任一层缺失只是记进 skipped[]，不自动分析、不阻塞生成。
        strategy_context = build_write_context(
            conn,
            source_analysis=source_analysis,
            reference_author_id=reference_author_id,
            goal=goal,
        )
        if reference_author_id is not None and reference_profile is None:
            # 如实记录「选了作者但这一层没有可用数据」，绝不虚构
            strategy_context["skipped"].append("reference_writing_profile")

        if state == personalization.COLD_START:
            # 没有可靠个人风格证据：不检索 reference、不注入任何风格画像，
            # 也绝不伪造 Voice。
            similar: list[dict[str, Any]] = []
            profile = None
            style_profile_json = None
            style_summary = None
            edit_preferences = None
        else:
            profile = usable_style_profile(conn)
            # reference 只来自 contents（final text）；不足即少给，禁止补齐
            # 04-C：author_auto 没有素材文本，不能用占位串去 BM25 检索 ——
            # 那只会把无关作品当成"相关示例"塞进 prompt。
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

    messages = build_write_messages(
        state=state,
        source_text=source_text,
        instruction=instruction,
        examples=examples,
        edit_preferences=edit_preferences,
        style_profile=style_profile_json,
        style_summary=style_summary,
        source_analysis=source_analysis,
        base_text=base_text,
        reference_profile=reference_profile,
        strategy_context=strategy_context,
        creation_path=path,
    )

    provider = get_provider()
    draft = await provider.chat(
        messages,
        model=settings.model_rewrite,
        # 不传 temperature / max_tokens：
        #   - thinking 模式下 temperature 会被服务端强制覆写，传了无效；
        #   - max_tokens 交给供应商默认上限（mimo-v2.6-pro 为 131072）。
    )
    draft = (draft or "").strip()

    with get_conn(settings.db_path) as conn:
        rewrite_id = repo.create_rewrite(
            conn,
            benchmark_content_id=source_content_id,
            ai_draft=draft,
            instruction=instruction,
            examples_used=[s["id"] for s in similar],
            style_profile_version=(profile or {}).get("version") if profile else None,
            model=settings.model_rewrite,
            prompt_version=WRITE_PROMPT_VERSION,
            personalization_state=state,
        )
        row = repo.get_rewrite(conn, rewrite_id)

    result = _serialize(row, similar)
    result["personalization"] = state_info
    # 04-C：把实际生效的创作路径回传，便于排查「为什么生成了这个」
    result["creation_path"] = path
    # 04-C 验收修复 ②：参考作者画像**是否真的进入**了本次 WRITE 上下文
    result["reference_profile_used"] = reference_profile is not None
    return result


# ---------------------------------------------------------------- 保存终稿

def save_final(
    rewrite_id: int,
    final_text: str,
    *,
    save_as_mine: bool = True,
) -> dict[str, Any]:
    """保存用户终稿。

    顺序（与产品要求一致）：
      1. final text 写入 contents(source='mine', provenance='ai_assisted')
      2. 记录 provenance
      3. 保存 rewrite pair（初稿 / 终稿 / 编辑距离 / 比例）

    **保存终稿绝不自动分析**（阶段 4 的 AI 调用边界）：
    style_eligible=True 只表示这条终稿「未来有资格」成为 STYLE 证据，
    不等于此刻就消耗 token。ANALYSIS 只能由用户之后显式点击
    「实时分析」/「Batch 分析」触发。终稿的 analysis_state 保持 unprocessed。

    注意：即使终稿与 AI 初稿完全一致、用户直接保存，
    provenance 仍然是 ai_assisted —— 不得伪装成 human_original。
    """
    final_text = (final_text or "").strip()
    if not final_text:
        raise NotFoundError("终稿内容为空")

    with get_conn(settings.db_path) as conn:
        row = repo.get_rewrite(conn, rewrite_id)
        if row is None:
            raise NotFoundError(f"生成记录 {rewrite_id} 不存在")

        draft = row["ai_draft"] or ""
        distance, ratio = compute_edit_metrics(draft, final_text)

        repo.update_rewrite_final(conn, rewrite_id, final_text, distance, ratio)

        saved_content_id: int | None = None
        if save_as_mine:
            # 用户确认的 AI 终稿：默认允许作为风格学习样本
            # （之后用户可以随时关闭；authorship/preference 权重仍由 edit_ratio 决定）
            new_id = repo.create_content(conn, "mine", final_text, style_eligible=True)
            repo.set_provenance(conn, new_id, "ai_assisted")
            repo.mark_rewrite_saved(conn, rewrite_id, new_id)
            saved_content_id = new_id

    # 不自动入队分析：ANALYSIS 只能由用户显式触发（见函数 docstring）

    with get_conn(settings.db_path) as conn:
        updated = repo.get_rewrite(conn, rewrite_id)
    result = _serialize(updated, [])
    result["saved_content_id"] = saved_content_id
    return result


# ---------------------------------------------------------------- 查询

def get_detail(rewrite_id: int) -> dict[str, Any]:
    with get_conn(settings.db_path) as conn:
        row = repo.get_rewrite(conn, rewrite_id)
        if row is None:
            raise NotFoundError(f"生成记录 {rewrite_id} 不存在")

        example_ids = repo._loads(row["examples_used"])
        similar: list[dict[str, Any]] = []
        for cid in example_ids:
            info = repo.get_content(conn, cid)
            if info is None:
                continue
            similar.append(
                {
                    "id": info["id"],
                    "source": info["source"],
                    "excerpt": info["excerpt"],
                    "topic": info["topic"],
                    "content_type": info["content_type"],
                    "score": 0.0,
                    "shared_tags": info["tags"],
                }
            )
    return _serialize(row, similar)


def _serialize(row: sqlite3.Row, similar: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "benchmark_content_id": row["benchmark_content_id"],
        "ai_draft": row["ai_draft"],
        "final_text": row["final_text"],
        "is_saved_as_mine": bool(row["is_saved_as_mine"]),
        "saved_content_id": row["saved_content_id"],
        "examples_used": similar,
        "style_profile_version": row["style_profile_version"],
        "edit_distance": row["edit_distance"],
        "edit_ratio": row["edit_ratio"],
        "prompt_version": row["prompt_version"],
        "personalization_state": row["personalization_state"],
        "model": row["model"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
