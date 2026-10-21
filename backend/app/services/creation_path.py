"""创作输入路径（04-C）：五种入口的**确定性**解析。

------------------------------------------------------------------
为什么单独成模块
------------------------------------------------------------------
WRITE 之前只有一个隐含路径：「有素材文本 → 生成」。
04-C 要求显式支持五种入口，其中「仅选参考作者 → AI 自主选题」
是没有素材文本的新形态。路径判定散落在 API / service 里会出现
「同一个输入在不同出口得到不同路径」的老问题（04-A 的 D1/D4 就是
这么来的），因此收敛成一个纯函数，两个出口共用。

------------------------------------------------------------------
路径定义
------------------------------------------------------------------
    rewrite        原帖改写：有素材，不指定参考作者
    rewrite_author 原帖 + 参考作者：有素材，并显式启用参考作者画像
    topic          仅主题创作：有主题 / 灵感，不指定参考作者
    topic_author   主题 + 作者：有主题 / 灵感，并显式启用参考作者画像
    author_auto    仅选择参考作者：没有素材也没有主题，由 AI 自主选题

------------------------------------------------------------------
纪律
------------------------------------------------------------------
- **0 AI**：只做确定性判定，绝不调用模型、绝不自动选题；
- **不猜主题**：topic 与 rewrite 在后端看来都是一段文本，
  无法从文本本身区分，必须由调用方显式声明；未声明时按 rewrite
  处理（与 04-C 之前的行为完全一致，不产生静默行为变化）；
- **空素材只有一种合法出路**：author_auto。除此之外一律报错，
  绝不退化成「拿空文本去生成」。
"""

from __future__ import annotations

from app.services.errors import ServiceError

#: 五种创作输入路径（顺序即文档顺序）
CREATION_PATHS: tuple[str, ...] = (
    "rewrite",
    "rewrite_author",
    "topic",
    "topic_author",
    "author_auto",
)

#: 有素材 / 主题文本的路径
SOURCE_PATHS: tuple[str, ...] = ("rewrite", "rewrite_author")
#: 主题 / 灵感类路径（source_text 是主题，不是待改写的原帖）
TOPIC_PATHS: tuple[str, ...] = ("topic", "topic_author")
#: 无素材无主题、由 AI 自主选题
AUTO_TOPIC_PATHS: tuple[str, ...] = ("author_auto",)

CREATION_PATH_LABELS: dict[str, str] = {
    "rewrite": "原帖改写",
    "rewrite_author": "原帖 + 参考作者",
    "topic": "仅主题创作",
    "topic_author": "主题 + 作者",
    "author_auto": "参考作者自主选题",
}


def is_source_path(path: str) -> bool:
    """素材路径：source_text 是一篇待改写的原帖。"""
    return path in SOURCE_PATHS


def is_topic_path(path: str) -> bool:
    """主题路径：source_text 是主题 / 灵感，不是原帖。"""
    return path in TOPIC_PATHS


def is_auto_topic_path(path: str) -> bool:
    """自主选题路径：没有素材也没有主题。"""
    return path in AUTO_TOPIC_PATHS


def has_reference_author(path: str) -> bool:
    return path in ("rewrite_author", "topic_author", "author_auto")


def resolve_creation_path(
    *,
    creation_path: str | None = None,
    has_source: bool,
    use_reference_profile: bool = False,
    reference_author_id: int | None = None,
) -> str:
    """唯一入口：把「有没有素材 / 有没有参考作者 / 显式声明」判定成一条路径。

    - 显式声明且合法 → 采用，但仍按输入形态校准作者维度；
    - 未声明 → 按输入形态推导，**行为与 04-C 之前完全一致**；
    - 声明与输入形态冲突 → 报错，绝不静默改判（静默改判会让
      「AI 自主选题」悄悄变成「拿空文本生成」）。

    作者维度的判定依据是 **reference_author_id**，不是 use_reference_profile：
      参考作者一旦被选中，其流量画像与跨账号数据就已经进入本次生成；
      use_reference_profile 只是「要不要再注入写作画像」的更细开关，
      它不改变「这次有没有参考作者」这个事实。
    """
    has_author = reference_author_id is not None

    declared = (creation_path or "").strip() or None
    if declared is not None and declared not in CREATION_PATHS:
        raise ServiceError(
            f"未知的创作路径：{declared}（可选：{' / '.join(CREATION_PATHS)}）"
        )

    # ---- 无素材：只有「参考作者自主选题」一条合法出路 ----
    if not has_source:
        if declared is not None and declared != "author_auto":
            raise ServiceError(
                f"没有素材或主题时只支持「参考作者自主选题」，"
                f"但收到了「{CREATION_PATH_LABELS.get(declared, declared)}」"
            )
        if not has_author:
            raise ServiceError(
                "没有素材也没有参考作者，无法生成。"
                "请粘贴素材 / 写下主题，或先选择一个参考作者由 AI 自主选题。"
            )
        return "author_auto"

    # ---- 有素材 ----
    if declared in TOPIC_PATHS:
        return "topic_author" if has_author else "topic"
    if declared in SOURCE_PATHS:
        return "rewrite_author" if has_author else "rewrite"
    if declared == "author_auto":
        raise ServiceError(
            "「参考作者自主选题」不接收素材或主题；"
            "若要基于素材生成，请改用「原帖改写」或「仅主题创作」。"
        )
    # 未声明：与旧行为一致 —— 有参考作者即 rewrite_author，否则 rewrite
    # 主题路径必须由调用方显式声明（后端无法从文本区分素材与主题）
    return "rewrite_author" if has_author else "rewrite"
