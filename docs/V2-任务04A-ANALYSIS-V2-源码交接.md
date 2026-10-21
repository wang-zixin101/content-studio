# ANALYSIS V2 源码交接材料

> **用途**：为 ANALYSIS V2 提示词的重新设计提供**原始真实材料**。
> **来源**：Content Studio（`C:\Users\18483\Desktop\x运营\content-studio`），全部代码**原样复制**，未做任何整理、改写、优化或省略。
> **本文件生成时间**：2026-10-09
> **交接方职责边界**：提示词的研究、优化、设计由 ChatGPT 接管；本文件只提供事实材料，不含任何设计方案建议。

---

## 0. 交接说明与硬约束

交给你的设计任务：**重新设计 ANALYSIS V2 的提示词**（`ANALYSIS_SYSTEM` 与 `ANALYSIS_SCHEMA_HINT` 的措辞、结构、判别规则）。

**必须遵守的硬约束：**

1. **字段名与字段树不得改动**（除非明确走 Schema 变更流程）。当前共 **59 个字段路径 / 54 个叶子字段**。改字段名（如 `openingType`）、新增字段、删除字段，都会被 `validate_analysis_v2()` 判为 `schema_error` 并整份拒收。
2. **标量空值必须写 `null`，不能省略 key；列表空值必须写 `[]`**。这是校验器的强制规则（见 §4.3）。
3. **必须与 `response_format = {"type": "json_object"}` 兼容**——所有内部调用都开了 JSON 模式。
4. **输出 token 预算**：实时路径 `max_tokens=1500`，Batch 路径 `max_completion_tokens=2000`（见 §6）。提示词要求的输出量必须在这个预算内可控。
5. **输入只有正文文本**。没有作者信息、没有发布时间、没有互动数据、没有其它帖子作为对照（见 §5.1）。
6. **语料现实**：中文 X 短帖为主。真实样本 159 条实测：中位 **52 字**，均值 89.1 字，最小 8 字，最大 3999 字；其中 `<30 字 30 条，30–100 字 102 条，100–300 字 26 条，≥300 字 1 条`。
7. **语言**：目标内容是中文 X（Twitter）内容，提示词与输出均为中文。

---

## 1. `ANALYSIS_SYSTEM` 完整原文

**源码位置**：`backend/app/prompts.py:605-611`

```python
ANALYSIS_SYSTEM = (
    "你是中文 X / 社交媒体内容的取证式内容与文风分析器。"
    "你的任务不是评价内容好坏，而是将文本拆成：CONTENT、RHETORIC、"
    "STANCE / ENGAGEMENT、MICROSTYLE 四个层面。只描述文本中真正存在的现象。"
    "单篇出现的东西不能自动认定为用户稳定风格。主题词不能自动认定为口头禅。"
    "平台常规写法不能自动认定为个人 Voice。只输出 JSON。"
)
```

**拼接后的完整字符串（182 字符，无换行）：**

```
你是中文 X / 社交媒体内容的取证式内容与文风分析器。你的任务不是评价内容好坏，而是将文本拆成：CONTENT、RHETORIC、STANCE / ENGAGEMENT、MICROSTYLE 四个层面。只描述文本中真正存在的现象。单篇出现的东西不能自动认定为用户稳定风格。主题词不能自动认定为口头禅。平台常规写法不能自动认定为个人 Voice。只输出 JSON。
```

---

## 2. `ANALYSIS_SCHEMA_HINT` 完整原文

**源码位置**：`backend/app/prompts.py:613-683`

> 注意：这是**伪 JSON**——它不能被 `json.loads()` 解析（例如 `"length_chars": 中文字符数（整数）`、`"tags": ["3-6 个内容标签"]` 都是自然语言占位）。它作为提示词的一部分以纯文本形式发给模型。

````python
ANALYSIS_SCHEMA_HINT = """{
  "topic": "主题，2-8 字",
  "subtopic": "子主题，更具体，2-10 字",
  "content_type": "内容类型：观点表达/经验分享/故事叙述/教程方法/清单盘点/金句语录/吐槽批评/复盘总结/提问互动/情感抒发/资讯转述",
  "core_viewpoint": "核心观点，一句话，不超过 40 字",
  "structure": "结构，用箭头描述，如：钩子→痛点→方法→行动号召",
  "opening_type": "开头方式",
  "ending_type": "结尾方式",
  "tone": "语气",
  "emotion": "主要情绪",
  "sentence_style": "句式特点",
  "length_chars": 中文字符数（整数）,
  "tags": ["3-6 个内容标签"],
  "keywords": ["3-8 个关键词"],

  "content_invariants": {
    "named_entities": ["出现的实体/专有名词，原样保留"],
    "numbers_and_quantities": ["数字与量词，原样保留"],
    "factual_claims": ["事实性陈述"],
    "opinions_or_judgments": ["观点/判断（注意归属：是作者还是引述他人）"],
    "causal_relations": ["因果关系"],
    "uncertainty_or_attribution": ["不确定性表述或信息来源归属"],
    "must_preserve": ["改写时必须原样保留的要点"]
  },

  "rhetorical": {
    "hook_mechanism": "开头的抓取机制",
    "moves": ["修辞动作，如：设问/对比/让步/反讽"],
    "evidence_modes": ["论据类型，如：个人经历/数据/类比"],
    "closing_function": "结尾承担的功能，如：行动号召/留白/回环",
    "information_density": "信息密度描述"
  },

  "stance_engagement": {
    "certainty": "确定性程度",
    "directness": "直接程度",
    "subjectivity": "主观程度",
    "disagreement_intensity": "对抗性/异议强度",
    "hedge_markers": ["弱化标记词"],
    "booster_markers": ["强化标记词"],
    "attitude_markers": ["态度标记"],
    "self_mention": "第一人称使用情况",
    "reader_address": "对读者的称呼方式",
    "engagement_devices": ["互动装置，如：提问/邀请评论"]
  },

  "microstyle": {
    "sentence_rhythm": "句子节奏",
    "fragments_level": "短句/断句程度",
    "sentence_length_pattern": "句长分布模式",
    "discourse_markers": ["话语标记"],
    "conjunction_habits": ["连词习惯"],
    "punctuation_habits": ["标点习惯"],
    "line_break_habit": "换行习惯",
    "parenthetical_habit": "插入语习惯",
    "quote_habit": "引号使用习惯",
    "enumeration_habit": "列举习惯",
    "numeral_habit": "数字写法习惯",
    "emoji_habit": "表情符号习惯",
    "repetition_habit": "重复习惯",
    "distinctive_phrases": ["有辨识度的短语（仅记录，不等于用户口头禅）"],
    "register": "语体，如：口语化/书面化"
  },

  "style_boundary": {
    "likely_voice_markers": ["可能属于个人 Voice 的标记（需多篇验证）"],
    "platform_or_genre_markers": ["平台/体裁通用写法"],
    "topic_bound_markers": ["只在特定主题下出现的表达"],
    "uncertain_markers": ["证据不足、无法判断的表达"]
  }
}"""
````

---

## 3. 动态提示词构造逻辑（完整实现）

### 3.1 `build_analysis_messages` —— 实时与 Batch 共用的唯一 builder

**源码位置**：`backend/app/prompts.py:686-705`

```python
def build_analysis_messages(raw_text: str) -> list[dict[str, str]]:
    """构建单条内容分析请求。

    realtime 与 Batch **必须**复用本函数，不得维护第二套分析 Prompt。
    """
    return [
        {"role": "system", "content": ANALYSIS_SYSTEM},
        {
            "role": "user",
            "content": (
                "请分析下面这段内容，严格按以下 JSON 结构输出，"
                "不要输出任何解释文字或 Markdown 代码块。\n\n"
                f"JSON 结构：\n{ANALYSIS_SCHEMA_HINT}\n\n"
                "待分析内容：\n"
                "------\n"
                f"{raw_text}\n"
                "------"
            ),
        },
    ]
```

**装配后消息结构**：只有 2 条消息，`system` = `ANALYSIS_SYSTEM`，`user` = 固定引导语 + Schema Hint + 分隔符包裹的正文。

**实测体积**：system 182 字符；以 400 字正文构造时 user 为 2591 字符。

### 3.2 渲染示例（真实装配结果，非源码，仅供理解消息形状）

以下使用一条真实短帖作为 `raw_text`（21 字），展示模型实际收到的完整 user 消息：

```
请分析下面这段内容，严格按以下 JSON 结构输出，不要输出任何解释文字或 Markdown 代码块。

JSON 结构：
{
  "topic": "主题，2-8 字",
  ... （即 §2 的 ANALYSIS_SCHEMA_HINT 全文）
}

待分析内容：
------
有没有人跟我一样

两眼一睁

就是 X
------
```

### 3.3 `build_external_analysis_prompt` —— 给外部模型（GPT / Claude / Gemini）的包装

**源码位置**：`backend/app/prompts.py:708-763`

> 这是**协议包装层**，不是第二套提示词：字段定义段直接拼接 `ANALYSIS_SYSTEM` 与 `ANALYSIS_SCHEMA_HINT`，因此外部模型看到的字段定义与内部完全一致。

````python
def build_external_analysis_prompt(
    *,
    schema_version: str,
    schema_hash: str,
    prompt_version: str,
    format_version: int,
    task_count: int,
) -> str:
    """给外部模型（GPT / Claude / Gemini）的批量分析提示词（Stage 12）。

    **只包装，不复制**：字段定义与分析规则直接取自
    ANALYSIS_SYSTEM + ANALYSIS_SCHEMA_HINT —— 全项目只有这一份 ANALYSIS 定义，
    内部分析与外部模型看到的 schema 必然一致。

    纯字符串拼接，0 AI / 0 token。
    """
    return (
        "你是一个批量内容分析执行器。\n\n"
        "我会上传一份 **task JSONL**。请严格按下面的流程处理，"
        "除最终 JSONL 外不要输出任何内容。\n\n"
        "## 输入格式\n"
        "第一行是 _meta（元信息），**必须跳过，不要分析它**。\n"
        "之后每一行是一个任务对象，形如：\n"
        '{"content_id": 123, "external_id": null, '
        '"content_hash": "sha256:...", "text": "正文"}\n\n'
        "## 你要做的\n"
        f"1. 对**每一个**任务行的 `text` 各做一次分析（共 {task_count} 条，以文件实际为准）。\n"
        "2. `content_id` 与 `content_hash` **必须原样回填**，不允许改动、不允许省略、"
        "不允许重新排序后错配。\n"
        "3. 输出 **result JSONL**：一行一个 JSON object，不加 Markdown、不加代码块、"
        "不加解释文字、不加序号。\n\n"
        "## 输出格式\n"
        "第一行必须是 _meta：\n"
        '{"_meta": {"format": "content-studio-analysis-result", '
        f'"format_version": {format_version}, '
        f'"analysis_schema_version": "{schema_version}", '
        f'"schema_hash": "{schema_hash}", '
        f'"prompt_version": "{prompt_version}", '
        '"model": "<你使用的模型名>", "count": <结果条数>}}\n\n'
        "之后每一行：\n"
        '{"content_id": <原样>, "content_hash": "<原样>", "analysis": { ...ANALYSIS V2... }}\n\n'
        "## analysis 字段定义（必须严格遵守，不得增删字段、不得改字段名）\n"
        "----------------\n"
        f"{ANALYSIS_SYSTEM}\n"
        "----------------\n"
        f"{ANALYSIS_SCHEMA_HINT}\n"
        "----------------\n\n"
        "## 硬性约束\n"
        "1. **每个声明的字段都必须出现**。标量没有内容时写 `null`，不要省略该 key；"
        "列表没有内容时写 `[]`。\n"
        "2. **不要新增任何字段**，也不要改写字段名（例如不要写 openingType、opening）。\n"
        "3. 只描述文本中真实存在的现象，不要脑补、不要评价好坏。\n"
        "4. 不要输出 Markdown 代码块围栏。如果确实需要包裹，"
        "整份输出外面只能有一对 ``` 围栏。\n"
        "5. 只输出 JSONL 本身。\n"
    )
````

**实测体积**：整份外部提示词 3389 字符（`task_count=100` 时）。

### 3.4 关于"动态构造"的说明

ANALYSIS 路径的提示词**动态性极弱**：唯一动态内容是被分析的正文 `raw_text`，以及外部包装里的 5 个版本标识 + `task_count`。没有任何检索增强、没有 few-shot 注入、没有作者上下文、没有历史样本。

`build_analysis_messages` 的 docstring 明确约束：**realtime 与 Batch 必须复用本函数，不得维护第二套分析 Prompt**。

---

## 4. 输出字段、Schema、验证规则与字段含义

### 4.1 字段总表（59 路径 / 54 叶子）

"路径数 59" = 54 个叶子 + 5 个对象节点（`content_invariants`、`rhetorical`、`stance_engagement`、`microstyle`、`style_boundary`）。

| 字段路径 | 类型 | 提示词中的含义描述 | 校验规则 |
|---|---|---|---|
| `topic` | 可空标量 | 主题，2-8 字 | 字符串或 null |
| `subtopic` | 可空标量 | 子主题，更具体，2-10 字 | 字符串或 null |
| `content_type` | 可空标量 | 内容类型：观点表达/经验分享/故事叙述/教程方法/清单盘点/金句语录/吐槽批评/复盘总结/提问互动/情感抒发/资讯转述 | 字符串或 null |
| `core_viewpoint` | 可空标量 | 核心观点，一句话，不超过 40 字 | 字符串或 null |
| `structure` | 可空标量 | 结构，用箭头描述，如：钩子→痛点→方法→行动号召 | 字符串或 null |
| `opening_type` | 可空标量 | 开头方式 | 字符串或 null |
| `ending_type` | 可空标量 | 结尾方式 | 字符串或 null |
| `tone` | 可空标量 | 语气 | 字符串或 null |
| `emotion` | 可空标量 | 主要情绪 | 字符串或 null |
| `sentence_style` | 可空标量 | 句式特点 | 字符串或 null |
| `length_chars` | 可空整数 | 中文字符数（整数） | int 或 null（**bool 被显式排除**） |
| `tags` | 列表 | 3-6 个内容标签 | 字符串数组，可 `[]` |
| `keywords` | 列表 | 3-8 个关键词 | 字符串数组，可 `[]` |
| `content_invariants.named_entities` | 列表 | 出现的实体/专有名词，原样保留 | 字符串数组 |
| `content_invariants.numbers_and_quantities` | 列表 | 数字与量词，原样保留 | 字符串数组 |
| `content_invariants.factual_claims` | 列表 | 事实性陈述 | 字符串数组 |
| `content_invariants.opinions_or_judgments` | 列表 | 观点/判断（注意归属：是作者还是引述他人） | 字符串数组 |
| `content_invariants.causal_relations` | 列表 | 因果关系 | 字符串数组 |
| `content_invariants.uncertainty_or_attribution` | 列表 | 不确定性表述或信息来源归属 | 字符串数组 |
| `content_invariants.must_preserve` | 列表 | 改写时必须原样保留的要点 | 字符串数组 |
| `rhetorical.hook_mechanism` | 可空标量 | 开头的抓取机制 | 字符串或 null |
| `rhetorical.moves` | 列表 | 修辞动作，如：设问/对比/让步/反讽 | 字符串数组 |
| `rhetorical.evidence_modes` | 列表 | 论据类型，如：个人经历/数据/类比 | 字符串数组 |
| `rhetorical.closing_function` | 可空标量 | 结尾承担的功能，如：行动号召/留白/回环 | 字符串或 null |
| `rhetorical.information_density` | 可空标量 | 信息密度描述 | 字符串或 null |
| `stance_engagement.certainty` | 可空标量 | 确定性程度 | 字符串或 null |
| `stance_engagement.directness` | 可空标量 | 直接程度 | 字符串或 null |
| `stance_engagement.subjectivity` | 可空标量 | 主观程度 | 字符串或 null |
| `stance_engagement.disagreement_intensity` | 可空标量 | 对抗性/异议强度 | 字符串或 null |
| `stance_engagement.hedge_markers` | 列表 | 弱化标记词 | 字符串数组 |
| `stance_engagement.booster_markers` | 列表 | 强化标记词 | 字符串数组 |
| `stance_engagement.attitude_markers` | 列表 | 态度标记 | 字符串数组 |
| `stance_engagement.self_mention` | 可空标量 | 第一人称使用情况 | 字符串或 null |
| `stance_engagement.reader_address` | 可空标量 | 对读者的称呼方式 | 字符串或 null |
| `stance_engagement.engagement_devices` | 列表 | 互动装置，如：提问/邀请评论 | 字符串数组 |
| `microstyle.sentence_rhythm` | 可空标量 | 句子节奏 | 字符串或 null |
| `microstyle.fragments_level` | 可空标量 | 短句/断句程度 | 字符串或 null |
| `microstyle.sentence_length_pattern` | 可空标量 | 句长分布模式 | 字符串或 null |
| `microstyle.discourse_markers` | 列表 | 话语标记 | 字符串数组 |
| `microstyle.conjunction_habits` | 列表 | 连词习惯 | 字符串数组 |
| `microstyle.punctuation_habits` | 列表 | 标点习惯 | 字符串数组 |
| `microstyle.line_break_habit` | 可空标量 | 换行习惯 | 字符串或 null |
| `microstyle.parenthetical_habit` | 可空标量 | 插入语习惯 | 字符串或 null |
| `microstyle.quote_habit` | 可空标量 | 引号使用习惯 | 字符串或 null |
| `microstyle.enumeration_habit` | 可空标量 | 列举习惯 | 字符串或 null |
| `microstyle.numeral_habit` | 可空标量 | 数字写法习惯 | 字符串或 null |
| `microstyle.emoji_habit` | 可空标量 | 表情符号习惯 | 字符串或 null |
| `microstyle.repetition_habit` | 可空标量 | 重复习惯 | 字符串或 null |
| `microstyle.distinctive_phrases` | 列表 | 有辨识度的短语（仅记录，不等于用户口头禅） | 字符串数组 |
| `microstyle.register` | 可空标量 | 语体，如：口语化/书面化 | 字符串或 null |
| `style_boundary.likely_voice_markers` | 列表 | 可能属于个人 Voice 的标记（需多篇验证） | 字符串数组 |
| `style_boundary.platform_or_genre_markers` | 列表 | 平台/体裁通用写法 | 字符串数组 |
| `style_boundary.topic_bound_markers` | 列表 | 只在特定主题下出现的表达 | 字符串数组 |
| `style_boundary.uncertain_markers` | 列表 | 证据不足、无法判断的表达 | 字符串数组 |

**已脚本验证**：`ANALYSIS_SCHEMA_HINT` 的键路径集合与 `ANALYSIS_V2_SPEC` 的字段路径集合**完全一致（59/59，无多余、无缺失）**。

### 4.2 机器可读 spec（`ANALYSIS_V2_SPEC`）完整源码

**源码位置**：`backend/app/services/analysis_spec.py:39-148`

```python
# 字段类型
SCALAR = "scalar"
LIST = "list"
INTEGER = "integer"
OBJECT = "object"


def _s() -> dict[str, Any]:
    """可空标量。"""
    return {"type": SCALAR, "nullable": True}


def _i() -> dict[str, Any]:
    """可空整数。"""
    return {"type": INTEGER, "nullable": True}


def _l() -> dict[str, Any]:
    """字符串列表（允许空数组）。"""
    return {"type": LIST}


def _o(fields: dict[str, Any]) -> dict[str, Any]:
    return {"type": OBJECT, "fields": fields}


# ------------------------------------------------------------------
# canonical spec —— 与 prompts.ANALYSIS_SCHEMA_HINT 逐字段对齐
# ------------------------------------------------------------------
ANALYSIS_V2_SPEC: dict[str, Any] = {
    "version": ANALYSIS_SCHEMA_VERSION,
    "fields": {
        # ---- 13 个扁平字段 ----
        "topic": _s(),
        "subtopic": _s(),
        "content_type": _s(),
        "core_viewpoint": _s(),
        "structure": _s(),
        "opening_type": _s(),
        "ending_type": _s(),
        "tone": _s(),
        "emotion": _s(),
        "sentence_style": _s(),
        "length_chars": _i(),
        "tags": _l(),
        "keywords": _l(),
        # ---- nested ----
        "content_invariants": _o(
            {
                "named_entities": _l(),
                "numbers_and_quantities": _l(),
                "factual_claims": _l(),
                "opinions_or_judgments": _l(),
                "causal_relations": _l(),
                "uncertainty_or_attribution": _l(),
                "must_preserve": _l(),
            }
        ),
        "rhetorical": _o(
            {
                "hook_mechanism": _s(),
                "moves": _l(),
                "evidence_modes": _l(),
                "closing_function": _s(),
                "information_density": _s(),
            }
        ),
        "stance_engagement": _o(
            {
                "certainty": _s(),
                "directness": _s(),
                "subjectivity": _s(),
                "disagreement_intensity": _s(),
                "hedge_markers": _l(),
                "booster_markers": _l(),
                "attitude_markers": _l(),
                "self_mention": _s(),
                "reader_address": _s(),
                "engagement_devices": _l(),
            }
        ),
        "microstyle": _o(
            {
                "sentence_rhythm": _s(),
                "fragments_level": _s(),
                "sentence_length_pattern": _s(),
                "discourse_markers": _l(),
                "conjunction_habits": _l(),
                "punctuation_habits": _l(),
                "line_break_habit": _s(),
                "parenthetical_habit": _s(),
                "quote_habit": _s(),
                "enumeration_habit": _s(),
                "numeral_habit": _s(),
                "emoji_habit": _s(),
                "repetition_habit": _s(),
                "distinctive_phrases": _l(),
                "register": _s(),
            }
        ),
        "style_boundary": _o(
            {
                "likely_voice_markers": _l(),
                "platform_or_genre_markers": _l(),
                "topic_bound_markers": _l(),
                "uncertain_markers": _l(),
            }
        ),
    },
}
```

### 4.3 校验器完整源码

**源码位置**：`backend/app/services/analysis_spec.py:151-280`

```python
def spec_field_paths(spec: dict[str, Any] | None = None) -> list[str]:
    """展开 spec 的全部字段路径（nested 用 `.` 连接），已排序。"""
    fields = (spec or ANALYSIS_V2_SPEC)["fields"]
    out: list[str] = []

    def walk(prefix: str, node: dict[str, Any]) -> None:
        for name, rule in node.items():
            path = f"{prefix}{name}"
            out.append(path)
            if rule["type"] == OBJECT:
                walk(f"{path}.", rule["fields"])

    walk("", fields)
    return sorted(out)


def canonical_spec_json(spec: dict[str, Any] | None = None) -> str:
    """canonical JSON：sort_keys + 稳定 separators + 非 ASCII 原样。"""
    return json.dumps(
        spec or ANALYSIS_V2_SPEC,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def compute_schema_hash(spec: dict[str, Any] | None = None) -> str:
    """对 canonical spec JSON 的 UTF-8 字节做 SHA256。"""
    payload = canonical_spec_json(spec).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


#: 真实字段定义指纹 —— 只要字段树发生变化，这个值必然变化
ANALYSIS_SCHEMA_HASH = compute_schema_hash()


# ------------------------------------------------------------------
# 严格校验
# ------------------------------------------------------------------

class SpecError(Exception):
    """单条 analysis 不符合 ANALYSIS V2 spec。"""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _check_value(path: str, rule: dict[str, Any], value: Any) -> None:
    kind = rule["type"]

    if kind == SCALAR:
        if value is None:
            if not rule.get("nullable", False):
                raise SpecError(path, "不允许为 null")
            return
        if not isinstance(value, str):
            raise SpecError(path, f"应为字符串，实际 {type(value).__name__}")
        return

    if kind == INTEGER:
        if value is None:
            if not rule.get("nullable", False):
                raise SpecError(path, "不允许为 null")
            return
        # bool 是 int 的子类，必须显式排除
        if isinstance(value, bool) or not isinstance(value, int):
            raise SpecError(path, f"应为整数，实际 {type(value).__name__}")
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


def validate_analysis_v2(data: Any) -> dict[str, Any]:
    """严格校验一份 ANALYSIS V2。

    通过 → 原样返回 data（不做任何改写）；不通过 → 抛 SpecError。
    """
    if not isinstance(data, dict):
        raise SpecError("$", f"顶层应为对象，实际 {type(data).__name__}")
    _check_object("", ANALYSIS_V2_SPEC["fields"], data)
    return data


def is_valid_analysis_v2(data: Any) -> bool:
    try:
        validate_analysis_v2(data)
        return True
    except SpecError:
        return False
```

### 4.4 校验规则要点（对提示词设计的约束）

源码位置：`backend/app/services/analysis_spec.py:1-29`（模块 docstring）

- **所有声明的 key 都是 required**：缺 key → `schema_error`，**不允许靠缺 key 表示空值**。
- **标量空值必须写 `null`**。
- **列表允许 `[]`**。
- **未知 key → `schema_error`**（文件级拒收，**不做静默丢弃**）。
- **不自动修正**：`openingType` / `opening` / 大小写 / 驼峰 一律视为未知 key → `schema_error`。
- 只做「结构 / 类型」校验，**不做业务语义判断**（不给 topic 打分、不猜字段名）。
- **重要**：`validate_analysis_v2()` 目前**只服务外部分析导入**；实时与 Batch 内部路径**不做此校验**（见 §7.1-A3）。

### 4.5 Schema Hash 计算方式

```
ANALYSIS_SCHEMA_HASH = sha256( canonical_spec_json(ANALYSIS_V2_SPEC).encode("utf-8") )
canonical_spec_json  = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

**当前值**：`d1bec6d56a330422ef06ad7908b1186aad87934a28e32075e4a06ee5d7af20f1`

> **改字段树的后果**：只要 `ANALYSIS_V2_SPEC` 变化，hash 必然变化 → 所有已导出的外部任务文件（含旧 hash）回导时会被 `validate_meta()` 拒收（`external_analysis.py:672-674`）。这是改 Schema 最大的连带风险。

---

## 5. Realtime / Batch / 外部分析 的实际调用方式

### 5.1 Realtime（实时分析）

**源码位置**：`backend/app/services/analyzer.py:56-91`

```python
async def analyze_content(content_id: int) -> None:
    """分析单条内容：取原文 → AI 分析 → 落库 → 更新标签与摘要。

    失败时**不写状态**，直接抛出异常；由调用方（后台队列）根据错误是否可恢复
    决定重试还是标记 failed。这样「重试期间」内容会保持 analyzing，而不是被
    过早标记为永久失败。
    """
    provider = get_provider()

    with get_conn(settings.db_path) as conn:
        row = repo.get_content_raw(conn, content_id)
        if row is None:
            return
        raw_text = row["raw_text"]

    data = await provider.chat_json(
        build_analysis_messages(raw_text),
        model=settings.model_analysis,
        temperature=0.2,
        max_tokens=1500,
    )
    norm = normalize_analysis(data, raw_text)

    with get_conn(settings.db_path) as conn:
        # 落库统一走 services.persistence —— 与 Batch、外部分析导入**完全同一套**
        # 副作用（save_analysis / tags / summary / set_status）。
        # raw=data：把 V2 新增字段（content_invariants / rhetorical /
        # stance_engagement / microstyle / style_boundary）原样存进 raw_json。
        persist_analysis_result(
            conn,
            content_id,
            norm,
            raw=data,
            model=settings.model_analysis,
            raw_text=raw_text,
        )
```

**归一化函数**（`analyzer.py:31-53`）—— 只映射 13 个扁平字段，其余原样进 `raw_json`：

```python
def normalize_analysis(data: dict[str, Any], raw_text: str) -> dict[str, Any]:
    """把模型返回规整成稳定结构，容忍字段缺失或类型偏差。"""
    length = data.get("length_chars")
    try:
        length_chars = int(length)
    except (TypeError, ValueError):
        length_chars = len(raw_text)

    return {
        "topic": _s(data.get("topic")),
        "subtopic": _s(data.get("subtopic")),
        "content_type": _s(data.get("content_type")),
        "core_viewpoint": _s(data.get("core_viewpoint")),
        "structure": _s(data.get("structure")),
        "opening_type": _s(data.get("opening_type")),
        "ending_type": _s(data.get("ending_type")),
        "tone": _s(data.get("tone")),
        "emotion": _s(data.get("emotion")),
        "sentence_style": _s(data.get("sentence_style")),
        "length_chars": length_chars,
        "tags": _as_list(data.get("tags"))[:10],
        "keywords": _as_list(data.get("keywords"))[:15],
    }
```

**调用链**：前端「导入」页 → `api/analysis.py` 入队 → `jobs.py:174 await analyze_content(content_id)`（后台 worker，`WORKER_COUNT=2`，可恢复错误最多重试 3 次，指数退避）。

### 5.2 Batch（批量分析）

**源码位置**：`backend/app/services/batch.py:51-76`

```python
def build_jsonl(items: list[tuple[int, str]]) -> tuple[bytes, dict[str, int]]:
    """把 (content_id, raw_text) 列表编成 Batch JSONL。

    - messages 复用 build_analysis_messages —— 与实时分析完全同一套 Prompt；
    - response_format 与 max_completion_tokens 也与实时请求保持一致。
    """
    lines: list[str] = []
    custom_map: dict[str, int] = {}
    for content_id, raw_text in items:
        custom_id = f"c{content_id}"
        custom_map[custom_id] = content_id
        body = {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": settings.model_batch,
                "messages": build_analysis_messages(raw_text),
                "temperature": 0.2,
                "max_completion_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
        }
        lines.append(json.dumps(body, ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return payload, custom_map
```

**结果解析与写回**（`batch.py:150-200`，`_apply_success` 节选）：

```python
        try:
            content = response["body"]["choices"][0]["message"]["content"]
            data = extract_json(content)
            resolved.add(content_id)
            outcome = _save_analysis(content_id, data)
```

**保存路径**（`batch.py:248-270`）：

```python
def _save_analysis(content_id: int, data: dict[str, Any]) -> str | None:
    """与实时分析完全相同的保存路径。

    返回 'mine'（mine 内容）/ 'ok'（其他来源）；
    内容已被删除时返回 None —— 调用方应安全跳过，且不计入成功。
    """
    with get_conn(settings.db_path) as conn:
        raw = repo.get_content_raw(conn, content_id)
        if raw is None:
            logger.info("Batch 结果对应的内容 %s 已不存在，跳过", content_id)
            return None
        is_mine = raw["source"] == "mine"
        norm = normalize_analysis(data, raw["raw_text"])
        # 与 realtime / 外部分析导入**完全同一套**落库副作用
        persist_analysis_result(
            conn,
            content_id,
            norm,
            raw=data,
            model=settings.model_batch,
            raw_text=raw["raw_text"],
        )
        return "mine" if is_mine else "ok"
```

**调用链**：前端「导入」页 `pages/import.tsx:182` → `api.startAnalysis(batchId, mode='batch')` → `POST /api/imports/{id}/analyze?mode=batch`。Batch 使用**独立端点** `AI_BATCH_BASE_URL`；未配置即视为不可用，**严禁静默回退到实时端点**。常驻 `monitor_loop()` 每 45 秒轮询一次。

### 5.3 外部分析（导出任务 + 复制提示词 + 回导）

**任务文件生成**（`backend/app/services/external_analysis.py:357-383`）：

```python
def _meta_block(count: int) -> dict[str, Any]:
    return {
        "format": EXTERNAL_TASK_FORMAT,
        "format_version": EXTERNAL_ANALYSIS_FORMAT_VERSION,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "schema_hash": ANALYSIS_SCHEMA_HASH,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "count": count,
    }


def build_task_jsonl(targets: list[dict[str, Any]]) -> bytes:
    """任务 JSONL：第一行 _meta，之后一行一个任务。UTF-8。"""
    lines = [json.dumps({"_meta": _meta_block(len(targets))}, ensure_ascii=False)]
    for t in targets:
        lines.append(
            json.dumps(
                {
                    "content_id": t["content_id"],
                    "external_id": t["external_id"],
                    "content_hash": t["content_hash"],
                    "text": t["text"],
                },
                ensure_ascii=False,
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")
```

**提示词下发接口**（`backend/app/api/external_analysis.py:127-146`）：

```python
@router.get("/prompt")
def get_external_prompt(count: int = Query(default=0, ge=0)) -> dict:
    """返回可直接复制给外部模型的提示词。**0 AI**。

    提示词里的字段定义直接引用 ANALYSIS_SYSTEM + ANALYSIS_SCHEMA_HINT ——
    全项目只有一份 ANALYSIS 定义，不存在第二份 schema。
    """
    text = build_external_analysis_prompt(
        schema_version=ANALYSIS_SCHEMA_VERSION,
        schema_hash=ANALYSIS_SCHEMA_HASH,
        prompt_version=ANALYSIS_PROMPT_VERSION,
        format_version=EXTERNAL_ANALYSIS_FORMAT_VERSION,
        task_count=count,
    )
    return {
        "prompt": text,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "schema_hash": ANALYSIS_SCHEMA_HASH,
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "format_version": EXTERNAL_ANALYSIS_FORMAT_VERSION,
    }
```

**文件级 `_meta` 校验**（`backend/app/services/external_analysis.py:650-684`）：

```python
def validate_meta(meta: dict[str, Any], *, expected_format: str | None = None) -> list[str]:
    """校验 _meta。返回 **warning** 列表；文件级错误直接抛 FileLevelError。"""
    if not meta:
        raise FileLevelError("缺少 _meta 元信息")

    fmt = meta.get("format")
    wanted = expected_format or EXTERNAL_RESULT_FORMAT
    if fmt != wanted:
        raise FileLevelError(f"文件格式不匹配：期望 {wanted}，实际 {fmt}")

    fv = meta.get("format_version")
    if fv != EXTERNAL_ANALYSIS_FORMAT_VERSION:
        raise FileLevelError(
            f"format_version 不匹配：期望 {EXTERNAL_ANALYSIS_FORMAT_VERSION}，实际 {fv}"
        )

    sv = meta.get("analysis_schema_version")
    if sv != ANALYSIS_SCHEMA_VERSION:
        raise FileLevelError(
            f"analysis_schema_version 不匹配：期望 {ANALYSIS_SCHEMA_VERSION}，实际 {sv}"
        )

    sh = meta.get("schema_hash")
    if sh != ANALYSIS_SCHEMA_HASH:
        raise FileLevelError(f"schema_hash 不匹配：期望 {ANALYSIS_SCHEMA_HASH}，实际 {sh}")

    warnings: list[str] = []
    pv = meta.get("prompt_version")
    if pv != ANALYSIS_PROMPT_VERSION:
        # Prompt 文案版本与字段结构是两件事：只提示，不拒收
        warnings.append(
            f"prompt_version 为 {pv}（当前 {ANALYSIS_PROMPT_VERSION}）—— "
            "Prompt 措辞不同不影响导入，只要字段结构一致。"
        )
    return warnings
```

> **对提示词设计的关键含义**：`prompt_version` 不匹配**只警告不拒收**——说明"只改提示词措辞、不改字段结构"是被明确支持的升级方式。

**单条记录校验**（`backend/app/services/external_analysis.py:689-726`）：

```python
def _classify_record(
    rec: dict[str, Any],
    metas: dict[int, dict[str, Any]],
    duplicate_ids: set[int],
) -> dict[str, Any]:
    cid = rec.get("content_id")

    if rec["shape_error"]:
        return {"classification": CLS_INVALID_JSON, "message": rec["shape_error"]}

    if isinstance(cid, bool) or not isinstance(cid, int):
        return {"classification": CLS_INVALID_JSON, "message": "content_id 缺失或不是整数"}

    if cid in duplicate_ids:
        return {
            "classification": CLS_DUPLICATE,
            "message": "同一文件里同一个 content_id 出现多次，全部不导入",
        }

    analysis = rec.get("analysis")
    try:
        validate_analysis_v2(analysis)
    except SpecError as err:
        return {
            "classification": CLS_SCHEMA_ERROR,
            "field_path": err.path,
            "message": err.message,
        }

    row = metas.get(cid)
    if row is None:
        return {"classification": CLS_NOT_FOUND, "message": f"内容 {cid} 不存在"}

    if not content_hash_matches(row["raw_text"], rec.get("content_hash") or ""):
        return {
            "classification": CLS_HASH_MISMATCH,
            "message": "正文与导出时不一致（content_hash 不匹配）",
        }
```

### 5.4 三条路径共用的落库原语

**源码位置**：`backend/app/services/persistence.py:42-77`

```python
def tag_items_for(normalized: dict[str, Any]) -> list[tuple[str, str]]:
    """从 normalized 结果推导 content_tags 关联项。

    与 realtime / Batch 原来的写法逐字一致：
    tags → kind='tag'，topic → kind='topic'，content_type → kind='content_type'。
    """
    items: list[tuple[str, str]] = [(t, "tag") for t in normalized["tags"]]
    if normalized["topic"]:
        items.append((normalized["topic"], "topic"))
    if normalized["content_type"]:
        items.append((normalized["content_type"], "content_type"))
    return items


def summary_for(normalized: dict[str, Any], raw_text: str) -> str:
    """摘要回填规则：核心观点 → 主题 → 正文前 80 字。"""
    return normalized["core_viewpoint"] or normalized["topic"] or (raw_text or "")[:80]


def persist_analysis_result(
    conn: sqlite3.Connection,
    content_id: int,
    normalized: dict[str, Any],
    raw: dict[str, Any],
    model: str,
    raw_text: str,
) -> None:
    """把一份分析结果落库（不做 normalize —— 调用方必须已经 normalize）。

    参数 raw 是要存进 analyses.raw_json 的**模型原始返回**；
    对内部路径是 provider 返回的 dict，对外部分析是结果文件里的 analysis 对象。
    """
    repo.save_analysis(conn, content_id, normalized, model, raw=raw)
    repo.replace_content_tags(conn, content_id, tag_items_for(normalized))
    repo.set_summary(conn, content_id, summary_for(normalized, raw_text))
    repo.set_status(conn, content_id, "analyzed", None)
```

---

## 6. 模型参数、Token 限制、版本标识

### 6.1 版本标识（当前实际值）

**源码位置**：`backend/app/config.py:90-114`

```python
# Prompt 版本：记录在生成结果里，便于回溯「是哪版提示词产出的」
WRITE_PROMPT_VERSION = "v2"
ANALYSIS_PROMPT_VERSION = "v2"
STYLE_PROMPT_VERSION = "v2"

# ------------------------------------------------------------------
# 分析相关的**四个**独立版本标识（Stage 12 起明确分开，不再混用）
# ------------------------------------------------------------------
#   ANALYSIS_PROMPT_VERSION          Prompt 措辞版本（上面的 "v2"）
#   ANALYSIS_SCHEMA_VERSION          字段结构版本 —— 只要字段树变了就必须改
#   ANALYSIS_SCHEMA_HASH             ANALYSIS_V2_SPEC 的 canonical JSON 指纹
#   EXTERNAL_ANALYSIS_FORMAT_VERSION 外部交换文件的**信封**格式版本
#
# Schema hash 由 services/analysis_spec.py 计算，这里不重复定义。
ANALYSIS_SCHEMA_VERSION = "analysis-v2"
EXTERNAL_ANALYSIS_FORMAT_VERSION = 1

# 外部交换信封标识（写进 task / result JSONL 的 _meta）
EXTERNAL_TASK_FORMAT = "content-studio-analysis-task"
EXTERNAL_RESULT_FORMAT = "content-studio-analysis-result"

# 外部模型名落库前缀：外部结果绝不能被误认为内部模型产出
EXTERNAL_MODEL_PREFIX = "external:"
EXTERNAL_MODEL_UNKNOWN = "external:unknown"
EXTERNAL_MODEL_MAX_LEN = 64

# 批量分析：达到该条数时，导入默认走 Batch Pro（用户仍可强制实时）
BATCH_THRESHOLD = 20
```

| 标识 | 当前值 | 含义 | 谁在校验 |
|---|---|---|---|
| `ANALYSIS_PROMPT_VERSION` | `"v2"` | 提示词措辞版本 | 外部导入时**只警告不拒收** |
| `ANALYSIS_SCHEMA_VERSION` | `"analysis-v2"` | 字段结构版本 | 外部导入时**拒收** |
| `ANALYSIS_SCHEMA_HASH` | `d1bec6d5…f20f1` | spec 的 SHA256（自动计算） | 外部导入时**拒收** |
| `EXTERNAL_ANALYSIS_FORMAT_VERSION` | `1` | 信封格式版本 | 外部导入时**拒收** |

### 6.2 模型配置

**源码位置**：`backend/app/config.py:129-211`

```python
@dataclass
class Settings:
    """运行时配置。所有字段均可通过环境变量覆盖。

    非 frozen：允许测试注入独立数据库路径，避免污染真实库。
    """

    # ---- AI ----
    ai_provider: str = "openai_compat"
    ai_base_url: str = "https://api.xiaomimimo.com/v1"
    ai_api_key: str = ""
    model_analysis: str = "mimo-v2.6-pro"
    model_rewrite: str = "mimo-v2.6-pro"
    model_style: str = "mimo-v2.6-pro"
    model_batch: str = "mimo-v2.6-pro"
    ai_timeout: int = 180

    # ---- Batch（独立端点，不得默认复用实时 Base URL）----
    # MiMo Batch 使用独立 Base URL，例如 https://batch-api-<region>.xiaomimimo.com/v1
    ai_batch_base_url: str = ""
    ai_batch_api_key: str = ""
```

**事实**：`model_analysis` / `model_rewrite` / `model_style` / `model_batch` **四个槽位默认值全部是 `mimo-v2.6-pro`**，均可通过环境变量单独覆盖（`AI_MODEL_ANALYSIS` / `AI_MODEL_REWRITE` / `AI_MODEL_STYLE` / `AI_MODEL_BATCH`）。协议为 **OpenAI 兼容**（`ai/openai_compat.py`）。

### 6.3 各路径的实际参数

| 路径 | 模型 | temperature | token 上限 | JSON 模式 | 重试 |
|---|---|---|---|---|---|
| **Realtime** | `settings.model_analysis` | `0.2` | `max_tokens=1500` | `response_format=json_object` | `chat_json(retries=1)` |
| **Batch** | `settings.model_batch` | `0.2` | `max_completion_tokens=2000` | `response_format={"type":"json_object"}` | 无（单条失败不影响整批） |
| **外部分析** | 外部模型自填 | 外部自定 | 外部自定 | 靠提示词约束（§3.3 硬性约束） | — |

### 6.4 Provider 层参数映射

**源码位置**：`backend/app/ai/openai_compat.py:22-72`

```python
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        if not self.api_key:
            raise AIError(
                "未配置 AI_API_KEY，请在 backend/.env 中填入密钥", retryable=False
            )

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            # MiMo 官方 Chat Completions 使用 max_completion_tokens；
            # 不同时发送 max_tokens，避免依赖旧别名的兼容性。
            payload["max_completion_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._post(client, url, headers, payload)

            # 部分服务不支持 response_format：降级重试
            if resp.status_code == 400 and json_mode:
                body = resp.text.lower()
                if "response_format" in body or "json_object" in body:
                    payload.pop("response_format", None)
                    resp = await self._post(client, url, headers, payload)
```

> **注意**：如果服务端不支持 `response_format`，会**去掉它再发一次**——此时模型不再受 JSON 模式约束，输出可能带解释文字或代码块。

**`chat_json` 的重试与 JSON 提取**（`backend/app/ai/base.py:100-131`）：

```python
    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> dict[str, Any]:
        """发起对话并解析为 JSON 对象；解析失败时重试。"""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            text = await self.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            try:
                return extract_json(text)
            except AIError as err:
                last_err = err
                # 追加纠正提示后重试
                messages = messages + [
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": "只输出合法的 JSON 对象本身，不要包含任何解释文字或 Markdown 代码块。",
                    },
                ]
        raise last_err or AIError("JSON 解析失败")
```

**JSON 提取容错**（`backend/app/ai/base.py:36-79`）：先直接解析 → 再试 ```` ```json ``` ```` 代码块 → 最后截取首个 `{` 到最后一个 `}`。

---

## 7. 已知代码问题与下游依赖（附准确源码位置）

### 7.1 已知问题（来自任务 03 审计，未做任何修改）

| 编号 | 问题 | 证据类型 | 源码位置 |
|---|---|---|---|
| **A1** | 54 个叶子字段中存在多组语义重叠，而 `ANALYSIS_SYSTEM` 仅 182 字、未给任何判别规则。重叠组：`opening_type` vs `rhetorical.hook_mechanism`；`tone` vs `emotion` vs `microstyle.register`；`sentence_style` vs `microstyle.sentence_rhythm` vs `microstyle.sentence_length_pattern` vs `microstyle.fragments_level`；`structure` vs `rhetorical.moves`；`stance_engagement.certainty` vs `subjectivity` vs `disagreement_intensity`；`content_invariants.factual_claims` vs `opinions_or_judgments` | **已证实**（源码） | `prompts.py:605-683` |
| **A2** | Realtime `max_tokens=1500` 与 Batch `max_completion_tokens=2000` 不一致；1500 对 59 字段中文 JSON 有截断风险，截断 → `extract_json` 失败 → 自动**再打一次模型** | **已证实**（源码） | `analyzer.py:75` vs `batch.py:70` |
| **A3** | 内部路径（Realtime / Batch）**完全不做 spec 校验**，只走宽松的 `normalize_analysis()`；缺字段静默变 `None`/`[]` 并进 `raw_json`。`validate_analysis_v2()` 只服务外部导入 | **已证实**（源码） | `analyzer.py:31-53,77`；`batch.py:260`；校验器仅在 `external_analysis.py:710` 使用 |
| **A4** | `ANALYSIS_SCHEMA_HINT` 是伪 JSON，不可解析；字段约束（topic 2-8 字、tags 3-6 个、core_viewpoint ≤40 字）是自然语言软约束，**无任何校验** | **已证实**（源码） | `prompts.py:613-683` |
| **A5** | 无 few-shot、无输出示例、无"信息不足时该填什么"的字段级降级规则 | **已证实**（源码） | `prompts.py:686-705` |
| **A6** | `hook_mechanism` / `moves` / `evidence_modes` / `closing_function` / `information_density` 为开放式自由文本，跨帖措辞不可比 → 画像聚合长尾化 | **推断** | `prompts.py:638-644`；消费方 `repository.py:1171-1273` |
| **A7** | 真实语料中位仅 52 字，强行填 54 字段必然产生幻觉式推断 | **推断**（语料实测） | 语料 `hope yin 小曦 10.6 159条帖子.json` |
| **A8** | 同一帖多次运行的字段取值一致性（重测一致性）**从未测过** | **待实验** | — |
| **X1** | 提示词措辞**没有任何指纹**：改措辞后 `ANALYSIS_PROMPT_VERSION` 仍是 `"v2"`、`schema_hash` 也不变；系统无法察觉内部与外部产出分布已不同 | **已证实**（源码） | `config.py:91-104` |
| **X2** | `analyses` 表**没有 `prompt_version` 列**，无法追溯某条分析是哪版提示词产出的 | **已证实**（源码） | `db.py` analyses 表定义 |

### 7.2 下游依赖（改字段会波及的全部位置）

| 依赖方 | 依赖内容 | 源码位置 |
|---|---|---|
| **落库**：13 个扁平列 | `topic/subtopic/content_type/core_viewpoint/structure/opening_type/ending_type/tone/emotion/sentence_style/length_chars/tags/keywords` | `analyzer.py:39-53` → `repository.save_analysis` |
| **落库**：全部 54 叶子 | 其余 41 个叶子**只存在于 `analyses.raw_json`** | `persistence.py:74` |
| **标签关联** | `tags` → `content_tags(kind='tag')`；`topic` → `kind='topic'`；`content_type` → `kind='content_type'` | `persistence.py:42-53` |
| **摘要回填** | `core_viewpoint` → `topic` → 正文前 80 字 | `persistence.py:56-58` |
| **读取 API** | `repo.get_analysis()` **只返回 13 个扁平列**（不含 `content_invariants`） | `repository.py:1734-1756` |
| **读取 API** | `repo.get_analysis_invariants()` 单独从 `raw_json` 取 `content_invariants` | `repository.py:1759-1781` |
| **Stage 6 作者写作画像** | `WRITING_PROFILE_SECTIONS`：**39 个字段路径**（opening 3 / structure 5 / language 15 / stance 12 / boundary 4） | `repository.py:1171-1273` |
| **Stage 8 流量画像** | `PERFORMANCE_WRITING_SECTIONS`：**35 个字段路径**（与前者交集 35） | `performance_profile.py:466-479` |
| **Stage 9 跨账号洞察** | 同上字段集合 | `cross_account.py` |
| **Stage 10 策略上下文** | `WRITING_PRIORITY`（14 个字段）、`PERFORMANCE_PRIORITY_FIELDS`（10）、`CROSS_PRIORITY_KEYS`（11）、`SOURCE_ANALYSIS_FIELDS`（9） | `rewrite_strategy.py:63-118` |
| **写作检索（BM25）** | 检索语料 = `topic` + `subtopic` + `core_viewpoint` + `tags` + `keywords` | `retriever.py:99-119` |
| **写作注入（单稿）** | `source_analysis.content_invariants`（**注：该路径实际取不到，见 A-W1**） | `prompts.py:377-383`；`rewriter.py:129-131` |
| **写作注入（三候选）** | `content_invariants` 单独注入 | `rewrite_candidates.py:114-118` |

### 7.3 写作侧的相关已知问题（供参考，非本次设计范围）

| 编号 | 问题 | 源码位置 |
|---|---|---|
| **W1** | 单稿写作路径**永不注入 `content_invariants`**：`repo.get_analysis()` 只返回 13 个扁平列，`build_write_messages` 从中取 `content_invariants` 恒为 `None`；三候选路径却用 `get_analysis_invariants()` 单独取了 | `rewriter.py:129-131` + `repository.py:1740-1756` + `prompts.py:377-383`（对照 `rewrite_candidates.py:114-118`） |
| **W3** | `build_candidate_write_messages()` 不接收 `reference_profile`，导致 `_WRITE_BASE` 中 REFERENCE_AUTHOR_PROFILE 规则在该路径永不触发 | `prompts.py:477-579` |

### 7.4 改动字段树的连锁影响清单

若设计方案涉及**新增 / 删除 / 重命名字段**（不仅仅改措辞），以下位置必须同步修改，且会触发兼容性风险：

1. `backend/app/prompts.py` — `ANALYSIS_SCHEMA_HINT`
2. `backend/app/services/analysis_spec.py` — `ANALYSIS_V2_SPEC`（→ `ANALYSIS_SCHEMA_HASH` 自动变化）
3. `backend/app/repository.py` — `WRITING_PROFILE_SECTIONS`（39 字段）
4. `backend/app/services/performance_profile.py` — `PERFORMANCE_WRITING_SECTIONS`（35 字段）
5. `backend/app/services/cross_account.py` — 跨账号维度
6. `backend/app/services/rewrite_strategy.py` — 4 个优先级字段列表
7. `backend/app/services/analyzer.py` — `normalize_analysis()`（如需新增扁平列）
8. `backend/app/db.py` — `analyses` 表列（如需新增列 → 涉及 schema 迁移）
9. 前端所有消费画像的页面（作者详情 / 洞察 / 写作策略卡）
10. `backend/tests/test_regression.py` — 测试里的 `v2_raw` 固定样本

**兼容性风险**：`ANALYSIS_SCHEMA_HASH` 变化后，**所有已导出的外部任务文件（含旧 hash）回导时会被 `validate_meta()` 整份拒收**（`external_analysis.py:672-674`）。

---

## 8. 交接清单自检

| 你要求的材料 | 章节 | 状态 |
|---|---|---|
| 1. `ANALYSIS_SYSTEM` 完整原文 | §1 | ✅ 原样 |
| 2. `ANALYSIS_SCHEMA_HINT` 完整原文 | §2 | ✅ 原样 |
| 3. `build_analysis_messages` 完整实现及动态构造逻辑 | §3.1 / §3.3 | ✅ 原样 |
| 4. 全部输出字段、Schema、验证规则、字段含义 | §4.1 / §4.2 / §4.3 / §4.4 / §4.5 | ✅ 原样 |
| 5. Realtime / Batch / 外部分析的实际调用方式 | §5.1 / §5.2 / §5.3 / §5.4 | ✅ 原样 |
| 6. 模型参数、Token 限制、版本标识 | §6.1 / §6.2 / §6.3 / §6.4 | ✅ 原样 |
| 7. 已知代码问题与下游依赖（附源码位置） | §7.1 / §7.2 / §7.3 / §7.4 | ✅ 附准确位置 |

**本轮未修改任何业务代码、数据库或现有提示词，未调用任何 AI。**
