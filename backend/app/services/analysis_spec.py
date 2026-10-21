"""ANALYSIS 契约的**机器可读 spec** 与严格校验器。

------------------------------------------------------------------
这个模块是唯一的 canonical 定义
------------------------------------------------------------------
字段结构、枚举词典、空白模板全部在这里声明一次。
`prompts.ANALYSIS_SCHEMA_HINT` 是给人/模型看的自然语言版，
本模块是机器可执行版；测试会断言两者字段树一致，防止漂移。

外部分析结果来自不受控的模型，必须**先验后存**：
    严格校验通过 → 才允许提取索引列 → 才允许落库
Realtime / Batch / 外部分析三条入口**共用**本校验器（不得各自松紧）。

------------------------------------------------------------------
本模块的边界（刻意划清）
------------------------------------------------------------------
- 只做「结构 / 类型 / 枚举」校验，**不做业务语义判断**
  （不给 topic 打分、不检查 evidence 是否真的出现在原文里 —— 那是质检不是合同）
- **不自动修**：缺 key、多 key、驼峰变体、近义枚举值一律视为错误
- **不猜**：字段名不认识就报错，绝不静默丢弃

------------------------------------------------------------------
字段规则
------------------------------------------------------------------
- 所有声明的 key 都是 **required**：缺 key → 错误
- 可空标量允许 `null`，但**不允许靠缺 key 表示空值**
- 列表允许 `[]`；对象列表的每个元素必须**恰好**含声明的键
- 标量型枚举字段的取值必须落在枚举词典内（`null` 也允许）
- 类型化对象列表的枚举子字段（如 `moves[].type`）同样强制枚举
- 未知 key → 错误（文件级拒收，不做静默丢弃）
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import ANALYSIS_SCHEMA_VERSION

# 字段类型
SCALAR = "scalar"
LIST = "list"
OBJECT = "object"
OBJECT_LIST = "object_list"


# ------------------------------------------------------------------
# 统一枚举词典
#
# 设计原则：跨样本要能计数，所以写作特征用**固定标签**；
# 无法判断时可以返回 null；确实不属于已知类型时用「其他」/「不明显」兜底。
# 模型不得输出近义词 —— 这里就是白名单。
# ------------------------------------------------------------------

ENUMS: dict[str, tuple[str, ...]] = {
    "content_type": (
        "观点表达", "经验分享", "故事叙述", "教程方法", "清单盘点", "金句语录",
        "吐槽批评", "复盘总结", "提问互动", "情感抒发", "资讯转述", "推广推荐",
        "其他",
    ),
    "opening_type": (
        "直接陈述", "设问", "反问", "反常识", "悬念", "数字事实", "故事场景",
        "直接号召", "其他", "不明显",
    ),
    "structure_type": (
        "单句直述", "观点—理由", "问题—回答", "现象—解释", "对照—结论",
        "铺垫—揭示", "故事—感悟", "清单—步骤", "其他", "不明显",
    ),
    "ending_type": (
        "自然结束", "观点收束", "开放提问", "行动号召", "反转揭示", "情绪收束",
        "其他", "不明显",
    ),
    "tone": (
        "中性", "严肃", "轻松", "幽默", "讽刺", "愤怒", "激昂", "温和",
        "其他", "不明显",
    ),
    "register": ("口语", "书面", "混合", "不明显"),
    "directness": ("直接", "委婉", "不明显"),
    "certainty": ("明确肯定", "保留余地", "不明显"),
    "rhythm": ("短句快节奏", "长句解释", "长短交替", "列表式", "不明显"),
    "claim_kind": ("事实性主张", "作者判断", "个人经历", "他人引述"),
    "move_type": (
        "设问", "反问", "对比", "反转", "递进", "因果", "让步", "反讽",
        "类比", "重复", "列举", "悬念", "故事化", "称呼读者", "号召互动", "其他",
    ),
    "marker_type": (
        "短句", "长句", "换行", "列表", "重复", "反问", "标点强调",
        "表情符号", "数字强调", "口语词", "读者称呼", "引用", "其他",
    ),
}


# ------------------------------------------------------------------
# 字段树
# ------------------------------------------------------------------

def _e(enum_key: str) -> dict[str, Any]:
    """可空 + 受枚举约束的标量。"""
    return {"type": SCALAR, "nullable": True, "enum": enum_key}


def _s() -> dict[str, Any]:
    """可空标量（无枚举约束）。"""
    return {"type": SCALAR, "nullable": True}


def _l() -> dict[str, Any]:
    """字符串列表（允许空数组）。"""
    return {"type": LIST}


def _o(fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": OBJECT, "fields": fields}


def _ol(fields: dict[str, Any]) -> dict[str, Any]:
    """类型化对象列表 —— 元素的键集合必须**恰好**等于 fields。"""
    return {"type": OBJECT_LIST, "fields": fields}


ANALYSIS_FIELDS: dict[str, Any] = {
    # ---- 基础信息（结构化，便于搜索与分组）----
    "topic": _s(),
    "subtopic": _s(),
    "content_type": _e("content_type"),
    "core_message": _s(),
    "tags": _l(),
    "keywords": _l(),

    # ---- 内容层：这篇实际说了什么 ----
    "content": _o(
        {
            "claims": _ol(
                {
                    "kind": _e("claim_kind"),
                    "text": {"type": SCALAR, "nullable": False},
                    "evidence": {"type": SCALAR, "nullable": False},
                }
            ),
            "must_preserve": _l(),
            "context_limits": _l(),
        }
    ),

    # ---- 写法层：怎么组织的 ----
    "writing": _o(
        {
            "opening_type": _e("opening_type"),
            "structure_type": _e("structure_type"),
            "ending_type": _e("ending_type"),
            "moves": _ol(
                {
                    "type": _e("move_type"),
                    "evidence": {"type": SCALAR, "nullable": False},
                    "mechanism": {"type": SCALAR, "nullable": False},
                }
            ),
        }
    ),

    # ---- 语言层：本篇的语气与形式（**不是**作者的长期风格）----
    "style": _o(
        {
            "tone": _e("tone"),
            "register": _e("register"),
            "directness": _e("directness"),
            "certainty": _e("certainty"),
            "rhythm": _e("rhythm"),
            "markers": _ol(
                {
                    "type": _e("marker_type"),
                    "evidence": {"type": SCALAR, "nullable": False},
                }
            ),
            "distinctive_phrases": _l(),
        }
    ),

    # ---- 复用层：哪些写法可以换题迁移 ----
    "reuse": _o(
        {
            "patterns": _ol(
                {
                    "method": {"type": SCALAR, "nullable": False},
                    "evidence": {"type": SCALAR, "nullable": False},
                    "how_to_apply": {"type": SCALAR, "nullable": False},
                }
            ),
            "boundaries": _l(),
        }
    ),
}

ANALYSIS_SPEC: dict[str, Any] = {
    "version": ANALYSIS_SCHEMA_VERSION,
    "fields": ANALYSIS_FIELDS,
    "enums": {key: list(values) for key, values in ENUMS.items()},
}


def build_blank_template() -> dict[str, Any]:
    """按 spec 生成「唯一空白模板」：所有 key 齐备、可 JSON 解析。"""
    def _blank(rule: dict[str, Any]) -> Any:
        kind = rule["type"]
        if kind == SCALAR:
            return None
        if kind == LIST:
            return []
        if kind == OBJECT:
            return {name: _blank(sub) for name, sub in rule["fields"].items()}
        if kind == OBJECT_LIST:
            return []
        raise AssertionError(f"未知 spec 类型 {kind}")

    return {name: _blank(rule) for name, rule in ANALYSIS_FIELDS.items()}


BLANK_TEMPLATE: dict[str, Any] = build_blank_template()


def spec_field_paths(spec: dict[str, Any] | None = None) -> list[str]:
    """展开全部字段路径，已排序。

    对象节点输出自身路径（如 `writing`），其子字段继续展开；
    对象列表用 `[]` 标记逐元素下钻（如 `writing.moves[].type`）。
    """
    fields = (spec or ANALYSIS_SPEC)["fields"]
    out: list[str] = []

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for name, rule in node.items():
            path = f"{prefix}{name}"
            out.append(path)
            kind = rule["type"]
            if kind == OBJECT:
                walk(f"{path}.", rule["fields"])
            elif kind == OBJECT_LIST:
                walk(f"{path}[].", rule["fields"])

    walk("", fields)
    return sorted(out)


def scalar_field_paths(spec: dict[str, Any] | None = None) -> list[str]:
    """只返回**可索引**的标量路径（不含对象/数组节点）。

    这些路径是 analyses 表的扁平列来源。
    """
    out: list[str] = []

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for name, rule in node.items():
            path = f"{prefix}{name}"
            if rule["type"] == SCALAR:
                out.append(path)
            elif rule["type"] == OBJECT:
                walk(f"{path}.", rule["fields"])

    walk("", (spec or ANALYSIS_SPEC)["fields"])
    return sorted(out)


def canonical_spec_json(spec: dict[str, Any] | None = None) -> str:
    """canonical JSON：sort_keys + 稳定 separators + 非 ASCII 原样。"""
    return json.dumps(
        spec or ANALYSIS_SPEC,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_schema_hash(spec: dict[str, Any] | None = None) -> str:
    """对 canonical spec JSON 的 UTF-8 字节做 SHA256。

    指纹覆盖 **version + fields + enums** —— 换枚举值也属于合同变更，
    必须让 hash 变化，否则外部交换文件会带着旧枚举冒充新合同。
    """
    payload = canonical_spec_json(spec).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


#: 真实字段定义指纹 —— 只要字段树或枚举词典变化，这个值必然变化
ANALYSIS_SCHEMA_HASH = compute_schema_hash()


# ------------------------------------------------------------------
# 严格校验
# ------------------------------------------------------------------

class SpecError(Exception):
    """单条 analysis 不符合 ANALYSIS 契约。"""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path or '$'}: {message}")
        self.path = path or "$"
        self.message = message


def _check_enum(path: str, rule: dict[str, Any], value: str) -> None:
    enum_key = rule.get("enum")
    if not enum_key:
        return
    allowed = ENUMS[enum_key]
    if value not in allowed:
        raise SpecError(
            path,
            f"「{value}」不在枚举词典内（允许：{'、'.join(allowed)}）",
        )


def _check_scalar(path: str, rule: dict[str, Any], value: Any) -> None:
    if value is None:
        if not rule.get("nullable", False):
            raise SpecError(path, "不允许为 null")
        return
    if not isinstance(value, str):
        raise SpecError(path, f"应为字符串，实际 {type(value).__name__}")
    _check_enum(path, rule, value)


def _check_value(path: str, rule: dict[str, Any], value: Any) -> None:
    kind = rule["type"]

    if kind == SCALAR:
        _check_scalar(path, rule, value)
        return

    if kind == LIST:
        if not isinstance(value, list):
            raise SpecError(path, f"应为数组，实际 {type(value).__name__}")
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                raise SpecError(
                    f"{path}[{idx}]", f"数组元素应为字符串，实际 {type(item).__name__}"
                )
        return

    if kind == OBJECT:
        if not isinstance(value, dict):
            raise SpecError(path, f"应为对象，实际 {type(value).__name__}")
        _check_object(f"{path}." if path else "", rule["fields"], value)
        return

    if kind == OBJECT_LIST:
        if not isinstance(value, list):
            raise SpecError(path, f"应为数组，实际 {type(value).__name__}")
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                raise SpecError(
                    f"{path}[{idx}]", f"数组元素应为对象，实际 {type(item).__name__}"
                )
            _check_object(f"{path}[{idx}].", rule["fields"], item)
        return

    raise SpecError(path, f"未知的 spec 类型 {kind}")


def _check_object(prefix: str, fields: dict[str, Any], value: dict[str, Any]) -> None:
    expected = set(fields)
    actual = set(value)

    missing = sorted(expected - actual)
    if missing:
        raise SpecError(
            f"{prefix}{missing[0]}",
            f"缺少必需字段「{missing[0]}」（不允许靠缺 key 表示空值）",
        )

    unknown = sorted(actual - expected)
    if unknown:
        raise SpecError(
            f"{prefix}{unknown[0]}",
            f"未知字段「{unknown[0]}」（不自动纠正字段名）",
        )

    for name, rule in fields.items():
        _check_value(f"{prefix}{name}", rule, value[name])


def validate_analysis(data: Any) -> dict[str, Any]:
    """严格校验一份 ANALYSIS。

    通过 → 原样返回 data（**不做任何改写**）；不通过 → 抛 SpecError。
    """
    if not isinstance(data, dict):
        raise SpecError("$", f"顶层应为对象，实际 {type(data).__name__}")
    _check_object("", ANALYSIS_FIELDS, data)
    return data


def is_valid_analysis(data: Any) -> bool:
    try:
        validate_analysis(data)
        return True
    except SpecError:
        return False
