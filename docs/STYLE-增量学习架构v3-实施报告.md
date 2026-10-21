# STYLE 增量学习架构 v3（A 方案）实施报告

**日期**：2026-10-10
**依据**：《Content Studio V2｜STYLE 增量学习架构设计 v3（A 方案确认版）》
**测试**：`Ran 837 tests / OK`
**生产库**：`b867f12e…` 未变；Desktop 库 mtime 未变；未构建 EXE
**模型**：MiMo thinking 保留，未加任何输出 Token 上限

---

## 1. 做了什么

| 目标 | 实现 |
|---|---|
| ① 前 19 篇积累、第 20 篇首建、第 21 篇起增量；已学同版本不重复 | `style_learning.sync_style_learning()` 状态机 + 幂等键 |
| ② 持久化逐篇状态 / 规律 / 证据关系，支持去重、撤销、修稿、失败恢复 | 新增 3 张台账表 + SAVEPOINT 原子写入 |
| ③ 接通四条导入路径；对标绝不进个人 STYLE | 原帖保持原 ANALYSIS 入口；已分析结果经 `notify_analysis_completed()` 直达 STYLE |
| ④ 复用 ANALYSIS v3.1 / STYLE v3 / WRITE 上下文 | 复用合同校验与 `_style_write_context()`；未重构其他模块 |
| ⑤ 旧档案不冒充新版 | `style_profile.profile_kind` 区分 `legacy_min5` / `initial_v20` / `incremental` |

---

## 2. 数据库迁移（v8 → v9）

`SCHEMA_VERSION` 8 → **9**（表数 16 → 19）。

### 新增表

| 表 | 用途 | 关键约束 |
|---|---|---|
| `style_content_state` | 逐篇 STYLE 学习状态，**与 analysis_state 分开** | `content_id` UNIQUE；state ∈ pending/queued/processing/learned/failed/invalidated；存 `text_hash`、`extractor_version`、`observation_json` |
| `style_patterns` | 规律身份（去重） | `pattern_key` UNIQUE（rule 归一化后哈希）；classification ∈ VOICE/X_REGISTER/TOPIC_BOUND/UNCERTAIN；status ∈ candidate/supported/retired |
| `style_pattern_evidence` | pattern ↔ content 逐字证据 | **UNIQUE(pattern_id, content_id)** —— 同一作品对同一规律只一条证据，`COUNT(DISTINCT content_id)` 天然是支持篇数 |

### 补列

- `style_profile.profile_kind`：`legacy_min5` / `initial_v20` / `incremental`

### 迁移动作

```python
if previous_version < 9:
    conn.execute(
        "UPDATE style_profile SET profile_kind = 'legacy_min5' "
        " WHERE profile_kind IS NULL"
    )
```

**只打标签，不删任何历史**。旧 5 篇门槛档案完整保留，但标记为 legacy → `usable_style_profile()` 不再返回它（不用于 WRITE）。

---

## 3. 学习引擎（`app/services/style_learning.py`，新增）

### 3.1 门槛与语义

| 概念 | 判定 |
|---|---|
| `ANALYSIS completed` | 该帖有合规分析结果（本地或外部导入） |
| `STYLE eligible` | source=mine + style_eligible=1 + analysis_state=analyzed，可计入门槛 |
| `STYLE completed` | `style_content_state.state = learned`（**不等于** ANALYSIS 完成） |
| `STYLE initialized` | 存在 `profile_kind='initial_v20'` 的档案 |

`STYLE_INITIAL_MIN = 20`。`MAX_SAMPLES_PER_REQUEST = 20` **只是单次请求**的抽样上限，不是历史总量上限。

### 3.2 幂等

键 = `content_id + text_hash + analysis 版本 + extractor_version`。
已 learned 且 `text_hash` 未变 → 直接返回 `skipped`，**不调用模型**。

### 3.3 原子性

写库用 `SAVEPOINT style_learn_one`：
- 先 `DELETE` 该篇旧证据 → 插入新证据 → 刷新规律状态 → 标记 learned
- 任何异常 → `ROLLBACK TO SAVEPOINT` + 状态置 `failed`，**绝不留下半套证据**

### 3.4 单篇观察合同

新增 `STYLE_OBSERVE_SYSTEM` + `STYLE_OBSERVE_SCHEMA_HINT`（prompts.py），输出：

```json
{"observations":[{"rule":"...","dimension":"...","classification":"VOICE",
  "applies_when":"...","quote":"逐字引文","evidence_type":"support",
  "match_pattern_id":null}]}
```

校验（`validate_observation`）：classification 枚举、**quote 必须逐字存在于该篇原文**、evidence_type 枚举、`match_pattern_id` 必须在提供的已有规律内。失败 → **一次**带具体路径的纠正重试。

**不把全部台账发给模型**：请求只带本篇原文 + 本篇 ANALYSIS + 若干相关规律摘要。

### 3.5 权重与撤回

- 权重不是 `weight += 1`：基础支持量 = `COUNT(DISTINCT content_id)`，由 UNIQUE 约束保证
- `refresh_pattern_status`：≥2 篇 → `supported`，否则 `candidate`
- 撤销/删除/转对标 → `invalidate_content()` 删除该篇证据、状态置 invalidated、失去全部证据的规律降级 `retired`（保留历史）
- **修稿**：`sync_pool` 发现 `text_hash` 变化 → 撤回旧版本贡献 → 回 pending 重学（新旧不双计）

---

## 4. 四条导入路径接线

| 路径 | 接线位置 | 结果 |
|---|---|---|
| A 我的原始帖子 | **未改动**现有 ANALYSIS 入口（导入仍不自动付费分析） | 分析成功 → `notify_analysis_completed()` → `sync_style_learning()` |
| B 对标原始帖子 | **未改动**现有对标 ANALYSIS 入口 | 进对标流程；source=benchmark 不进个人 STYLE |
| C 我的已分析结果 | `external_analysis.confirm_import()` 末尾已有 `notify_analysis_completed()` | **零次 ANALYSIS** → 直达 STYLE 池 |
| D 对标已分析结果 | 同上 | **零次 ANALYSIS** → 对标资料库，**不计入**个人门槛 |

两条已分析结果路径都走 `persist_analysis_result()` 直接落库，**不经过 analyzer**，因此天然零重复 ANALYSIS。

`jobs._style_update_task()` 已改为调用 `sync_style_learning()`，旧的"每 5 篇重算 + 最多送 20 篇原文 + 追赶循环"主路径**已停用**。

---

## 5. 端到端验收（隔离库，19 → 20 → 21）

脚本：`validate_style_incremental.py`（`--real` 模式第 21 篇走真实 MiMo）

| # | 场景 | 结果 |
|---|---|---|
| ① | 前 19 篇已分析有效作品 | `action=waiting`，`learned=0` ✓ |
| ② | 第 20 篇 → 首次集中建档 | `action=initial`，`learned=20`，patterns=2，**evidence=37 条**，档案 v1 `based_on=20` `kind=initial_v20`，WRITE 可用 ✓ |
| ③ | 第 21 篇（**真实 MiMo**）→ 只增量 | `action=incremental`，`learned=1`，**实际学习 = [21]**（前 20 篇不再送模型）✓ |
| ④ | 重复导入第 21 篇同版本 | `action=noop`，`learned=0` ✓ |
| ⑤ | 对标已分析结果 | 门槛 21 → 21（不计入），`entered_personal_style=False` ✓ |
| ⑥ | 撤销第 21 篇资格 | 残留证据 **0**，状态 `invalidated`，`action=noop`（跌破门槛 → 暂停学习）✓ |

### 第 21 篇真实模型输出（attempt=1，合同首次通过）

```
learn_one: {'content_id': 21, 'status': 'learned', 'observations': 3}
state: {'state': 'learned', 'extractor_version': 'obs-v1'}
- TOPIC_BOUND | 用「做副业第N步：」带编号的冒号短语起手… | 引文:'做副业第21步：'          | authorship: high
- VOICE       | 用句首口语词「其实」引出肯定断言…          | 引文:'其实真正的分水岭是能不能拒绝诱惑。' | authorship: high
- VOICE       | 结尾用「先……，再……」次序句给出可执行顺序…  | 引文:'先把一件事做到稳定产出，再考虑第二条曲线。' | authorship: high
引文逐字不合格: []
```

交付物：`task04b_deliverables/STYLE-增量学习-19-20-21-执行证据.json`

---

## 6. 测试

```
Ran 837 tests in 267s
OK
```

本轮新增 / 改写的测试：

**新增 `TestStyleIncrementalLearning`（7 条）**
- `test_under_20_does_not_start`（1/19 篇不启动）
- `test_twentieth_triggers_initial_build`（第 20 篇首建，学满 20 篇）
- `test_twenty_first_only_learns_the_new_one`（只学第 21 篇）
- `test_repeat_import_does_not_relearn`（幂等）
- `test_benchmark_never_counts`（对标不计入）
- `test_no_concurrent_second_task`（无并发第二任务）

**改写为架构 v3 语义的旧测试**
- `TestStyleStaleness` → 门槛 20；新增 `test_legacy_profile_is_not_usable`（legacy 不用于 WRITE）、`test_new_profile_is_usable`
- `TestStyleEligibility.test_staleness_detects_invalidated_samples` → 失效即需清理
- `TestTask04BTargetedFixes.test_revoke_after_initial_pauses_learning` → 20→19 后暂停学习
- `TestBatchStyleNotification` / `TestStage12DownstreamProfiles` → 改为断言通知到 `sync_style_learning` 与"未满 20 篇不启动"
- 已停用的旧追赶循环测试（`TestStyleCatchup` / `TestStyleCatchupBeyondLimit`）整体替换为上述增量学习测试

**顺带修复**：重建了被误删的 `FakeProvider` 测试基础设施。

---

## 7. 过程中发现并修复的真实缺陷

1. **直接调用 `learn_one` 时状态行缺失** → 证据写入但状态未落库（"有证据无状态"）。已修：状态行不存在时自动建行。
2. **`get_provider` 局部导入导致无法注入** → 测试无法 mock。已改为模块级导入。
3. **门槛被绕过**：建档后撤销导致跌破 20 时，原逻辑仍会"增量学习"。已修：门槛硬性化，`count < 20` 一律只清理不学习。

---

## 8. 改动文件清单与 SHA-256（前 16 位）

| 文件 | 指纹 | 改动 |
|---|---|---|
| `backend/app/db.py` | `61e9dbd418a566fa` | 3 张新表 + `profile_kind` 列 + v9 迁移 |
| `backend/app/repository.py` | `b0a46d1e9b4b1734` | `save_style_profile` / `latest_style_profile` 支持 `profile_kind` |
| `backend/app/jobs.py` | `7fa4cd0ad798045a` | style 任务改走 `sync_style_learning` |
| `backend/app/prompts.py` | `785fc32a56874f89` | 新增单篇观察提示词与合同 |
| `backend/app/services/style_learning.py` | `3208879cbe672b78` | **新增**：增量学习引擎 |
| `backend/app/services/style_profile.py` | `a2a88e515d491e72` | `usable_style_profile` 排除 legacy；staleness 判据 |
| `backend/tests/test_regression.py` | `9e5175a606237b8d` | 新增/改写测试 |
| `validate_style_incremental.py` | `9edd764cc12dd643` | **新增**：端到端验收脚本 |

**未改动（指纹与上一轮一致）**：`style_contract.py` `11317118eb342fa5`、`config.py` `2b9a1f44ea09763c`、`analyzer.py` `3f096521be27c596`

---

## 9. 未解决 / 需 ChatGPT 关注

1. **首次建档的 20 次模型调用**：架构允许"分批提取、统一归并"。当前实现是逐篇调用（20 次）。真实库首次建档会有 20 次调用；若需合并为更少的请求，需要设计批量观察合同（本设计未规定，故未做）。
2. **端到端验收中前 20 篇用 FakeProvider**：真实 20 次调用成本高且易限流。第 21 篇（增量关键路径）用了真实 MiMo 并验证通过；**前 20 篇的真实模型批量观察未实跑**。
3. **`common_openings` 等汇总字段当前为空**：档案由证据台账投影生成时，只填充了 `signature_patterns` / `topic_bound_terms` / `uncertain_patterns` / `evidence` / `voice_summary`。`common_openings`、`common_tones` 等需要从规律按 dimension 归类——本轮未做映射（STYLE v3 合同允许为空，不影响合同校验）。
4. **对标已分析结果的设计留白**：设计文档明确"不擅自规定它必须额外触发新的 AI 画像调用"，因此仅进入既有对标流程，未新增画像调用。


---

## 10. 第二轮独立验收：6 项正确性修复

| # | 问题 | 修复 |
|---|---|---|
| 1 | 首建 20 篇部分失败仍发布 initial_v20 | 首建分支改为**必须全部成功**才发布；部分失败返回 `initial_partial`（不落档案），重试只补失败篇 |
| 2 | 撤销后证据删了但档案没更新、规律没降级 | `invalidate_content` 删除后逐条 `refresh_pattern_status`；`sync_style_learning` 增量分支 evidence_changed 纳入 `pool["invalidated"]/requeued` —— **撤销后即使没有待学新作品也重建档案** |
| 3 | API 异常后状态停 processing | `learn_one` 所有异常路径（API/超时/合同/写库）统一兜底 → 状态 failed + last_error，重试幂等 |
| 4 | 增量档案固定清空 edit_preferences | 恢复 04-B 确定性差异提取；`frequent_*` / `rewrite_pair_count` 由真实差异生成 |
| 5 | 版本幂等不完整 | 幂等键三元组（text_hash + ANALYSIS 版本）；ANALYSIS 版本变化 → 撤回 + 回 pending；提取器升级**不**自动付费回填 |
| 6 | 规律匹配只看最近 20 条 | `_relevant_patterns` 改用 BM25 在全量规律里语义检索，只送 top 20 摘要 |

**新增测试**（`TestStyleV3Round3Fixes`，7 条）：partial_failure 不发布 / invalidate 降级+重建 / api_failure 留 failed / edit_preferences 非空 / 版本变化回 pending / 旧规律仍可检索 / 检索上下文有界。

**测试**：`Ran 844 tests / OK`。
**本轮更新指纹**：`style_learning.py` `4a46e3d9cf083e7f`、`test_regression.py` `e4a6b7eb8b47509d`（其余文件指纹见上表）。

---

## 11. 第三轮验收：2 个失败恢复边界

| # | 边界 | 修复 |
|---|---|---|
| 1 | 证据已保存但档案生成失败 → 下一次 sync 因 pending 为空永久 noop | 引入**持久 dirty 标记**（meta 表 `style_profile_dirty`）：learn_one 成功写证据 / invalidate 撤回证据时标记，build 成功保存后清除。两个 sync 分支的重建判据纳入 dirty —— pending 空时**仅重建档案，绝不重新调用 AI**（首建与增量均覆盖） |
| 2 | get_provider() / 已有规律检索 / 构造提示词等前置处理异常 → 状态停 processing | 这些步骤（标记 processing 之后）全部纳入 learn_one 的异常兜底 → 状态 `failed` + last_error，可恢复 |

**新增测试**（4 条）：
- `test_initial_build_failure_retries_profile_without_relearning`：首建档案生成失败 → 20 篇证据已落库、档案缺失；重试轮用**会炸的 provider** + learn_one 调用计数 → **calls == 0**，档案发布 based_on=20
- `test_incremental_build_failure_retries_profile_without_relearning`：增量同模式 → calls == 0，重建后档案 based_on=21 且 sample_ids 含第 21 篇
- `test_preprocessing_failure_marks_failed_and_recovers`：规律检索异常 → failed；恢复后重试 → learned + 证据落库一致
- `test_get_provider_failure_marks_failed`：provider 初始化异常 → failed + last_error

**测试**：`Ran 848 tests / OK`。
**本轮更新指纹**：`style_learning.py` `d13c5e94b8dcdf80`、`test_regression.py` `6b39deefd2194154`。
**未改动**：`db.py` `61e9dbd418a566fa`、`prompts.py` `785fc32a56874f89`、`style_contract.py` `11317118eb342fa5`、`config.py` `2b9a1f44ea09763c`。
端到端验收脚本重跑全部场景通过。生产库与 Desktop V1 未变；未构建 EXE；未调付费模型。
