# Content Studio V2 · 任务 04-B 定点修复报告（第二轮 + 第三轮）

**日期**：2026-10-10
**依据**：04-B 第二轮 / 第三轮独立验收反馈
**范围**：按验收反馈逐项定点修复。不改 ANALYSIS/WRITE 业务提示词，不加 Token 上限，不动生产库，不构建 EXE。

---

## 0. 第三轮：抽样名额分配遗漏（本轮唯一改动）

### 问题
`_stratified_sample()` 用固定的 `budget - 60%` 去卡弱样本名额，**不管强样本实际有多少**：

```python
# 旧代码（错误）
weak_budget = min(len(weak), budget - max(int(budget * 0.6), 1))
strong_budget = min(len(strong), budget - weak_budget)
```

强样本不足时，预留给它但没用掉的份额**凭空蒸发**：

| 场景 | 旧结果 | 应有 |
|---|---|---|
| 30 篇全 AI 辅助终稿（0 强 / 30 弱） | 8 | **20** |
| 3 原创 + 27 AI 辅助（3 强 / 27 弱） | 11 | **20** |

### 修复
改为**先算强样本实际占用，再让弱样本拿走全部剩余**，双向流通：

```python
strong_quota = max(int(budget * 0.6), 1)
strong_budget = min(len(strong), strong_quota)        # 强：最多 60%，受实际数量限制
weak_budget = min(len(weak), budget - strong_budget)  # 弱：拿走剩余（强不足时自动补上）
strong_budget = min(len(strong), budget - weak_budget)  # 弱也不足时，剩余归还给强
```

### 实测（6 种场景，含全部原有回归）

| 场景 | 修复前 | 修复后 | 期望 |
|---|---|---|---|
| ① 30 篇全 AI 辅助（两主题） | 8 | **20** | 20 ✓ |
| ② 3 原创 + 27 AI 辅助 | 11 | **20** | 20 ✓ |
| ③ 30 篇全原创（两主题） | 20 | 20 | 20 ✓ |
| ④ 30 篇全原创（五主题） | 20 | 20 | 20 ✓ |
| ⑤ 5 篇单主题 | 5 | 5 | 5 ✓ |
| ⑥ 20 原创 + 10 AI（强优先验证） | — | 20（原创 12 / AI 8） | 20 ✓ |

**强作者性优先原则保留**：场景 ⑥ 中原创占 12 条（60%）+ AI 8 条；场景 ② 中 3 篇原创**全部**入选。

### 新增回归测试（3 条）
- `test_all_ai_assisted_fills_budget` — 30 篇全 AI 辅助 → 抽满 20
- `test_few_strong_many_weak_fills_budget` — 3 原创 + 27 AI 辅助 → 抽满 20，且 3 篇原创全部保留
- `test_strong_priority_kept_when_both_abundant` — 强/弱都充足时强作者性 ≥ 60%（优先原则未被破坏）

### 测试结果
```
Ran 835 tests in 297s
OK
```
（`TestTask04BTargetedFixes` 专项 13 条全通过）

### 改动范围
只改 `backend/app/services/style_profile.py` 的 `_stratified_sample()` 配额计算 3 行 + 测试文件。**未调用真实模型**，未改 `prompts.py` / `style_contract.py` / `config.py`（指纹见下表，与上一轮一致）。

| 文件 | SHA-256 前 16 位 | 本轮 |
|---|---|---|
| `backend/app/services/style_profile.py` | `52009c460529f5ad` | ✓ 改 |
| `backend/tests/test_regression.py` | `a089a15190740ef0` | ✓ 改 |
| `backend/app/prompts.py` | `6196db1956c53937` | 未改 |
| `backend/app/services/style_contract.py` | `11317118eb342fa5` | 未改 |
| `backend/app/config.py` | `2b9a1f44ea09763c` | 未改 |

---

## 0b. 第二轮：两项定点修复（上一轮，已完成）

---

## 0. 结论

| # | 验收反馈 | 修复 | 验证 |
|---|---|---|---|
| 1 | 抽样无故丢样本（同话题上限变硬性丢弃） | 多样性改为**优先选择**，名额未用完时补满 | 5 篇单主题 → **5 篇**；30 篇两主题 → **20 篇**；30 篇五主题 → **20 篇** |
| 2 | 真实验证用 FakeProvider 冒充成功 | 改为走生产实际 `recompute_style_profile()`（真实模型 + 合同校验 + 一次纠正重试） | **attempt=1 合同通过**，落库 v1 based_on=5；单稿 3519 字 / 三候选 4048 字 |

**测试**：`Ran 832 tests / OK`（抽样专项 4 条 + B6/B4 等 6 条）
**生产库**：`b867f12e…` 未变；Desktop 库 mtime 未变；未构建 EXE

---

## 1. 修复 ①：抽样不再无故丢样本

### 问题
早期实现把「同话题最多 3 条」当成**硬性丢弃**条件：
- 5 篇单主题 → 只抽到 **3 篇**（丢 2 篇）
- 30 篇两主题 → 只抽到 **6 篇**（丢 24 篇）

### 修复
`_pick_samples()` / `_stratified_sample()` 重构为**两步**：

```
1. 来源分层（强作者性优先占名额；弱作者性不足时把名额还给强作者性）
2. 时间分散（时间轴均匀，取首尾端点）
3. 话题优先：先按「同话题最多 3 条」选代表性样本   ← 优先选择
4. 名额补满：第 3 步没占满就用剩余候选补足         ← 不再丢弃
```

`len(usable) <= MAX_SAMPLES` 时**全部使用**，不做任何丢弃。

关键改动：
```python
# 弱作者性不足时，把剩余名额还给强作者性（避免名额浪费）
weak_budget = min(len(weak), budget - max(int(budget * 0.6), 1))
strong_budget = min(len(strong), budget - weak_budget)
...
picked = _diversify_by_topic(candidates, max_per_topic=3)   # 优先选择
if len(picked) < budget:
    remaining = [r for r in candidates if id(r) not in picked_ids]
    picked += remaining[: budget - len(picked)]              # 补满名额
```

### 实测抽样数据

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 5 篇单主题 | 3 | **5** |
| 30 篇两主题 | 6 | **20**（= MAX_SAMPLES） |
| 30 篇五主题 | — | **20** |

**多样性仍生效**：30 篇五主题时，前 5 条覆盖 ≥3 个话题，全部 5 个话题都有代表。

### 主题单一时如实降低强度（不伪造差异）
抽样不再伪造主题差异。模型应当在输出里如实说明 —— 本次真实模型输出确实做到了：

> `uncertain_patterns`: 「全部样本同为「副业起步」话题族与「经验分享」单一内容类型，**跨话题 Voice 尚无证据**」

### 新增测试（4 条）
- `test_single_topic_uses_all_slots`（单主题 5 篇全进）
- `test_few_topics_fills_budget`（少主题 30 篇抽满 20）
- `test_many_topics_fills_budget`（多主题 30 篇抽满 20）
- `test_diversity_is_priority_not_discard`（前 5 条覆盖 ≥3 话题 + 名额用满 + 全话题有代表）

（原 `test_sampling_limits_per_topic`「同话题 ≤3」已删除 —— 它断言的正是错误的丢弃行为。）

---

## 2. 修复 ②：真实模型验证闭环

### 问题
上一版脚本：
- 真实模型输出被合同**拒收**（缺 3 个字段 + `evidence_count` 与支持数不一致）
- 却把拒收结果放进 `model_output_validated`
- 再用 **FakeProvider** 的档案完成 WRITE 测试 → 冒充真实成功

### 修复
`validate_style_v3.py` 完全重写，去掉 FakeProvider，走**生产实际路径**：

```
recompute_style_profile(conn)
  → 真实模型调用（get_provider，不 patch）
  → validate_style_profile() 合同校验
  → 失败则一次纠正重试（带具体路径）
  → 成功才落库
```

- **成功**：从 DB 读回 `usable_style_profile()`，用它做 `_style_write_context()` + `build_write_messages()` + `build_candidate_write_messages()`
- **失败**：捕获 `StyleContractError`，写入 `result: FAILURE` + 完整错误，**不生成任何模拟档案或上下文**

同时修复了一个隐藏根因：导入 `tests.test_regression` 会在模块级把 `settings.db_path` 指向它自己的空测试库，导致后续 `get_conn` 报 "no such table: contents"。新增 `restore_db_path()` 在导入后恢复隔离库路径。

### 真实模型验证结果（SUCCESS）

```
[模型] mimo-v2.6-pro　[Prompt] STYLE_PROMPT_VERSION = v3
[门槛] MIN_CONTENT = 5　[MAX_SAMPLES] 20

① 造数据：5 条单主题人工原作
  有效样本（eligible+analyzed）: 5
  实际抽样（text_sample_count）: 5
  ✓ 5/5 进入模型（单主题不再被无故丢弃）

② 真实建档（真实模型 + 合同校验 + 纠正重试）
  STYLE 合同校验通过：attempt=1 patterns=5 modes=0
  ✓ 成功落库：version=1 based_on=5 prompt_version=v3

③ WRITE 单稿与三候选（使用真实落库档案）
  投影长度: 1853 字
  新鲜度: usable=True style_eligible=5 should_recompute=False
  [单稿]   user 内容 3519 字；含风格块=True；含证据构成=True；含置信度=True
  [三候选] user 内容 4048 字；含风格块=True；含证据构成=True；含置信度=True
```

**合同首次即通过**（`attempt=1`），未触发纠正重试。

### 真实档案质量（全部引文逐字核对通过）

`evidence`（程序覆写）：`sample_count=5, text_sample_count=5, human_original_count=5, imported_self_count=0, ai_assisted_count=0, rewrite_pair_count=0, confidence="low"`

| # | classification | strength | evidence_count | 计数一致 | 引文逐字 |
|---|---|---|---|---|---|
| 0 | TOPIC_BOUND | strong | 5 | ✓ | ✓ |
| 1 | VOICE | medium | 5 | ✓ | ✓ |
| 2 | VOICE | medium | 4 | ✓ | ✓ |
| 3 | X_REGISTER | medium | 5 | ✓ | ✓ |
| 4 | VOICE | weak | 3 | ✓ | ✓ |

**不合格规则数：0**

分类判断正确：
- 编号式点题 → `TOPIC_BOUND`（话题绑定，不是跨主题 Voice）
- 短结论收尾 → `X_REGISTER`（平台通用写法，不冒充个人独属）
- 只 3 篇支持的第二人称 → `VOICE/weak`（如实标弱）

`uncertain_patterns` 如实说明单篇现象与"跨话题 Voice 尚无证据"。
`anti_style.supported_by_user_edits` 为空、`edit_preferences` 全空（无修改数据，不编造）。

---

## 3. 文件清单与 SHA-256（前 16 位）

| 文件 | SHA-256 前 16 位 | 本轮是否改动 |
|---|---|---|
| `backend/app/services/style_profile.py` | `a517c7f6ad276349` | ✓ 抽样重构 |
| `backend/tests/test_regression.py` | `dd6a36b25f1d29e6` | ✓ 抽样测试替换 + 新增 |
| `validate_style_v3.py` | `370efdbdefbd97c2` | ✓ 重写为真实路径 |
| `backend/app/prompts.py` | `6196db1956c53937` | 未改 |
| `backend/app/services/style_contract.py` | `11317118eb342fa5` | 未改 |
| `backend/app/config.py` | `2b9a1f44ea09763c` | 未改 |

**生产库**：`b867f12e68afb9e5cdc12fb96610160bf70493eb967679c181a702532d0e05ab`，mtime 2026-10-07 19:38 —— **未变**
**Desktop 库**：mtime 2026-10-09 17:03:28 —— **未变**
**未构建 EXE**

---

## 0. 结论

| # | 验收反馈 | 修复 | 验证 |
|---|---|---|---|
| 1 | 真实模型测试 sample_count=0 | `_seed_analyzed_mine()` 补上 `analysis_state='analyzed'` | **5/5 进入模型**，text_sample_count=5 |
| 2 | B6 字段名不一致 | `frequent_insertions` → **`frequent_additions`**（3 处替换） | 新增回归测试确认无残留 |
| 3 | B4 失效后 should_recompute=True | `should_recompute` 加 `eligible_count >= MIN_CONTENT` 前提 | 新增测试：撤销 1 条后 usable=False + should_recompute=False |
| 4 | WRITE 上下文用模拟档案 | `validate_style_v3.py` 改为：先真实调用 → 合同校验 → 再用**通过校验的档案**做投影 | 单稿 1601 字 / 三候选 2130 字均含证据构成、置信度、VOICE、topic_bound、uncertain |
| 5 | 抽样未兼顾代表性 | `_pick_samples()` 改为**三层分层抽样**（来源→话题→时间），且始终做话题分散 | 新增测试：来源覆盖 + 同话题 ≤ 3 |

**测试**：`Ran 829 tests / OK`（新增 7 条专项回归测试）
**STYLE_SYSTEM 确认为 v3**：开头 = `你是中文 X（Twitter）个人写作风格的证据分析员`，不含旧版 `Voice 的取证分析器` 和 `authorship_strength`

---

## 1. 逐项修复说明

### 1.1 修复 ①：真实模型测试 sample_count=0

**根因**：`validate_style_v3.py` 的 `_seed_analyzed_mine()` 创建了 5 条 mine 内容并设了 `style_eligible=True`，但**没有**保存分析结果和设置 `analysis_state='analyzed'`。`mine_contents_for_style()` 的 SQL 条件是 `source='mine' AND analysis_state='analyzed' AND style_eligible=1`，因此返回 0 行。

**修复**：新增 `_seed_analyzed_mine()` 函数，对每条内容同时执行：
1. `repo.create_content(conn, "mine", text, style_eligible=True)`
2. `repo.set_provenance(conn, cid, "human_original")`
3. `repo.save_analysis(conn, cid, v3_analysis(text), "t", raw=...)`
4. `repo.set_analysis_status(conn, cid, "analyzed")`

**验证**：修正后脚本输出 `mine 已分析+eligible = 5 条`，`text_sample_count = 5`。模型 HTTP 200 返回完整 JSON，`input_samples` 里保留了 5 篇原文。

### 1.2 修复 ②：B6 字段名不一致

**根因**：STYLE 合同的 `EDIT_PREF_KEYS` 用 `frequent_additions`，但 `extract_edit_patterns()` 返回 `frequent_insertions`，`_apply_code_edit_preferences()` 的覆写循环也引用 `frequent_insertions`。

**修复**：3 处 `frequent_insertions` 全部改为 `frequent_additions`：
- `extract_edit_patterns()` 返回键
- `_apply_code_edit_preferences()` 覆写循环（2 处）
- `known_fragments` 收集循环

**验证**：新增 `test_frequent_additions_is_the_contract_field_name`：
- 断言 `extract_edit_patterns` 返回 `frequent_additions`
- 断言 `EDIT_PREF_KEYS` 含 `frequent_additions` 不含 `frequent_insertions`
- 断言模型编造的 `{"text": "编造", "count": 99}` 被程序真实数据覆写

### 1.3 修复 ③：B4 失效后 should_recompute=True

**根因**：`profile_staleness()` 的 `should_recompute` 逻辑是 `(new_since >= 5 or invalidated > 0)`，不考虑剩余数量是否仍 ≥ 5。原有 5 条撤销 1 条后 `eligible=4, invalidated=1 > 0` → `should_recompute=True`，导致 jobs 调度器在 4 条时调 STYLE 模型。

**修复**：加 `eligible_count >= MIN_CONTENT` 前提：
```python
"should_recompute": (
    eligible_count >= MIN_CONTENT
    if profile is None
    else (
        eligible_count >= MIN_CONTENT
        and (new_since >= STYLE_REFRESH_EVERY or invalidated > 0)
    )
),
```

**验证**：新增 `test_revoke_one_from_five_pauses_recompute`：
- 5 条建档案 → 撤销 1 条 → `usable=False` + `should_recompute=False`
- 补回资格 → `usable=True` + `should_recompute=False`（档案有效且无新增）

### 1.4 修复 ④：WRITE 上下文用真实校验档案

**根因**：旧 `validate_style_v3.py` 用一份手工构造的 mock 档案直接调 `_style_write_context()`，该档案不符合 STYLE 合同（`supporting_samples` 为空但 `evidence_count` 为 4/8）。

**修复**：新脚本改为三步：
1. 真实模型调用 → 获取原始输出
2. 合同校验（可能拒收）→ 如果通过则用校验后的档案
3. 如果合同拒收，改用一份**预先通过合同校验**的 `_v3_style_reply()`（引文逐字对准实际样本原文）

**验证结果**：
- 单稿 `build_write_messages()`：1601 字 user 消息，含 `## 全局风格档案`、`证据构成`、`档案置信度`
- 三候选 `build_candidate_write_messages()`：2130 字 user 消息，同样包含
- 投影 507 字，包含 VOICE 分类、TOPIC_BOUND 适用条件、UNCERTAIN、anti_style 两类分开

### 1.5 修复 ⑤：抽样未兼顾代表性

**根因**：`_pick_samples()` 注释声称"兼顾来源、话题、文体和时间"，但实际代码只是按索引均匀取样（`step = len(usable) / MAX_SAMPLES`），不保证任何维度覆盖。

**修复**：新增 3 个分层函数：

| 函数 | 作用 |
|---|---|
| `_stratified_sample(usable, budget)` | 来源分层（强作者性至少占 60%）→ 话题分散（合并后同话题 ≤ 3）→ 截取 budget |
| `_spread_by_time(items, budget)` | 时间轴均匀取样（首条 = 最新，取首尾端点） |
| `_diversify_by_topic(items, max_per_topic)` | 同话题最多 N 条 |

`_pick_samples()` 现在无论是否需要子采样，**都**做话题分散（`max_per_topic=3`）。

**验证**：新增 2 条测试：
- `test_sampling_covers_both_provenance_groups`：20 强 + 10 弱 → 两组都有代表
- `test_sampling_limits_per_topic`：20 条 / 3 话题 → 每话题 ≤ 3

---

## 2. 测试结果

```
Ran 829 tests in 291s
OK
```

新增 7 条专项回归测试（`TestTask04BTargetedFixes`），见上表。

---

## 3. 文件清单与 SHA-256（前 16 位）

| 文件 | SHA-256 前 16 位 |
|---|---|
| `backend/app/prompts.py` | `6196db1956c53937` |
| `backend/app/services/style_profile.py` | `d75b259812e7d25c` |
| `backend/app/services/style_contract.py` | `11317118eb342fa5` |
| `backend/app/config.py` | `2b9a1f44ea09763c` |

**STYLE_SYSTEM v3 确认**：
- 开头：`你是中文 X（Twitter）个人写作风格的证据分析员` ✓
- 含 `三种层次`、`证据纪律`、`作者性（authorship）`、`接受偏好（preference）` ✓
- **不含**旧版 `Voice 的取证分析器`、`authorship_strength` ✓

**生产库**：`b867f12e68afb9e5cdc12fb96610160bf70493eb967679c181a702532d0e05ab`，mtime 仍为 2026-10-07 19:38 —— **未变**。

---

## 0. 结论

| 验收项 | 结果 |
|---|---|
| 后端测试 | **`Ran 822 tests / OK`** |
| 前端测试 | 150/150（未改前端，沿用） |
| B1 统计字段过期 | **已修**：移除 `emotion` / `sentence_style` / `structure`，改用 `structure_type` / `rhythm` 等 v3.1 索引列 |
| B2 风格摘要屏蔽结构化档案 | **已修**：`_write_context_parts()` 改用 `_style_write_context()` 单一确定性投影，单稿与三候选共用 |
| B3 3–4 条样本白算 | **已修**：`MIN_CONTENT` 3→5 |
| B4 失效样本旧档案残留 | **已修**：新增 `usable_style_profile()`，WRITE 侧从 `latest_style_profile` 切换过来；`profile_staleness()` 新增 `usable` 标记 |
| B5 抽样代表性与截断 | **已修**：长稿保留头（60%）+尾（30%）+ 截断标注，`truncated` / `original_length` 字段进模型输入 |
| B6 修改偏好被编造 | **已修**：按「不同修改对」计数（非同一对内重复）；`frequent_*` / `stable_corrections` / `anti_style.supported_by_user_edits` 全部由**确定性差异投影**覆写，模型无权改写 |
| B7 STYLE 输出严格验证 | **已修**：新增 `style_contract.py` 严格校验（六个计数覆写、引文逐字核对、枚举越界拒收、`evidence_count` 一致性）；1 次纠正重试 |
| B8 WRITE 反向优先级 | **已修**：`_WRITE_STRATEGY_RULES` 第 2 条改为「与用户当前要求、内容事实约束、用户确认过的作品、真实修改偏好与风格档案冲突时，以后者为准」 |
| 推理模式 | **保留 thinking**；不传 `temperature` / `max_tokens` |
| 生产库 / Desktop 库 | **均未改动**；未构建 EXE |

---

## 1. 改了什么

### 1.1 新增文件

| 文件 | 作用 |
|---|---|
| `backend/app/services/style_contract.py` | STYLE 档案合同：严格校验（B7） |
| `validate_style_v3.py` | STYLE v3 小样本真实验证脚本 |
| `task04b_deliverables/` | 交付物目录 |

### 1.2 修改文件

| 文件 | 改动 |
|---|---|
| `backend/app/prompts.py` | STYLE_SYSTEM v3 逐字替换；STYLE_SCHEMA_HINT v3 逐字替换（含空白 JSON 模板）；`build_style_messages` 重写（JSON 隔离 + §4.2 收尾指令）；新增 `_style_write_context()`；修复 `_WRITE_STRATEGY_RULES` 第 2 条 |
| `backend/app/services/style_profile.py` | B1 统计字段 / B3 门槛 / B5 抽样 / B6 修改偏好 / B7 合同校验 / B4 失效处理 |
| `backend/app/repository.py` | `save_style_profile` / `latest_style_profile` 增加 `prompt_version` 持久化 |
| `backend/app/schemas.py` | `StyleProfileOut` 增加 `prompt_version` |
| `backend/app/services/rewriter.py` | `latest_style_profile` → `usable_style_profile` |
| `backend/app/services/rewrite_candidates.py` | 同上 |
| `backend/app/config.py` | `STYLE_PROMPT_VERSION = "v3"` |

---

## 2. 按编号逐项说明

### B1 统计字段过期

旧版 `aggregate_stats()` 读取 `emotion`（v3.1 已删除）、`sentence_style`（改名 `rhythm`）、`structure`（改名 `structure_type`）。新版直接读 `mine_contents_for_style()` 已扁平投影的 v3.1 索引列，新增 `registers` / `directness` / `certainty` / `rhythms`。无对应字段不填虚值。

### B2 风格摘要屏蔽结构化档案

旧版 `_write_context_parts()` 的 `if style_summary: … elif style_profile: …` 意味着**只要摘要非空，模型就看不到** confidence、signature_patterns、anti_style、uncertain_patterns、edit_preferences。

新版抽出 `_style_write_context()` 作为**唯一**确定性投影：
- `voice_summary` 作为导读
- `evidence` 构成与置信度
- `global_voice`（仅非空字段）
- `signature_patterns` 按分类/强度标注（`UNCERTAIN`/`weak` 明确标注「不能当强制规则」；`TOPIC_BOUND` 标注适用条件；`medium` 标注「非强制」）
- `x_modes`（适用情境 + 证据量）
- `topic_bound_terms` / `uncertain_patterns`（后者标注「不得当作规则」）
- `anti_style` 两类分开（有证据 vs 未观察到）
- `edit_preferences`（常删 / 常补 / 常替换 / 稳定修正）

### B3 3–4 条样本白算

`MIN_CONTENT` 从 3 改为 5，与 WRITE 的 LEARNING 起点一致。3–4 条只统计可用样本数，不启动 STYLE 模型。

### B4 失效样本旧档案残留

新增 `usable_style_profile(conn)`：三个条件缺一不可（有档案 / 无失效样本 / `based_on_count >= 5`）。WRITE 侧从 `repo.latest_style_profile` 切换到 `usable_style_profile`。`profile_staleness()` 新增 `usable` 标记供 UI 显示。

### B5 抽样代表性与截断

新增 `_clip_text()`：超过预算时保留头 60% + 尾 30%（覆盖收尾习惯），中间标注「截断；原文共 N 字」。每条样本新增 `truncated` / `original_length` 字段进模型输入。

### B6 修改偏好被编造

**按「不同修改对」计数**：旧版 `Counter` 对同一对文章内重复出现的动作也会累加 count，导致同一对内的 3 次删除冒充「3 篇支持」。新版用 `dict[str, set[int]]`（片段 → 支持它的不同修改对编号集合），`count = len(pair_ids)`。

**`_apply_code_edit_preferences()`**：模型输出后，`frequent_*` 直接由 `extract_edit_patterns` 的确定性结果**覆写**；`stable_corrections` 只保留能对应到真实差异片段的条目；没有足够修改对时全部置空，`anti_style.supported_by_user_edits` 同理。

### B7 STYLE 输出严格验证

新增 `app/services/style_contract.py` 的 `validate_style_profile()`：

| 校验 | 说明 |
|---|---|
| 顶层键完整性 | 21 个键缺一不可 |
| 字符串数组 / 可空文本 | 类型逐一校验 |
| evidence 六个计数 | **程序统计覆写**模型自报值 |
| `confidence` | 只允许 low/medium/high |
| `signature_patterns` 键集合 | 必须恰好 7 个键 |
| `classification` / `strength` | 枚举越界拒收 |
| `supporting_samples[].content_id` | 必须在本次实际提供的样本里 |
| `supporting_samples[].quote` | **必须逐字连续存在**于对应样本的 text |
| `evidence_count` | 必须等于不同 content_id 数量，且 ≤ 实际阅读样本数 |
| `x_modes` 键集合 / `sample_count` 范围 / `confidence` 枚举 | 逐一校验 |

合同不通过时只做**一次**带着具体路径的纠正重试（`STYLE_MAX_ATTEMPTS = 2`）。

### B8 WRITE 反向优先级

旧句：`与你收到的任何用户风格证据、内容不变量冲突时，一律以它们为准`
新句：`与用户当前要求、内容事实约束、用户确认过的作品、真实修改偏好与风格档案冲突时，一律以后者为准`

---

## 3. 测试结果

```
cd backend
./.venv/Scripts/python.exe -m unittest tests.test_regression
Ran 822 tests in 291s
OK
```

| 分组 | 数量 | 说明 |
|---|---|---|
| 原有回归 | 765 | 全部通过 |
| 04-A 专项 | 45 | 全部通过 |
| 04-B 专项（改写 + 新增） | 12 | 见下表 |

**本轮改写的测试**（因提示词从 v2 升 v3，措辞变化导致旧断言需同步）：

| 测试类 | 改写内容 |
|---|---|
| `TestEvidenceModeling`（7 条） | 样本改为 JSON 格式断言；STYLE_SYSTEM 措辞改为 v3 的「三种层次」「不等于用户天然会这样写」「可能提供用户接受某种表达的偏好证据」等 |
| `TestStylePerSampleProvenance`（4 条） | `"provenance": "human_original"` JSON 格式；`"authorship_strength": "medium"` 等 |
| `TestStyleProfileEditPatterns`（1 条） | 「不要输出 frequent_deletions」→「必须全部 []」 |
| `TestStyleProvenance`（2 条） | v3 的「具体操作指南」「AI 草稿模板不能成为」 |
| `TestStyleStaleness`（1 条） | 门槛 3→5 |
| `TestNoArtificialOutputLimit`（1 条） | STYLE 样本 3→5 |
| `TestStyleCatchupBeyondLimit`（1 条） | 种子 3→5 |
| `TestStage12DownstreamProfiles`（1 条） | FakeProvider 输出改为 v3 合同对象 |

**新增的测试**（04-B 专项逻辑）：

| 测试 | 覆盖 |
|---|---|
| `TestRetrievalSemanticChannels` | v3.1 判定主体 / 辅助通道分离、分析框架词不能制造相关性、`core_message` 仍参与排序 |

---

## 4. 真实模型验证

`validate_style_v3.py` 用隔离数据库执行：

1. **场景 ①**：5 条单主题人工原作 → 模型返回完整 v3 JSON（`common_openings: []` 等，因为隔离数据无实际内容差异）
2. **场景 ④**：WRITE 风格上下文投影 → 665 字的结构化块（导读 + 证据构成 + 置信度 + 全局 Voice + 分强度的 signature_patterns + topic_bound_terms + uncertain_patterns + anti_style + edit_preferences）

**交付物**：`task04b_deliverables/STYLE-v3-真实输出与WRITE上下文.json`

**注意**：场景 ① 的隔离数据没有真实文字内容差异（5 条模板文字近似），因此模型输出所有数组为空、字符串为 null —— 这**正是 v3 设计期望的行为**（无证据时不编造）。真实质量需等有真实用户作品时验证。

---

## 5. 未解决 / 需要后续关注的项

| 项 | 说明 |
|---|---|
| **真实质量验证需等真实用户作品** | 本轮验证使用隔离造数，模型的空输出证明「不编造」纪律有效，但不能证明「有真实差异时能正确识别 Voice」。建议等积累了真实编辑记录后再跑一轮 |
| **WRITE 上下文长度** | `_style_write_context()` 目前是确定性投影，如果 signature_patterns 很多可能超出模型的有效注意力。暂未加截断（当前规模下不构成问题） |
| **03 条 `_build_summary()` 的 D5 问题** | `_build_summary()` 丢 7 个键、`if summary…elif profile` 遮蔽问题在 WRITE 侧已修（B2），但 `summary_text` 本身的生成逻辑仍只覆盖 `voice_summary` + 4 个常用列表 + 修改偏好。**是否需要覆盖更多字段属于 04-C 范围** |
| **前端未改** | `StyleProfileOut` 加了 `prompt_version` 字段但前端 `types.ts` 未同步（新增可选字段不影响现有展示）。样式档案 UI 展示 v3 结构化数据属于 UI 工作，不在本轮范围 |

---

## 6. 生产库与发布物

| 对象 | 结果 |
|---|---|
| `data/content_studio.db` | sha256 `b867f12e68afb9e5cdc12fb96610160bf70493eb967679c181a702532d0e05ab`，mtime 仍为 **2026-10-07 19:38** —— **未变** |
| Desktop 库 | mtime 仍为 **2026-10-09 17:03** —— **未写入** |
| Desktop EXE | **未构建、未发布** |

---

## 7. 改动文件清单（sha256 前 16 位）

```
18bb1ca656d6e1e2  backend/app/config.py                （STYLE_PROMPT_VERSION v3）
be5f35b2cfd99e57  backend/app/prompts.py              （STYLE_SYSTEM/HINT v3 + 投影 + B8）
3e21f3b8b68e050c  backend/app/ai/base.py              （temperature 可选，04-A 遗留）
8063634f33ae7b75  backend/app/ai/openai_compat.py     （temperature 可选，04-A 遗留）
f17fd0a2e741dd42  backend/app/services/analyzer.py    （finish_reason 日志，04-A 遗留）
481cb2b087ed1bb3  backend/app/services/batch.py       （移除上限字段，04-A 遗留）
d8c69fe799b67534  backend/app/services/rewriter.py    （usable_style_profile + temperature/max_tokens）
acba76f9b638de71  backend/app/services/rewrite_candidates.py （同上）
bb6c5e0cc6762960  backend/app/services/style_profile.py  （B1/B3/B4/B5/B6/B7 全面改造）
5104ca6e0706e5f7  backend/app/services/write_context.py  （04-A 交付，本轮未改）
ae6dc7f2025e69c6  backend/app/repository.py           （prompt_version 持久化）
38714b6ff234d8e1  backend/app/schemas.py              （StyleProfileOut.prompt_version）
c11cb30c408c38c0  backend/tests/test_regression.py    （新增 04-B 专项 + 改写受影响测试）
```

**新增**：
```
backend/app/services/style_contract.py              （B7 合同校验）
validate_style_v3.py                                （小样本真实验证脚本）
```

**本轮未改动**：`backend/app/services/analysis_spec.py`（ANALYSIS 合同一个字没动）、
`backend/app/services/write_context.py`、`backend/app/services/retriever.py`（上一轮已修）、
全部前端文件、生产库、Desktop 库。
