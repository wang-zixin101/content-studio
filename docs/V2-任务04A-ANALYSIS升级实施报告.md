# Content Studio V2 · 任务 04-A：ANALYSIS 升级实施报告

**实施日期**：2026-10-10
**依据**：《Content Studio V2 · ANALYSIS 正式设计 v1》
**范围**：只做 04-A。未改写 STYLE / WRITE 的提示词业务规则；STYLE 与 WRITE 的**接口**保持可用（并有回归测试守住）。

---

## 0. 一句话结论

新版 ANALYSIS 合同已落到代码、数据库与全部下游消费点；**后端 810/810 测试通过、前端 150/150 通过**，并用真实中文 X 样本（真实 MiMo 调用）跑通了「导入 → 分析 → 画像 → 洞察 → 检索 → 写作 → 个人风格」完整闭环。

四条 AI 调用链**均不人为指定输出 Token 上限**，使用供应商默认值（`mimo-v2.6-pro` 为 131072）；已用拦截真实 HTTP 请求体的方式逐条验证（§3.2）。

过程中额外发现并修复了一个**用户可见的既有缺陷**：7 个字段被后端算出却被响应模型静默丢弃（详情页永远显示「无作者 / 未参与风格学习」）。

---

## 1. 改了什么

### 1.1 新增文件

| 文件 | 作用 |
|---|---|
| `backend/app/services/write_context.py` | **写作资料组装器**：单稿与三候选共用的取数入口（修 D1/D4 的结构性前提） |
| `e2e_task04a_analysis.py` | 端到端验收脚本（隔离库 + 真实样本 + 真实 AI，产出交付物） |
| `verify_task04a.py` | 只读体检脚本：证据逐字性 / 合同有效性 / 无残缺 analyzed / 路径连通 / 指标闭环，可反复跑 |
| `check_output_limit.py` | 完成 Token 上限检查：拦截四条链的真实 HTTP 请求体，**不调用付费模型** |

### 1.2 契约层（新）

**`backend/app/services/analysis_spec.py`（重写）** —— 唯一的 canonical 定义：

- 字段树：**6 基础字段 + 4 模块**（`content` / `writing` / `style` / `reuse`），共 **37 条路径**、**12 个可索引标量**；
- **枚举词典 12 组**（`content_type` / `opening_type` / `structure_type` / `ending_type` / `tone` / `register` / `directness` / `certainty` / `rhythm` / `claim_kind` / `move_type` / `marker_type`），逐字取自设计文档 §3.2；
- 新增 `OBJECT_LIST` 类型（`moves[]` / `markers[]` / `patterns[]` / `claims[]`），元素的键集合必须**恰好**等于声明；
- 严格校验器 `validate_analysis()`：缺 key、多 key、类型错、**枚举越界**一律拒收，绝不自动修；
- `BLANK_TEMPLATE` 由 spec 生成 —— 与提示词里的模板做**逐字段比对**（防漂移最强的一条断言）；
- `ANALYSIS_SCHEMA_HASH` 覆盖 **version + fields + enums**：改枚举 = 改合同 = 指纹必变。

**`backend/app/prompts.py`**：

- `ANALYSIS_SYSTEM` / `ANALYSIS_SCHEMA_HINT` 换成设计定稿文本，**逐字保留业务规则**（未做任何工程侧改写）；
- `build_analysis_messages()` 按设计 §4：原文以 `{"text": <JSON>}` 序列化注入，分隔符与指令类字句无法改变任务；
- `build_external_analysis_prompt()` 继续**只包装不复制** —— 换合同只改被引用的常量。

### 1.3 数据库（v7 → v8）

**`backend/app/db.py`**：

| 变更 | 说明 |
|---|---|
| `analyses` 换合同 | 13 个扁平列 → **12 个索引列 + `analysis_json`（完整规范 JSON）+ 4 个版本列**（`prompt_version` / `schema_version` / `schema_hash` / `model`） |
| `contents.source_meta` | 新增 JSON 列，收容 `conversation_id` / `language` / `reply_to_username` / `hashtags` / `urls` / `media_urls` |
| `content_performance.updated_at` | 记录表现数据最后一次刷新时间 |
| `style_profile.prompt_version` | 记录档案由哪版 STYLE 提示词产出（D6） |
| 迁移 `_rebuild_analyses_for_v8()` | 0 行 → 直接重建；**>0 行 → 改名封存为 `analyses_legacy_v2`（绝不 DELETE）**，内容退回 `unprocessed` 让 UI 如实显示 |

### 1.4 三条分析入口统一（D2 / D3 / D6）

**`services/analyzer.py`（重写）**：

- `request_analysis()` 成为内部唯一分析入口：**调用 → 截断检测 → JSON 解析 → 严格校验**；
- **截断即硬失败**：`finish_reason ∈ {length, max_tokens, max_output_tokens}` → 立刻抛出，报错里带 `reasoning_tokens`；**不重发**（同参数重发只会再截断）；
- **合同不符**：带着**具体错误路径**（如 `style.register`）纠正重试一次，仍失败则抛出；
- `compute_length_chars()` 本机计算字数，模型合同里**没有** `length_chars`。

**`config.py`**：

```python
# 完成 Token：四条链一律**不主动传入** max_tokens / max_completion_tokens，
# 使用供应商默认值（mimo-v2.6-pro 为 131072，即该模型最大输出）
ANALYSIS_DETECT_TRUNCATION = True   # finish_reason 命中 length/max_tokens → 判失败
ANALYSIS_MAX_ATTEMPTS = 2           # 只对结构性问题生效，截断不在此列
ANALYSIS_PROMPT_VERSION = "v3"      # WRITE / STYLE 仍为 v2（本轮不改它们的提示词）
ANALYSIS_SCHEMA_VERSION = "analysis-v3"
```

**为什么不设上限**（小米 MiMo 开放平台官方 OpenAI 兼容文档
`platform.xiaomimimo.com/docs/en-US/api/chat/openai-api`）：

> `max_completion_tokens` … An upper bound for the number of tokens that can be
> generated for a completion, **including visible output tokens and reasoning tokens**.
> mimo-v2.6-pro: **default 131072**　Required range: [1, 131072]

1. 我们无法预知推理模型需要多少 token —— reasoning 逐次浮动，写死任何值都可能比默认更早截断；
2. 供应商默认已经是该模型的最大输出，自己写只会更小或持平；
3. 上游若调整默认值，不写死的一方自动跟着受益。

**截断仍然被严密监控，且不依赖上限值本身**：`finish_reason` 命中
`length / max_tokens / max_output_tokens` 即判失败并报出真实用量
（`completion_tokens` + `reasoning_tokens`）。

> **修正记录（2026-10-10 当天）**：本报告初版曾把四条链统一写成 `16384`。
> 那仍然是「人为设了一个上限」，与「不人为限制」的要求相悖 —— 已全部移除。
> 详细检查结果见 §3.2。

**`services/batch.py`**：`_save_analysis` 加严格校验 + 截断检测；失败明确标 failed，**绝不以 analyzed 落库**。

**`services/external_analysis.py`**：改用 `validate_analysis`，落库走同一 `persist_analysis_result`。信封格式未变，`format_version` 仍为 1。

**`ai/base.py` / `ai/openai_compat.py`**：新增 `chat_with_meta()` 返回 `finish_reason` + `usage` —— 推理模型的截断必须可观测，否则「HTTP 200」会冒充成功。

### 1.5 下游消费点（全部切到新字段）

| 消费点 | 旧 | 新 |
|---|---|---|
| 检索语料 | `core_viewpoint` | `core_message` |
| 作者写作画像 | 39 路径 / 5 段 | **14 路径 / 6 段**（`content` 定位 + 开头 + 结构 + 结尾 + 语言 + 语气） |
| 流量画像 | 35 路径 / 4 段 | **13 路径 / 6 段**；二元组合右端改为 `writing.opening_type` / `writing.structure_type` / `style.tone` |
| 跨作者洞察 | 9 维度 | **12 维度**（2 内容 + 10 写法） |
| 写作优先级表 | 旧路径 | 全部换新路径；`select_source_analysis` 支持点分路径读取 |
| 内容详情页 | 固定 13 扁平字段 | **四组结果 + 原文证据 + 来源信息** |
| 作者流量画像面板 | 只有基线 / 内容维度 / 写法维度 / 组合 | 追加**互动指标表**（6 个指标的中位与门槛）与**按书写目标看案例**（四个目标各给高/低案例 + 写法特征）—— 只读展示，不新增页面、不新增工作流 |

**路径取值新增数组下钻**：`analysis_path_values()` 支持 `writing.moves[].type` 语法 —— 对象列表里的 `type` 才能真正参与跨帖计数。

### 1.6 D9：次级指标闭环

| 位置 | 新增内容 |
|---|---|
| 流量画像 | `metric_baselines`（曝光 + 5 个比率的账号内中位 / Top10 / Bottom40 门槛 / 未知计数）、`goal_samples`（曝光 / 讨论 / 传播 / 收藏 四个目标的高/低案例，带结构化写法特征） |
| 跨作者洞察 | `engagement_dimensions`（作者等权：先作者内中位，再对作者取中位） |
| 写作参照 | `strategy_context.performance_goal` —— 单稿与三候选**都**带上当次目标对应的指标基线与案例 |
| 接口 | `RewriteIn` / `RewriteCandidateIn` 新增 `goal`（默认 `exposure`，不传行为不变） |

**比率纪律**：仅 `view_count > 0` 且该计数非 NULL 时才算；分母为 0/未知 → `None`（不是 0）；`0` 是已知值，可以覆盖旧值；**不取最大值**（计数会下降，取最大值会伪造从未发生的峰值）；**不合成爆款总分**。

### 1.7 D1 / D4：两个写作出口

> **范围说明**：设计文档 §6 把 D1/D4 列为「与 WRITE 阶段同步解决」，
> 但同一份文档 §5 又要求 04-A 阶段的 WRITE 单稿「完整读取并注入 source analysis、
> must_preserve、参考作者和适用表现洞察」。
> 本轮**没有改写任何 WRITE 提示词的业务规则**（`_WRITE_BASE` / 状态段 / `_WRITE_TAIL` /
> 候选角色描述一字未动），只修**资料组装**——让既有的提示词规则能真正拿到它期望的数据。
> 这正是 04-A 能做的、也必须做的最小改动：不改规则，只把数据接通。

- 抽出 `_write_context_parts()`，单稿与三候选**共用**；
- 单稿现在注入**完整素材分析**（含 `content.must_preserve` / `claims` / `context_limits`）+ 参考作者画像 + 历史数据规律；
- 三候选补齐 `reference_profile` 参数 —— `_WRITE_BASE` 里那 6 条参考画像规则不再是无出口的死指令。

### 1.8 D8：状态唯一入口

`repository.set_analysis_status()` 成为唯一状态转换入口，`status` 由 `analysis_state` **推导**。旧的 `set_status` / `set_analysis_state` 保留为包装函数（不再能只写一列）。所有成对写入的调用点已合并。

### 1.9 Xporter 元数据与重复导入

- 导入时保留 6 个扩展字段（实测：`conversation_id` 100%、`language` 100%、`media_urls` 100%、`urls` 70%、`hashtags` 40%、`reply_to_username` 30% 的真实文件存在）；
- **语言过滤**：中文画像默认排除**明确标注为非中文**的帖子；`und`（未判定）与缺失照常保留，排除条数写入 `excluded_non_chinese`；
- **重复导入**：内容仍然不重复导入、不调用模型，但表现数据**按采集时间刷新** —— 旧快照不得覆盖新快照、NULL 不得抹掉已知计数、`0` 可以覆盖；扩展元数据按键合并，**作者与正式列一律不动**。

### 1.10 顺手修掉的既有缺陷（本轮新发现）

`ContentOut` 少声明了 7 个字段（`author_username` / `style_eligible` / `posted_at` / `source_url` / `external_id` / `post_type` / `import_batch_id`）。Pydantic 会静默丢弃未声明的键，因此：

- 内容详情页的「@作者」**永远显示「无作者」**；
- 「风格学习资格」**永远显示「未参与」**；
- 内容行的风格学习徽标**从不出现**。

已补齐，并新增 `TestTask04AResponseCompleteness` —— 用**递归键比对**守住这一类缺陷（任何生产出来的键没被响应模型接住，测试立刻失败）。

---

## 2. 测试结果

### 2.1 后端

```
cd backend
./.venv/Scripts/python.exe -m unittest tests.test_regression
Ran 810 tests in 290s
OK
```

| 分组 | 数量 | 说明 |
|---|---|---|
| 原有回归 | 765 | 全部通过（其中约 112 条因合同换代同步改写） |
| 04-A 专项 + 上限检查 | 45 | 见下表 |
| 隔离库 | 全程 `tempfile.mkdtemp()` | **生产库清单见 §2.3** |

04-A 专项分组：

| 测试类 | 条数 | 覆盖 |
|---|---|---|
| `TestTask04AAnalysisPipeline` | 5 | 完整合同落库、**截断硬失败且不重试**、合同错误的**指定路径**纠正重试、两次失败不落 analyzed、索引列/本机字数/版本指纹、**不指定完成上限** |
| `TestTask04AStateEntry` | 3 | 双列同步、非法状态拒收、状态映射完整 |
| `TestTask04AImportMetadata` | 4 | 6 个扩展字段落库、缺失不补 NULL、重复导入不碰作者、语言判定语义（含坏 JSON 不抛异常） |
| `TestTask04ADownstreamWiring` | 2 | **每个消费点的每条路径都必须存在于合同里**（防静默取空） |
| `TestTask04AMetricsAndGoals` | 5 | 比率的分母/分子门槛、零覆盖标记、目标榜排序、写参照投影、缺数据不凑榜 |
| `TestTask04AWriteParity` | 4 | 单稿拿到 `must_preserve` + 参考画像 + 历史规律；候选拿到参考画像规则；两条路径共用组装器；关闭开关即不注入 |
| `TestTask04AMigration` | 3 | 空表重建、**旧行封存不删**、幂等 |
| `TestTask04AContentDetail` | 2 | 四组结果 + 来源元数据送达前端 |
| `TestTask04AResponseCompleteness` | 5 | **递归键比对**：响应模型不得丢弃任何生产键 |
| `TestTask04APromptFidelity` | 5 | 提示词忠实度、枚举必须全在 hint 里、外部包装复用同一份定义 |
| `TestNoArtificialOutputLimit` | 5 | **四条链拦截真实 HTTP 请求体**，断言没有输出 Token 上限（见 §3.2） |
| `TestRealtimePayload` | 2 | 默认不发上限字段 / 显式传值时仍可发送 |

个别共享夹具也升级为契约级断言，例如：

- `test_blank_template_matches_schema_hint` —— 提示词模板与 spec 逐字段相同；
- `test_field_count = 37`、`test_scalar_paths_are_index_columns`；
- `test_enums_are_in_the_hint` —— 12 组枚举的**每个取值**都必须在提示词里出现。

### 2.2 前端

```
cd frontend
npx tsc --noEmit     # 无输出 = 通过
npm test             # tests 150 / pass 150 / fail 0
```

### 2.3 生产库与发布文件

| 对象 | 结果 |
|---|---|
| `data/content_studio.db` | `b867f12e68afb9e5cdc12fb96610160bf70493eb967679c181a702532d0e05ab`，mtime 仍为 2026-10-07 19:38 —— **未变** |
| Desktop 库 | 本轮全程 `mode=ro` 只读访问；未执行任何写入 |
| 已发布 EXE | **未重建** |

---

## 3. 数据流验收（真实样本 · 真实模型）

**跑法**：

```
./backend/.venv/Scripts/python.exe e2e_task04a_analysis.py --write --style
./backend/.venv/Scripts/python.exe verify_task04a.py <隔离库路径>
```

**体检结果：5/5 通过**

| 检查项 | 结果 |
|---|---|
| 证据逐字性（每条 evidence 必须是原文的连续子串） | **394 / 394** |
| 合同有效性（库里存的 JSON 仍能通过严格校验） | 22 条全部有效，0 无效 |
| 无残缺 analyzed（不许「缺字段却标 analyzed」） | 0 条残缺 |
| 路径全在合同内（画像/洞察/检索/写参照引用 0 条合同外路径） | ✓ |
| 次级指标闭环（6 指标 + 4 目标） | ✓ |

**闭环逐环结果**

| 环节 | 结果 |
|---|---|
| 导入 | benchmark 精选 12 条 + mine 4 条；语料池追加 989 条（**0 AI 调用**）；库内共 1006 条 |
| ANALYSIS | 12/12 benchmark + 4/4 mine 通过严格校验并落库；另有一次供应商侧安全过滤（见 §4.3） |
| 作者写作画像（@btcmos） | 样本 **11** 条，**6 个分段 14 个指标全部非空** |
| 单账号流量画像（@btcmos） | eligible **500** / 已分析 11；**6 条指标基线全部有中位值**；**4 个书写目标全部可列案例** |
| 跨作者洞察 | 内容维度 2 + 写法维度 10 + 互动维度 6；写法维度中 7 个有实际取值；**互动维度 6 个全部有跨账号中位** |
| BM25 检索 | query=content 1，命中 5 条，top score 1.0 |
| WRITE 单稿 | ✓（真实生成，落库为 rewrite） |
| WRITE 三候选 | ✓ A / B / C 三段全部解析成功 |
| 保存人工终稿 | ✓ `saved_content_id=1010`，`edit_ratio=0.5345` |
| STYLE | ✓ **v4，基于 7 条我的内容** |

**多作者画像的真实覆盖**

| 作者 | 画像样本 | 非空指标 | 流量 eligible | 指标基线 | 目标案例 |
|---|---|---|---|---|---|
| @btcmos（香港老雷） | 11 | 14/14 | 500 | 6/6 | 4/4 |
| @wangchangfu88（王长富） | 4 | 14/14 | 498 | 6/6 | 4/4 |
| @lchngwi1993771 | 4 | 14/14 | 4 | 6/6 | 样本不足（如实标记） |
| @kitesrox / @lixinbao_x | 1 | 14/14 | 1 | 6/6 | 样本不足（如实标记） |

> 「样本不足」是**如实标记**而不是凑数：只有当该指标有 ≥5 条可算样本时才列案例。

**交付物**

- `task04a_deliverables/真实样本-ANALYSIS-输出.md` —— 每条样本的原文 + 表现指标 + **完整规范 ANALYSIS JSON**
- `task04a_deliverables/数据流验收结果.json` —— 上面各表的原始数据

### 3.1 一个值得记下来的质量观察
第 2 条样本（`毁掉我人生的十个网站`，源文件本身就只到「五、」）的 `context_limits` 里，模型自己写了：

> 「四条链接均为 t.co 短链，未提供跳转后的实际网站内容，无法判断其主题或为何被称『毁掉人生』」
> 「原文在『五、』处截断，第六至第十项及第五项正文缺失」

我核对过源文件 —— **确实如此**（123 字符，止于「五、」）。也就是说模型不仅没有编造，还主动把「看不到什么」标了出来。这正是 `context_limits` 这个字段被设计出来的目的。

### 3.2 完成 Token 上限检查（四条链的实际请求参数）

**结论：5/5 通过 —— 四条 AI 调用链 + Batch 通道，实际发出去的请求体里都没有任何输出 Token 上限字段。**

跑法（**不调用付费模型**：httpx 客户端被换成记录型假客户端，请求不出本机）：

```
./backend/.venv/Scripts/python.exe check_output_limit.py \
    --json task04a_deliverables/完成Token上限检查.json
```

| 链路 | 请求数 | 端点 | 实际 payload 键 |
|---|---|---|---|
| ① ANALYSIS（Realtime） | 1 | `api.xiaomimimo.com/v1/chat/completions` | `messages, model, response_format, stream, temperature` |
| ② WRITE 单稿 | 1 | 同上 | `messages, model, stream, temperature` |
| ③ WRITE 三候选 | 1 | 同上 | `messages, model, stream, temperature` |
| ④ STYLE 风格档案 | 1 | 同上 | `messages, model, response_format, stream, temperature` |
| ⑤ Batch JSONL（ANALYSIS 第二通道） | 1 | `batch-api-cn.xiaomimimo.com` | `messages, model, response_format, temperature` |

被检查禁止出现的键（全部未出现）：
`max_completion_tokens`、`max_tokens`、`max_output_tokens`、`max_new_tokens`、`maxTokens`。

**排查范围**（确认没有隐藏上限）：

| 位置 | 结论 |
|---|---|
| `openai_compat.chat_with_meta`（唯一的实时请求构造点） | `if max_tokens:` 守卫；业务代码不传 → 字段不写入 |
| `batch.build_jsonl`（唯一的 Batch 请求构造点） | 已移除该字段 |
| `openai_compat._post` / 4xx 降级重试 | 只删 `response_format`，不引入上限 |
| `chat_json` / `chat` / `chat_with_meta` 三个透传层 | `max_tokens` 默认 `None`，逐层透传不注入 |
| 全仓库 grep `MAX_COMPLETION_TOKENS` | 0 处残留（四个常量已删除） |

**相关测试**（替换了原先「配额不得低于某固定值」的断言）：

| 测试 | 断言 |
|---|---|
| `TestNoArtificialOutputLimit`（5 条） | 四条链各跑一次真实调用，**拦截真实 HTTP 请求体**，断言不含任何上限字段；并断言四条链的 payload 键集合一致 |
| `TestRealtimePayload.test_default_request_sends_no_output_limit` | 默认不传 → 请求体无该字段 |
| `TestRealtimePayload.test_explicit_override_still_works` | 显式传值时仍会发送（能力保留，但无业务调用方使用） |
| `test_batch_request_has_json_mode_and_no_output_limit` | Batch JSONL 不含上限字段 |
| `test_valid_output_persists_full_contract`（分析链） | `calls[0]["max_tokens"] is None` |

> 保留的能力：`max_tokens` 作为透传参数仍在签名里，默认 `None`。
> 留它只为「测试可注入」与「极端情况下显式封顶」，不是业务调优旋钮 ——
> 这一点已写进 `AIProvider` 的类文档，防止将来有人又把它当配额用。

---

## 4. 尚未解决的问题

### 4.1 属于 04-B（STYLE）的范围 —— 本轮**未动**

| # | 问题 | 现状 |
|---|---|---|
| D5 | `_build_summary()` 丢 7 个键（`confidence` / `uncertain_patterns` / `global_voice` / `signature_patterns` / `anti_style` / `x_modes` / `topic_bound_terms`），且 `if summary…elif profile` 让全量 JSON 在摘要非空时永不发送 | 原样保留，未改 STYLE 提示词与摘要逻辑 |
| D7 | `MIN_CONTENT=3` × `COLD_START_MAX=4` → 我的内容为 3–4 条时会白算一版档案 | 原样保留 |

### 4.2 属于 04-C（WRITE）的范围 —— 本轮**未动**

| # | 问题 | 现状 |
|---|---|---|
| — | WRITE 提示词本身的业务规则、`_WRITE_BASE` 的 9 级优先级表述、候选三角色描述 | 一字未改 |

### 4.3 本轮已修但**行为有变**，需要先生知晓

| 项 | 旧行为 | 新行为 |
|---|---|---|
| 重复导入的表现数据 | 整条 skip，数字永不更新 | 内容不动，**数字按采集时间刷新** |
| 四条 AI 调用线的完成 Token | 分析 1500 / 单稿 2000 / 候选 4000 / 风格 3000（各自写死） | **一律不指定，使用供应商默认 131072**（该模型最大输出） |
| 分析失败判定 | JSON 解析失败才算失败 | **截断也算失败**（以前会被当成「解析失败」静默重试或标记 failed，原因不可见） |
| `analyses` 结构 | 13 扁平列 + `raw_json` | 12 索引列 + `analysis_json` + 4 版本列 |
| 非中文帖子 | 照常进入画像 | 中文画像默认排除（条数如实上报） |
| 「无作者 / 未参与」显示 | 永远显示（字段被响应模型静默丢弃） | 正常显示真实值 |

### 4.4 本轮**我的操作失误**（如实上报）

在补跑「此前因截断/安全过滤失败的两条内容」时，我的筛选条件写成了 `c.id > 12 AND 未分析`。这条 SQL 把**语料池里的 989 条 benchmark 内容**也匹配了进去（它们本来就都未分析）。进程在 600 秒超时时被终止，实际多跑了 **5 条**（content 16–20，均为香港老雷的真实帖子）。

**影响**：
- 多消耗 5 次分析调用（约 ¥0.1–0.2 量级）；
- 这 5 条结果本身**合法、合规、落在隔离库里**，没有污染任何真实数据；
- 副作用是 @btcmos 的写作画像样本从 6 条变成 11 条 —— 证据反而更充足了；
- 未触碰生产库与 Desktop 库。

已把该脚本的补跑入口改为**显式传 ID**，避免同类误伤。

### 4.5 一条真实观察：供应商侧的安全过滤

第一次跑分析时，有一条 mine 样本返回的是：

```
The request was rejected because it was considered high risk
```

这不是合同问题，是**供应商的内容安全策略**拒绝了这次请求。系统正确地把它当成失败、**没有落 analyzed**。后来重跑该条时又正常通过了 —— 说明这个过滤是**不稳定**的。

需要留意：真实使用中会遇到个别帖子被上游拒答。当前的处置是「明确失败 + 可重试」，不是「静默跳过」——这是刻意的。

### 4.6 一条真实观察：模型偶尔会借用邻近枚举
补跑时出现一次：

```
writing.moves[0].type: 「反常识」不在枚举词典内
（允许：设问、反问、对比、反转、递进、因果、让步、反讽、类比、重复、列举、悬念、故事化、称呼读者、号召互动、其他）
```

`反常识` 是 **`opening_type` 的合法取值**，被模型误用到了 `moves[].type`。提示词里同时存在 12 组枚举，这种「串味」会偶发。

处置链路是正确的：严格校验拒收 → 带着**具体字段路径**纠正重试一次 → 仍不合格则标 failed。**不会静默接受**，也不会污染统计口径。若后续发现频率偏高，可在 04-B/04-C 阶段考虑在提示词里为每个数组元素重复强调它自己的枚举（属于提示词调整，不在本轮范围）。

### 4.7 一条真实观察：temperature 在 thinking 模式下**被服务端强制覆盖**

查官方文档核对 Token 参数时顺带发现的（**本轮未修**，因为先生明确限定「只改这项配置」）：

> In thinking mode, the mimo-v2.6-flash, **mimo-v2.6-pro**, … models **do not support
> customizing the temperature and top_p parameters**. Even if these parameters are passed in,
> the actual effective values will be forcibly set by the model to its recommended
> default values of **1.0 and 0.95**.
> —— `platform.xiaomimimo.com/docs/en-US/api/chat/openai-api`

而 `thinking.type` 对 `mimo-v2.6-pro` 的默认值就是 `enabled`。

**这意味着我们代码里设的 temperature 全部无效**：

| 调用链 | 代码里写的 | 服务端实际生效 |
|---|---|---|
| ANALYSIS | `0.2` | `1.0` |
| WRITE 单稿 | `0.85` | `1.0` |
| WRITE 三候选 | `0.9` | `1.0` |
| STYLE | `0.4` | `1.0` |

**影响判断（需要先生决策，我不擅自改）**：

1. 我们**以为**在给分析压低随机性（0.2），实际上没有 —— 这很可能就是 §4.6「模型偶尔借用邻近枚举」频发的原因之一；
2. WRITE 与三候选本来就想要高随机性（0.85/0.9），服务端 1.0 与预期接近，影响小；
3. 若要真正控制随机性，要么在请求里显式传 `thinking: {type: "disabled"}`（代价：失去推理能力，
   而推理正是本模型做取证式分析的核心），要么接受 1.0 并在**输出结构上**做约束
   （现在的严格校验 + 纠正重试已经是这个方向）。
4. 也可以先不动 —— 实测产出的证据逐字率是 394/394，说明当前随机性下质量并未失控。

### 4.8 需要留意的运行特性

- **单次分析变慢了**：不再限制输出长度后，推理模型会思考得更久。实测单条 **30 秒–4 分钟**（与并发、网络、内容长度有关）。批量场景应当用 Batch Pro，不要指望实时路径做大批量。
- **枚举是硬约束**：模型输出近义词会被拒收，随后纠正重试一次；两次都不合格就标 failed。宁可失败也不让统计口径碎成一地 —— 这是刻意的取舍。
- **上限由上游决定**：我们不再传 `max_completion_tokens`，因此输出长度完全取决于服务端默认值（当前 131072）。若上游某天调低这个默认值，截断检测会立刻在日志里暴露，不会静默降质。

### 4.9 已知的、本轮**故意没做**的事

| 项 | 理由 |
|---|---|
| 旧 V2 分析结果的历史迁移 | 设计文档明确「现有真实业务数据无需历史迁移」；两处真实库的 `analyses` 均为 0 行。迁移逻辑按「封存不删」实现，有测试覆盖 |
| 多模态分析（看图 / 看视频） | 设计文档明确「本次不开发多模态 AI 分析」；媒体地址只保存供查看，分析里由 `context_limits` 标注看不到 |
| `conversation_id` 还原 thread | 设计文档明确「并不足以完整还原 thread，不要把相同 conversation_id 的文本串接」 |
| 内容详情页之外的前端新页面 | 设计文档明确「不新增独立页面」 |
| 画像侧 UI 展示次级指标 | 后端已全部产出并有响应模型声明；前端画像面板的**展示**属于 UI 工作，未在本轮范围内。**数据链路是通的**（`metric_baselines` / `goal_samples` / `engagement_dimensions` 均有类型与守卫测试） |
| temperature 覆盖问题（§4.7） | 先生明确限定本轮「只修改这项配置及必要测试，不修改提示词、业务逻辑」 |

## 5. 改动文件清单（含指纹）

sha256 前 16 位：

```
e4f3c701743ac20d  backend/app/config.py
f3c8f34c02673f45  backend/app/prompts.py
a8a52bb7c4b67b3a  backend/app/services/analysis_spec.py
f17fd0a2e741dd42  backend/app/services/analyzer.py
bfe54fcbdbe7280f  backend/app/services/persistence.py
eff9dcaed2d2f068  backend/app/db.py
ae6dc7f2025e69c6  backend/app/repository.py
38714b6ff234d8e1  backend/app/schemas.py
99cb7a15e7751fe1  backend/app/api/imports.py
14c9db391c3bd361  backend/app/api/rewrites.py
d5a1fe063bec9625  backend/app/api/contents.py
7385f0e0327f0ba2  backend/app/jobs.py
15bab595fce205c6  backend/app/services/batch.py
288d427451c4a030  backend/app/services/external_analysis.py
187d94a1c29c66f3  backend/app/services/performance_profile.py
ba7489322340622e  backend/app/services/cross_account.py
da30914d561991cd  backend/app/services/retriever.py
30691a555d4a6ede  backend/app/services/rewrite_strategy.py
d8c69fe799b67534  backend/app/services/rewriter.py
acba76f9b638de71  backend/app/services/rewrite_candidates.py
bb6c5e0cc6762960  backend/app/services/style_profile.py
5104ca6e0706e5f7  backend/app/services/write_context.py           （新增）
f065811df5c60b4c  backend/app/services/importer.py
3e21f3b8b68e050c  backend/app/ai/base.py
8063634f33ae7b75  backend/app/ai/openai_compat.py
80600fb717f76d59  frontend/src/lib/types.ts
d39b2010b8865d43  frontend/src/components/content-detail-sheet.tsx
6d7f6cd7c0ee5104  frontend/src/components/author-performance-panel.tsx
c11cb30c408c38c0  backend/tests/test_regression.py
f76c3f7573bb3f6a  e2e_task04a_analysis.py                       （新增）
6f58b35e099a112b  verify_task04a.py                             （新增）
1dd37c41c5e75b66  check_output_limit.py                         （新增）
```

**未改动**：`data/content_studio.db`（`b867f12e68afb9e5cdc12fb96610160bf70493eb967679c181a702532d0e05ab`，mtime 仍为 2026-10-07 19:38）、Desktop 库、任何已发布产物。本轮**未重建 Desktop 发布版**。

---

*本报告由实施过程实跑数据生成。所有数字均可在报告中给出的命令下复现。*
