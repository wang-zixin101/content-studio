"""STYLE 档案合同：类型、关键字段、来源计数与逐字引文的严格校验（B7）。

------------------------------------------------------------------
为什么需要它
------------------------------------------------------------------
04-A 交付的真实样本证明：合法 JSON ≠ 语义正确。STYLE 输出此前
`chat_json()` 拿到 dict 就直接存档，没有任何合同校验 ——
模型可以自报 evidence 计数、引用根本没有提供的文章、把一篇内的
重复说成「多篇常用」而不被发现。

本模块把「不能凭空编造」从提示词请求升级为**代码级约束**：

  1. 六个 evidence 计数由程序统计**覆写**，模型自报的数字一律丢弃；
  2. `signature_patterns[].supporting_samples` 的每个 `content_id`
     必须出现在本轮实际提供的样本里，`quote` 必须逐字连续存在于
     对应样本的 text；
  3. `evidence_count` 必须等于 supporting_samples 中**不同 content_id**
     的数量，且不超过实际阅读的样本数；
  4. 枚举字段（classification / strength / confidence）越界即拒收。

校验失败时给出**具体路径**，调用方只允许做一次明确路径的纠正重试，
禁止无界重试。
"""

from __future__ import annotations

from typing import Any

#: 顶层键集合（与 prompts.STYLE_SCHEMA_HINT 的空白模板一致；缺一不可）
REQUIRED_TOP_LEVEL_KEYS = (
    "common_openings", "common_endings", "common_structures",
    "avg_length", "long_short_ratio", "first_person_habit",
    "common_tones", "common_expressions", "high_freq_words",
    "emotion_profile", "viewpoint_style", "common_content_types",
    "evidence", "global_voice", "signature_patterns", "x_modes",
    "edit_preferences", "anti_style", "topic_bound_terms",
    "uncertain_patterns", "voice_summary",
)

ALLOWED_CLASSIFICATIONS = ("VOICE", "X_REGISTER", "TOPIC_BOUND", "UNCERTAIN")
ALLOWED_STRENGTHS = ("strong", "medium", "weak")
ALLOWED_CONFIDENCE = ("high", "medium", "low")

#: evidence 里由程序统计、必须覆写模型自报值的六个计数
CODE_OWNED_COUNTS = (
    "sample_count", "text_sample_count", "human_original_count",
    "imported_self_count", "ai_assisted_count", "rewrite_pair_count",
)

EDIT_PREF_KEYS = (
    "frequent_deletions", "frequent_additions",
    "frequent_replacements", "stable_corrections",
)

SIGNATURE_KEYS = (
    "rule", "classification", "strength", "frequency",
    "evidence_count", "applies_when", "supporting_samples",
)

X_MODE_KEYS = ("name", "when_to_use", "markers", "sample_count", "confidence")

#: 可空字符串字段（允许 None，但出现时必须是 str）
NULLABLE_TEXT_FIELDS = (
    "avg_length", "long_short_ratio", "first_person_habit",
    "emotion_profile", "viewpoint_style", "voice_summary",
)
#: 字符串数组字段
STRING_LIST_FIELDS = (
    "common_openings", "common_endings", "common_structures",
    "common_tones", "common_expressions", "high_freq_words",
    "common_content_types", "topic_bound_terms", "uncertain_patterns",
)


class StyleContractError(Exception):
    """STYLE 输出不符合合同。`path` 指向具体位置，供一次纠正重试。"""

    def __init__(self, path: str, message: str):
        self.path = path
        super().__init__(f"{path}: {message}")


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_style_profile(
    data: Any,
    *,
    text_samples: dict[int, str],
    expected_counts: dict[str, int],
) -> dict[str, Any]:
    """校验 STYLE 输出并**覆写**由代码负责的字段。

    参数
    ----
    data : 模型输出的 dict
    text_samples : {content_id: 实际传给模型的 text}
        用于引文逐字核对与 `evidence_count` 上限判定。
    expected_counts : 代码统计的六个计数，将**覆写**模型自报值。

    返回
    ----
    净化后的档案（可安全存档）。

    抛出
    ----
    StyleContractError（含具体 path）—— 调用方据此做一次纠正重试。
    """
    if not isinstance(data, dict):
        raise StyleContractError("$", f"输出必须是 JSON 对象，实际 {type(data).__name__}")

    cleaned = dict(data)

    # ---- 1. 顶层键完整性 ----
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in cleaned]
    if missing:
        raise StyleContractError("$", f"缺少字段：{missing}")

    # ---- 2. 字符串数组与可空文本字段 ----
    for key in STRING_LIST_FIELDS:
        if not _is_str_list(cleaned.get(key)):
            raise StyleContractError(key, "必须是字符串数组")

    for key in NULLABLE_TEXT_FIELDS:
        value = cleaned.get(key)
        if value is not None and not isinstance(value, str):
            raise StyleContractError(key, "必须是字符串或 null")

    # ---- 3. evidence：程序计数覆写 + confidence 枚举 ----
    evidence = cleaned.get("evidence")
    if not isinstance(evidence, dict):
        raise StyleContractError("evidence", "必须是对象")
    for key in CODE_OWNED_COUNTS:
        if key not in expected_counts:
            raise StyleContractError(f"evidence.{key}", "程序未提供该计数（内部错误）")
        evidence[key] = int(expected_counts[key])
    if evidence.get("confidence") not in ALLOWED_CONFIDENCE:
        raise StyleContractError(
            "evidence.confidence",
            f"必须是 {'/'.join(ALLOWED_CONFIDENCE)}，实际 {evidence.get('confidence')!r}",
        )
    cleaned["evidence"] = evidence

    # ---- 4. global_voice ----
    voice = cleaned.get("global_voice")
    if not isinstance(voice, dict):
        raise StyleContractError("global_voice", "必须是对象")
    for key in ("register", "stance", "syntax_rhythm",
                "lexical_fingerprint", "formatting", "engagement"):
        if voice.get(key) is not None and not isinstance(voice[key], str):
            raise StyleContractError(f"global_voice.{key}", "必须是字符串或 null")
    if not _is_str_list(voice.get("rhetorical_patterns")):
        raise StyleContractError("global_voice.rhetorical_patterns", "必须是字符串数组")
    cleaned["global_voice"] = voice

    # ---- 5. signature_patterns：枚举 + 引文逐字核对 ----
    patterns = cleaned.get("signature_patterns")
    if not _is_str_list([""]):  # noqa: FURB123 —— 占位，实际检查在下方
        pass
    if not isinstance(patterns, list):
        raise StyleContractError("signature_patterns", "必须是对象数组")

    for i, pattern in enumerate(patterns):
        path = f"signature_patterns[{i}]"
        if not isinstance(pattern, dict):
            raise StyleContractError(path, "必须是对象")
        if set(pattern) != set(SIGNATURE_KEYS):
            raise StyleContractError(
                path,
                f"键必须恰好是 {sorted(SIGNATURE_KEYS)}，实际 {sorted(pattern)}",
            )
        if not isinstance(pattern["rule"], str) or not pattern["rule"].strip():
            raise StyleContractError(f"{path}.rule", "必须是非空字符串")
        if pattern["classification"] not in ALLOWED_CLASSIFICATIONS:
            raise StyleContractError(
                f"{path}.classification",
                f"必须是 {'/'.join(ALLOWED_CLASSIFICATIONS)}，"
                f"实际 {pattern['classification']!r}",
            )
        if pattern["strength"] not in ALLOWED_STRENGTHS:
            raise StyleContractError(
                f"{path}.strength",
                f"必须是 {'/'.join(ALLOWED_STRENGTHS)}，实际 {pattern['strength']!r}",
            )
        for key in ("frequency", "applies_when"):
            if not isinstance(pattern[key], str):
                raise StyleContractError(f"{path}.{key}", "必须是字符串")

        samples = pattern["supporting_samples"]
        if not isinstance(samples, list) or not samples:
            raise StyleContractError(
                f"{path}.supporting_samples",
                "必须是非空数组（没有逐字证据的规律应归入 uncertain_patterns）",
            )
        seen_ids: set[int] = set()
        for j, sample in enumerate(samples):
            spath = f"{path}.supporting_samples[{j}]"
            if not isinstance(sample, dict) or set(sample) != {"content_id", "quote"}:
                raise StyleContractError(
                    spath, "键必须恰好是 {content_id, quote}"
                )
            cid = sample["content_id"]
            quote = sample["quote"]
            if isinstance(cid, bool) or not isinstance(cid, int):
                raise StyleContractError(f"{spath}.content_id", "必须是整数")
            if cid not in text_samples:
                raise StyleContractError(
                    f"{spath}.content_id",
                    f"content_id {cid} 不在本次提供的样本里（不得引用未提供的文章）",
                )
            if not isinstance(quote, str) or not quote.strip():
                raise StyleContractError(f"{spath}.quote", "必须是原文连续逐字片段")
            if quote not in text_samples[cid]:
                raise StyleContractError(
                    f"{spath}.quote",
                    f"引文未在 content_id {cid} 的原文中逐字出现：{quote[:40]!r}",
                )
            seen_ids.add(cid)

        if pattern["evidence_count"] != len(seen_ids):
            raise StyleContractError(
                f"{path}.evidence_count",
                f"必须等于 supporting_samples 中不同 content_id 的数量 "
                f"（应为 {len(seen_ids)}，实际 {pattern['evidence_count']}）",
            )
        if pattern["evidence_count"] > len(text_samples):
            raise StyleContractError(
                f"{path}.evidence_count",
                f"不能超过实际阅读的样本数 {len(text_samples)}",
            )

    # ---- 6. x_modes ----
    modes = cleaned.get("x_modes")
    if not isinstance(modes, list):
        raise StyleContractError("x_modes", "必须是对象数组")
    for i, mode in enumerate(modes):
        path = f"x_modes[{i}]"
        if not isinstance(mode, dict) or set(mode) != set(X_MODE_KEYS):
            raise StyleContractError(
                path, f"键必须恰好是 {sorted(X_MODE_KEYS)}"
            )
        if not isinstance(mode["name"], str) or not mode["name"].strip():
            raise StyleContractError(f"{path}.name", "必须是非空字符串")
        if not isinstance(mode["when_to_use"], str):
            raise StyleContractError(f"{path}.when_to_use", "必须是字符串")
        if not _is_str_list(mode["markers"]):
            raise StyleContractError(f"{path}.markers", "必须是字符串数组")
        sample_count = mode["sample_count"]
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise StyleContractError(f"{path}.sample_count", "必须是整数")
        if sample_count < 0 or sample_count > len(text_samples):
            raise StyleContractError(
                f"{path}.sample_count",
                f"只能在 0 ~ 实际阅读样本数（{len(text_samples)}）之间",
            )
        if mode["confidence"] not in ALLOWED_CONFIDENCE:
            raise StyleContractError(
                f"{path}.confidence",
                f"必须是 {'/'.join(ALLOWED_CONFIDENCE)}，实际 {mode['confidence']!r}",
            )

    # ---- 7. edit_preferences：结构校验（frequent_* 随后由代码覆写）----
    prefs = cleaned.get("edit_preferences")
    if not isinstance(prefs, dict):
        raise StyleContractError("edit_preferences", "必须是对象")
    for key in EDIT_PREF_KEYS:
        if not _is_str_list(prefs.get(key)) and not _is_text_count_list(prefs.get(key)):
            raise StyleContractError(
                f"edit_preferences.{key}", "必须是字符串数组或 {text, count} 数组"
            )
    cleaned["edit_preferences"] = prefs

    # ---- 8. anti_style ----
    anti = cleaned.get("anti_style")
    if not isinstance(anti, dict):
        raise StyleContractError("anti_style", "必须是对象")
    for key in ("supported_by_user_edits", "not_observed_do_not_force"):
        if not _is_str_list(anti.get(key)):
            raise StyleContractError(f"anti_style.{key}", "必须是字符串数组")
    cleaned["anti_style"] = anti

    return cleaned


def _is_text_count_list(value: Any) -> bool:
    """`[{"text": "...", "count": N}]` 形态。"""
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict) or set(item) != {"text", "count"}:
            return False
        if not isinstance(item["text"], str):
            return False
        if isinstance(item["count"], bool) or not isinstance(item["count"], int):
            return False
    return True
